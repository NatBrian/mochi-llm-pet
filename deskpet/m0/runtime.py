"""Allow-listed, bounded tool execution over public ports.

This reference runtime consumes structured plans, not shell/Python/HTML text.
Every call and retry is revalidated by the TaskService before any side effect.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from deskpet.contracts import (
    ErrorCode, ProposedPlan, SessionCommand, ToolCall, ToolResult,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ShowArgs(_Args):
    pass


class _RecheckArgs(_Args):
    run_at: datetime

    @field_validator("run_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run_at needs an offset")
        return value.astimezone(timezone.utc)


class _PomodoroArgs(_Args):
    session_id: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=500)
    duration_s: float = Field(default=1500, gt=0, allow_inf_nan=False)

    @field_validator("goal")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("goal is blank")
        return value.strip()


class _ObservationArgs(_Args):
    evidence: list[str] = Field(min_length=1, max_length=20)
    state: str = Field(pattern=r"^(working|available|away|unknown)$")


class _Step(_Args):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class _PlanArgs(_Args):
    steps: list[_Step] = Field(min_length=1, max_length=4)


class ToolRuntime:
    def __init__(self, tasks, sessions, clock, emit, *, max_steps=4, max_replans=1):
        if not 1 <= max_steps <= 4 or not 0 <= max_replans <= 1:
            raise ValueError("M0 supports 1..4 steps and at most one replan")
        self.tasks = tasks
        self.sessions = sessions
        self.clock = clock
        self.emit = emit
        self.max_steps = max_steps
        self.max_replans = max_replans
        self.faults: dict[str, str] = {}
        self.saved_observations: list[dict[str, Any]] = []
        self._cache: dict[str, tuple[str, ToolResult]] = {}
        self._pending: dict[str, str] = {}
        self.registry = {
            "show_reminder": _ShowArgs,
            "schedule_recheck": _RecheckArgs,
            "start_pomodoro": _PomodoroArgs,
            "save_important_observation": _ObservationArgs,
            "execute_plan": _PlanArgs,
        }

    def execute(self, call: ToolCall) -> ToolResult:
        self._emit("tool.requested", call)
        error = self.tasks.validate(call.envelope)
        if error:
            return self._finish(call, self._result(call, "rejected", error))
        if call.tool_name not in self.registry:
            return self._finish(call, self._result(call, "rejected", ErrorCode.UNKNOWN_TOOL))
        try:
            parsed = self.registry[call.tool_name].model_validate(call.args)
        except ValidationError:
            return self._finish(call, self._result(call, "rejected", ErrorCode.INVALID_ARGUMENT))
        fingerprint = json.dumps({"tool_name": call.tool_name, "args": parsed.model_dump(mode="json"),
                                  "envelope": call.envelope.model_dump(mode="json")},
                                 sort_keys=True, ensure_ascii=False)
        if call.idempotency_key in self._cache:
            old_fingerprint, old = self._cache[call.idempotency_key]
            if old_fingerprint != fingerprint:
                return self._finish(call, self._result(call, "rejected", ErrorCode.INVALID_ARGUMENT))
            duplicate = old.model_copy(update={"call_id": call.call_id, "status": "duplicate",
                                               "evidence": {**old.evidence, "original_status": old.status}})
            return self._finish(call, duplicate)
        pending_fingerprint = self._pending.get(call.idempotency_key)
        if pending_fingerprint is not None and pending_fingerprint != fingerprint:
            return self._finish(call, self._result(call, "rejected", ErrorCode.INVALID_ARGUMENT))
        # Faults are deterministic, synchronous outcomes; real latency is not claimed.
        fault = self.faults.get(call.tool_name)
        if fault:
            status, error = ("timeout", ErrorCode.TIMEOUT) if fault == "timeout" else ("failed", ErrorCode.FAILED)
            return self._finish(call, self._result(call, status, error,
                                                  {"fault_injected": fault, "simulated": True}))
        try:
            result = self._dispatch(call, parsed)
        except Exception as exc:
            # The runtime records the failure, but never turns it into a success.
            result = self._result(call, "failed", ErrorCode.FAILED,
                                  {"exception": type(exc).__name__})
        if result.status == "pending":
            self._pending[call.idempotency_key] = fingerprint
        else:
            self._pending.pop(call.idempotency_key, None)
            # Only completed effects are memoized; transient failures can retry.
            if result.status in ("succeeded", "duplicate") and result.error_code is None:
                self._cache[call.idempotency_key] = (fingerprint, result)
        return self._finish(call, result)

    def execute_plan(self, plan: ProposedPlan) -> tuple[ToolResult, ...]:
        """Execute a flat plan; nesting and over-budget plans have no effects."""
        error = self.tasks.accept_decision(plan)
        if error is not None and error != ErrorCode.DUPLICATE:
            return (ToolResult(call_id=plan.envelope.request_id, status="rejected", error_code=error),)
        if len(plan.calls) > self.max_steps:
            return (ToolResult(call_id=plan.envelope.request_id, status="rejected",
                               error_code=ErrorCode.BUDGET_EXCEEDED),)
        if any(call.tool_name == "execute_plan" for call in plan.calls):
            return (ToolResult(call_id=plan.envelope.request_id, status="rejected",
                               error_code=ErrorCode.INVALID_ARGUMENT,
                               evidence={"reason": "plans contain registered leaf actions only"}),)
        # Structural/argument validation before step one prevents malformed tails
        # from partially executing a plan. Domain versions are checked per step.
        for call in plan.calls:
            schema = self.registry.get(call.tool_name)
            if schema is None:
                return (self._finish(call, self._result(call, "rejected", ErrorCode.UNKNOWN_TOOL)),)
            try:
                schema.model_validate(call.args)
            except ValidationError:
                return (self._finish(call, self._result(call, "rejected", ErrorCode.INVALID_ARGUMENT)),)
        results = []
        for call in plan.calls:
            result = self.execute(call)
            results.append(result)
            if result.status in ("pending", "rejected", "failed", "timeout") or result.error_code:
                break
        return tuple(results)

    def run(self, observation, task, planner, envelope) -> tuple[ToolResult, ...]:
        """Bounded result-feedback example: initial plan plus at most one replan."""
        all_results: list[ToolResult] = []
        remaining_steps = self.max_steps
        for round_index in range(self.max_replans + 1):
            if remaining_steps <= 0:
                break
            current_task = self.tasks.get(task.task_id) or task
            round_envelope = envelope if round_index == 0 else envelope.model_copy(
                update={"request_id": f"{envelope.request_id}:replan:{round_index}"})
            plan = planner.plan(observation, current_task, round_envelope, tuple(all_results))
            if not plan.calls:
                break
            if len(plan.calls) > remaining_steps:
                all_results.append(ToolResult(call_id=envelope.request_id, status="rejected",
                                              error_code=ErrorCode.BUDGET_EXCEEDED))
                break
            results = self.execute_plan(plan)
            all_results.extend(results)
            remaining_steps -= len(results)
            self.emit("runtime.feedback", task_id=task.task_id,
                      task_version=envelope.task_version, request_id=envelope.request_id,
                      payload={"round": round_index, "results": [r.model_dump(mode="json") for r in results]})
            if any(r.status == "pending" for r in results):
                break
            if not any(r.status in ("failed", "timeout") for r in results):
                break
        return tuple(all_results)

    def _dispatch(self, call, args) -> ToolResult:
        if call.tool_name == "show_reminder":
            result = self.tasks.request_delivery(call.envelope)
            return result.model_copy(update={"call_id": call.call_id})
        if call.tool_name == "schedule_recheck":
            if args.run_at.tzinfo is None or args.run_at.utcoffset() is None:
                return self._result(call, "rejected", ErrorCode.INVALID_ARGUMENT)
            error = self.tasks.schedule_recheck(call.envelope, args.run_at)
            return self._result(call, "rejected" if error else "succeeded", error)
        if call.tool_name == "start_pomodoro":
            result = self.sessions.apply(SessionCommand(
                command_id=call.idempotency_key, kind="start", session_id=args.session_id,
                payload={"goal": args.goal, "duration_s": args.duration_s,
                         "task_id": call.envelope.task_id}))
            if result.status == "rejected":
                return self._result(call, "rejected", result.error_code)
            return ToolResult(call_id=call.call_id,
                              status="duplicate" if result.status == "duplicate" else "succeeded",
                              state_version=result.view.version if result.view else None,
                              evidence={"session_id": args.session_id})
        if call.tool_name == "save_important_observation":
            record = {"task_id": call.envelope.task_id,
                      "context_revision": call.envelope.context_revision,
                      "recorded_at": self.clock.wall_now().isoformat(),
                      "state": args.state, "evidence": list(args.evidence),
                      "source": "scripted_tool"}
            self.saved_observations.append(record)
            return self._result(call, "succeeded", evidence={"observation_index": len(self.saved_observations)-1})
        if call.tool_name == "execute_plan":
            if len(args.steps) > self.max_steps:
                return self._result(call, "rejected", ErrorCode.BUDGET_EXCEEDED)
            calls = tuple(ToolCall(call_id=f"{call.call_id}:{i}", tool_name=step.tool_name,
                                   args=step.args, envelope=call.envelope,
                                   idempotency_key=f"{call.idempotency_key}:{i}")
                          for i, step in enumerate(args.steps))
            results = self.execute_plan(ProposedPlan(envelope=call.envelope, calls=calls))
            failed = next((r for r in results if r.status not in ("succeeded", "duplicate") or r.error_code), None)
            return ToolResult(call_id=call.call_id,
                              status=failed.status if failed else "succeeded",
                              error_code=failed.error_code if failed else None,
                              receipt_id=failed.receipt_id if failed else None,
                              evidence={"steps": [r.model_dump(mode="json") for r in results]})
        return self._result(call, "rejected", ErrorCode.UNKNOWN_TOOL)

    @staticmethod
    def _result(call, status, error=None, evidence=None):
        return ToolResult(call_id=call.call_id, status=status, error_code=error,
                          state_version=call.envelope.task_version, evidence=evidence or {})

    def _emit(self, kind, call, payload=None):
        self.emit(kind, task_id=call.envelope.task_id, task_version=call.envelope.task_version,
                  request_id=call.envelope.request_id, call_id=call.call_id,
                  payload=payload or {"tool_name": call.tool_name})

    def _finish(self, call, result):
        self._emit("tool.result", call, {"tool_name": call.tool_name, **result.model_dump(mode="json")})
        return result
