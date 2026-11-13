"""Single-owner in-memory reference for the M0 TaskService contract.

Construct and call this service on one domain thread. It has no Qt, OS, network,
SQLite, or hidden clock dependencies. ``pump`` consumes the injected clock; GUI
clients reach it through the queued gateway. B can replace this adapter with a
transactional repository while retaining the public ports and contract suite.

Persistence/restart recovery is deliberately not implemented by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from typing import Callable

from pydantic import ValidationError

from deskpet.contracts import (
    Clock, CommandResult, DecisionEnvelope, DeliveryPort, DisplayCommand,
    DisplayReceipt, ErrorCode, Observation, ProposedPlan, TaskCommand, TaskDraft,
    TaskView, ToolResult,
)


@dataclass
class _Attempt:
    command: DisplayCommand
    last_attempt_mono: float
    status: str = "pending"
    attempts: int = 0
    receipt: DisplayReceipt | None = None


class MemoryTaskService:
    """Reference semantics, including CAS, cancellation and receipt-driven delivery.

    ``emit`` accepts a TraceRecorder-compatible ``emit(kind, **fields)`` callable.
    Missing acknowledgements time out after ``receipt_timeout_s``; an explicit
    failure is retried no sooner than ``retry_interval_s``. Every retry reuses the
    delivery ID, so an idempotent GUI reuses one card rather than displaying twice.
    """

    def __init__(
        self, clock: Clock, delivery: DeliveryPort, emit: Callable,
        *, receipt_timeout_s: float = 5.0, retry_interval_s: float = 1.0,
    ):
        if (not math.isfinite(receipt_timeout_s) or receipt_timeout_s <= 0
                or not math.isfinite(retry_interval_s) or retry_interval_s <= 0):
            raise ValueError("delivery timeout and retry interval must be finite and positive")
        self.clock = clock
        self.delivery = delivery
        self.emit = emit
        self.receipt_timeout_s = receipt_timeout_s
        self.retry_interval_s = retry_interval_s
        self._tasks: dict[str, TaskView] = {}
        self._commands: dict[str, tuple[str, CommandResult]] = {}
        self._observation: Observation | None = None
        self._decisions: dict[str, str] = {}
        self._rechecks: dict[str, tuple[int, datetime, str]] = {}
        self._attempts: dict[str, _Attempt] = {}
        self._active_delivery: dict[str, str] = {}
        self._last_deferred: dict[str, tuple[int, int, str]] = {}

    def get(self, task_id: str) -> TaskView | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskView]:
        return [self._tasks[key] for key in sorted(self._tasks)]

    @staticmethod
    def _fingerprint(model) -> str:
        return json.dumps(model.model_dump(mode="json"), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)

    def _command_rejection(
        self, command: TaskCommand, code: ErrorCode, message: str,
    ) -> CommandResult:
        view = self.get(command.task_id)
        self.emit("command.rejected", task_id=command.task_id,
                  task_version=view.version if view else None,
                  payload={"command_id": command.command_id, "kind": command.kind,
                           "error_code": code.value, "message": message})
        return CommandResult(command_id=command.command_id, status="rejected",
                             error_code=code, actual_version=view.version if view else None,
                             view=view, message=message)

    def apply(self, command: TaskCommand) -> CommandResult:
        """Commit one versioned command. Retries replay its original result.

        A command ID is bound to its original complete input and terminal result,
        including rejection. Reusing it with another payload is invalid. A client
        retries a changed operation with a new ID and current expected_version.
        """
        fingerprint = self._fingerprint(command)
        previous = self._commands.get(command.command_id)
        if previous:
            if fingerprint != previous[0]:
                return self._command_rejection(
                    command, ErrorCode.INVALID_ARGUMENT, "command_id was reused with different input")
            original = previous[1]
            self.emit("command.duplicate", task_id=command.task_id,
                      task_version=original.actual_version,
                      payload={"command_id": command.command_id,
                               "original_status": original.status})
            return original.model_copy(update={
                "status": "duplicate", "error_code": ErrorCode.DUPLICATE,
                "message": f"replayed original {original.status} result",
            })

        result = self._apply_once(command)
        self._commands[command.command_id] = (fingerprint, result)
        return result

    def _apply_once(self, command: TaskCommand) -> CommandResult:
        reject = lambda code, message: self._command_rejection(command, code, message)
        if not command.command_id.strip() or not command.task_id.strip():
            return reject(ErrorCode.INVALID_ARGUMENT, "command_id and task_id must be nonempty")
        view = self.get(command.task_id)
        if command.kind == "create":
            if view is not None or command.expected_version != 0:
                return reject(ErrorCode.STALE_TASK, "create requires absent task and expected_version=0")
        else:
            if view is None:
                return reject(ErrorCode.NOT_FOUND, "task does not exist")
            if command.expected_version != view.version:
                return reject(ErrorCode.STALE_TASK, "expected_version does not match task")
            if view.status == "cancelled":
                return reject(ErrorCode.CANCELLED, "task was cancelled")
            if view.status == "completed":
                return reject(ErrorCode.INVALID_TRANSITION, "completed task is terminal")

        expected_fields = {"draft"} if command.kind in {"create", "edit"} else (
            {"seconds"} if command.kind == "snooze" else set())
        if set(command.payload) != expected_fields:
            return reject(ErrorCode.INVALID_ARGUMENT,
                          f"{command.kind} payload requires exactly {sorted(expected_fields)}")
        try:
            if command.kind in {"create", "edit"}:
                draft = TaskDraft.model_validate(command.payload["draft"])
            elif command.kind == "snooze":
                seconds = command.payload["seconds"]
                if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                    raise ValueError("seconds must be numeric")
                if not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError("seconds must be finite and positive")
        except (ValidationError, ValueError, TypeError) as exc:
            return reject(ErrorCode.INVALID_ARGUMENT, str(exc))

        now = self.clock.wall_now()
        if command.kind == "create":
            updated = TaskView(task_id=command.task_id, version=1, draft=draft)
        elif command.kind == "confirm":
            if view.status != "draft":
                return reject(ErrorCode.INVALID_TRANSITION, "only a draft can be confirmed")
            updated = view.model_copy(update={"status": "registered", "version": view.version+1,
                                               "confirmed_at": now})
        elif command.kind == "edit":
            updated = view.model_copy(update={"draft": draft, "version": view.version+1,
                                               "displayed_at": None,
                                               "occurrence": view.occurrence + (view.status == "registered")})
        elif command.kind == "snooze":
            if view.status != "registered":
                return reject(ErrorCode.INVALID_TRANSITION, "only a registered task can be snoozed")
            width = view.draft.latest_first_delivery_at - view.draft.earliest_at
            try:
                earliest = now + timedelta(seconds=seconds)
                latest = earliest + width
            except OverflowError:
                return reject(ErrorCode.INVALID_ARGUMENT, "snooze exceeds supported datetime range")
            draft = view.draft.model_copy(update={"earliest_at": earliest,
                                                   "latest_first_delivery_at": latest})
            updated = view.model_copy(update={"draft": draft, "version": view.version+1,
                                               "occurrence": view.occurrence+1, "displayed_at": None})
        elif command.kind == "cancel":
            updated = view.model_copy(update={"status": "cancelled", "version": view.version+1})
        elif command.kind == "complete":
            if view.status != "registered":
                return reject(ErrorCode.INVALID_TRANSITION, "only a registered task can be completed")
            updated = view.model_copy(update={"status": "completed", "version": view.version+1})
        else:  # Defensive for non-validated callers; normal TaskCommand rejects unknown kinds.
            return reject(ErrorCode.INVALID_ARGUMENT, "unknown command")

        self._tasks[command.task_id] = updated
        if command.kind in {"edit", "snooze", "cancel", "complete"}:
            self._invalidate_pending(command.task_id)
        kind = {"create": "created", "confirm": "confirmed", "edit": "edited",
                "snooze": "snoozed", "cancel": "cancelled", "complete": "completed"}[command.kind]
        self.emit(f"task.{kind}", task_id=updated.task_id, task_version=updated.version,
                  payload={"command_id": command.command_id, "status": updated.status,
                           "occurrence": updated.occurrence})
        return CommandResult(command_id=command.command_id, status="applied",
                             actual_version=updated.version, view=updated)

    def _invalidate_pending(self, task_id: str) -> None:
        self._active_delivery.pop(task_id, None)
        self._rechecks.pop(task_id, None)
        self._last_deferred.pop(task_id, None)
        try:
            self.delivery.dismiss(task_id)
        except Exception as exc:  # State is already committed; rendering cannot roll it back.
            self.emit("delivery.dismiss_failed", task_id=task_id,
                      task_version=self._tasks[task_id].version,
                      payload={"error": type(exc).__name__})

    def observe(self, observation: Observation) -> None:
        if self._observation and observation.context_revision < self._observation.context_revision:
            self.emit("observation.rejected", payload={"error_code": ErrorCode.STALE_CONTEXT.value,
                                                       "context_revision": observation.context_revision})
            return
        previous = self._observation
        self._observation = observation
        # Keep the newest sampling time/TTL even if the semantic state is
        # unchanged. Polling is not a new experimental observation event.
        def trace_key(value):
            return (value.context_revision, value.estimate.state,
                    value.estimate.evidence, value.estimate.source,
                    value.source, value.capabilities, value.application)
        if previous is not None and trace_key(previous) == trace_key(observation):
            return
        self.emit("observation.updated", source=observation.source,
                  simulated=observation.source != "live",
                  payload={"context_revision": observation.context_revision,
                           "state": observation.estimate.state,
                           "estimate_source": observation.estimate.source})

    def validate(self, envelope: DecisionEnvelope) -> ErrorCode | None:
        view = self.get(envelope.task_id)
        if view is None:
            return ErrorCode.NOT_FOUND
        if view.status == "cancelled":
            return ErrorCode.CANCELLED
        if envelope.task_version != view.version:
            return ErrorCode.STALE_TASK
        if view.status != "registered":
            return ErrorCode.INVALID_TRANSITION
        now = self.clock.wall_now()
        if now >= envelope.expires_at:
            return ErrorCode.EXPIRED_REQUEST
        if envelope.created_at > now:
            return ErrorCode.INVALID_ARGUMENT
        # Evidence can expire without any subsequent observation arriving. Do
        # not let a still-valid request keep relying on expired user/rule state.
        if self._observation is not None:
            until = self._observation.estimate.valid_until
            if (self._observation.observed_at > now
                    or (until is not None and until <= now)):
                return ErrorCode.STALE_CONTEXT
        current_revision = self._observation.context_revision if self._observation else 1
        if envelope.context_revision != current_revision:
            return ErrorCode.STALE_CONTEXT
        return None

    def accept_decision(self, plan: ProposedPlan) -> ErrorCode | None:
        error = self.validate(plan.envelope)
        fingerprint = self._fingerprint(plan)
        previous = self._decisions.get(plan.envelope.request_id)
        if error is None and previous:
            error = ErrorCode.DUPLICATE if previous == fingerprint else ErrorCode.INVALID_ARGUMENT
        if error is None:
            self._decisions[plan.envelope.request_id] = fingerprint
        self.emit("decision.accepted" if error is None else "decision.rejected",
                  task_id=plan.envelope.task_id, task_version=plan.envelope.task_version,
                  request_id=plan.envelope.request_id,
                  payload={"error_code": error.value if error else None})
        return error

    def schedule_recheck(self, envelope: DecisionEnvelope, run_at: datetime) -> ErrorCode | None:
        error = self.validate(envelope)
        if error is not None:
            return error
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            return ErrorCode.INVALID_ARGUMENT
        view = self._tasks[envelope.task_id]
        if not self.clock.wall_now() <= run_at <= view.draft.latest_first_delivery_at:
            return ErrorCode.OUTSIDE_WINDOW
        self._rechecks[view.task_id] = (view.version, run_at, envelope.request_id)
        self.emit("recheck.scheduled", task_id=view.task_id, task_version=view.version,
                  request_id=envelope.request_id, payload={"run_at": run_at.isoformat()})
        return None

    def request_delivery(self, envelope: DecisionEnvelope) -> ToolResult:
        # Revalidate authority even when fetching a previously accepted call's
        # outcome: cancellation/version/context/expiry still take precedence.
        error = self.validate(envelope)
        view = self.get(envelope.task_id)
        if error is not None:
            return ToolResult(call_id=envelope.request_id, status="rejected", error_code=error,
                              state_version=view.version if view else None)
        existing = self._active_delivery.get(view.task_id)
        if existing:
            attempt = self._attempts[existing]
            if (attempt.command.task_version == view.version
                    and attempt.command.occurrence == view.occurrence):
                # A window bounds creation of a new effect, not retrieval of a
                # real receipt. GUI acknowledgement may legitimately arrive
                # after the earliest/latest edge (especially a hard due time).
                return self._delivery_result(envelope, view, attempt)
        now = self.clock.wall_now()
        if not (view.draft.earliest_at <= now <= view.draft.latest_first_delivery_at):
            return ToolResult(call_id=envelope.request_id, status="rejected",
                              error_code=ErrorCode.OUTSIDE_WINDOW, state_version=view.version)
        return self._delivery_result(envelope, view, self._ensure_delivery(view, "tool"))

    @staticmethod
    def _delivery_result(envelope: DecisionEnvelope, view: TaskView, attempt: _Attempt) -> ToolResult:
        result_status = "succeeded" if attempt.status == "displayed" else (
            "failed" if attempt.status == "failed" else "pending")
        return ToolResult(call_id=envelope.request_id, status=result_status,
                          receipt_id=attempt.command.delivery_id if result_status == "succeeded" else None,
                          state_version=view.version,
                          error_code=(attempt.receipt.error_code if attempt.receipt else None),
                          evidence={"delivery_id": attempt.command.delivery_id,
                                    "receipt_source": attempt.receipt.source if attempt.receipt else None})

    def _ensure_delivery(self, view: TaskView, reason: str) -> _Attempt:
        existing = self._active_delivery.get(view.task_id)
        if existing:
            return self._attempts[existing]
        now = self.clock.wall_now()
        delivery_id = f"delivery:{view.task_id}:{view.occurrence}:v{view.version}"
        command = DisplayCommand(delivery_id=delivery_id, task_id=view.task_id,
                                 task_version=view.version, occurrence=view.occurrence,
                                 content=view.draft.content, requested_at=now,
                                 expires_at=now + timedelta(seconds=max(30.0, self.receipt_timeout_s*2)),
                                 reason=reason)
        attempt = _Attempt(command=command, last_attempt_mono=self.clock.monotonic_now())
        self._attempts[delivery_id] = attempt
        self._active_delivery[view.task_id] = delivery_id
        self._dispatch(attempt)
        return attempt

    def _dispatch(self, attempt: _Attempt) -> None:
        now = self.clock.wall_now()
        command = attempt.command
        # Renew an expired execution lease without inventing a new delivery.
        if now >= command.expires_at:
            command = command.model_copy(update={
                "expires_at": now + timedelta(seconds=max(30.0, self.receipt_timeout_s*2)),
            })
            attempt.command = command
        attempt.status = "pending"
        attempt.attempts += 1
        attempt.last_attempt_mono = self.clock.monotonic_now()
        self.emit("delivery.requested", task_id=command.task_id, task_version=command.task_version,
                  delivery_id=command.delivery_id,
                  payload={"reason": command.reason, "attempt": attempt.attempts,
                           "occurrence": command.occurrence})
        try:
            receipt = self.delivery.show(command)
        except Exception as exc:
            attempt.status = "failed"
            self.emit("delivery.failed", task_id=command.task_id, task_version=command.task_version,
                      delivery_id=command.delivery_id,
                      payload={"error_code": ErrorCode.FAILED.value, "error": type(exc).__name__})
            return
        if receipt is not None:
            try:
                receipt = DisplayReceipt.model_validate(receipt)
            except (ValidationError, ValueError, TypeError):
                attempt.status = "failed"
                self.emit("delivery.failed", task_id=command.task_id,
                          task_version=command.task_version, delivery_id=command.delivery_id,
                          payload={"error_code": ErrorCode.INVALID_ARGUMENT.value,
                                   "error": "invalid display receipt"})
            else:
                self.record_receipt(receipt)

    def record_receipt(self, receipt: DisplayReceipt) -> ErrorCode | None:
        attempt = self._attempts.get(receipt.delivery_id)
        view = self.get(receipt.task_id)
        error = None
        if attempt is None or view is None:
            error = ErrorCode.NOT_FOUND
        elif view.status == "cancelled":
            error = ErrorCode.CANCELLED
        elif (receipt.task_id != attempt.command.task_id
              or receipt.task_version != attempt.command.task_version
              or receipt.task_version != view.version):
            error = ErrorCode.STALE_TASK
        elif view.status != "registered":
            error = ErrorCode.INVALID_TRANSITION
        elif (receipt.rendered_at < attempt.command.requested_at
              or receipt.rendered_at > self.clock.wall_now()
              or receipt.rendered_at > attempt.command.expires_at):
            error = ErrorCode.INVALID_ARGUMENT
        elif attempt.status == "displayed":
            error = ErrorCode.DUPLICATE
        if error is not None:
            self.emit("delivery.duplicate" if error == ErrorCode.DUPLICATE else "delivery.rejected",
                      task_id=receipt.task_id, task_version=receipt.task_version,
                      delivery_id=receipt.delivery_id, source=receipt.source,
                      simulated=receipt.source != "native",
                      payload={"error_code": error.value, "outcome": receipt.outcome})
            return error

        attempt.receipt = receipt
        attempt.status = "displayed" if receipt.outcome == "displayed" else "failed"
        if receipt.outcome == "displayed":
            self._tasks[view.task_id] = view.model_copy(update={"displayed_at": receipt.rendered_at})
            self._rechecks.pop(view.task_id, None)
        self.emit("delivery.displayed" if receipt.outcome == "displayed" else "delivery.failed",
                  task_id=receipt.task_id, task_version=receipt.task_version,
                  delivery_id=receipt.delivery_id, source=receipt.source,
                  simulated=receipt.source != "native",
                  payload={"outcome": receipt.outcome,
                           "error_code": receipt.error_code.value if receipt.error_code else None,
                           "late_by_s": max(0.0, (receipt.rendered_at - view.draft.latest_first_delivery_at).total_seconds())})
        return None

    def _effective_state(self) -> str:
        obs = self._observation
        now = self.clock.wall_now()
        if obs is None or obs.observed_at > now:
            return "unknown"
        until = obs.estimate.valid_until
        if until is not None and until <= now:
            return "unknown"
        return obs.estimate.state

    def _available(self) -> bool:
        return self._effective_state() == "available"

    def pump(self) -> None:
        """Process due domain work once, in stable order, without sleeping.

        Command-vs-deadline races are decided by caller submission order: the
        replay runner orders equal-time inputs by seq and explicitly pumps; Qt
        serializes its queued commands and timer ticks on the service thread.
        """
        now = self.clock.wall_now()
        mono = self.clock.monotonic_now()
        for task_id, (version, run_at, request_id) in sorted(
            list(self._rechecks.items()), key=lambda item: (item[1][1], item[0])
        ):
            if run_at <= now:
                self._rechecks.pop(task_id, None)
                view = self.get(task_id)
                if view and view.status == "registered" and view.version == version:
                    self.emit("recheck.fired", task_id=task_id, task_version=version,
                              request_id=request_id, payload={"run_at": run_at.isoformat()})
        for view in sorted(self._tasks.values(), key=lambda v: (v.draft.earliest_at, v.task_id)):
            if view.status != "registered" or view.displayed_at is not None:
                continue
            existing = self._active_delivery.get(view.task_id)
            if existing:
                attempt = self._attempts[existing]
                elapsed = mono - attempt.last_attempt_mono
                if attempt.status == "pending" and elapsed >= self.receipt_timeout_s:
                    self.emit("delivery.timeout", task_id=view.task_id, task_version=view.version,
                              delivery_id=existing,
                              payload={"error_code": ErrorCode.TIMEOUT.value,
                                       "attempt": attempt.attempts})
                    self._dispatch(attempt)
                elif attempt.status == "failed" and elapsed >= self.retry_interval_s:
                    self._dispatch(attempt)
                continue
            if now < view.draft.earliest_at:
                continue
            if view.draft.mode == "hard":
                self._ensure_delivery(view, "hard_due")
            elif now >= view.draft.latest_first_delivery_at:
                self._ensure_delivery(view, "deadline_fallback")
            elif self._available():
                self._ensure_delivery(view, "opportunity")
            else:
                context = self._observation.context_revision if self._observation else 1
                state = self._effective_state()
                key = (view.version, context, state)
                if self._last_deferred.get(view.task_id) != key:
                    self._last_deferred[view.task_id] = key
                    self.emit("delivery.deferred", task_id=view.task_id, task_version=view.version,
                              payload={"state": state})


FakeTaskService = MemoryTaskService
