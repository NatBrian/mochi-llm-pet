"""Behavioral TaskService contract suite: reusable with another service factory.

Set ``M0_ADAPTERS=adapters.json`` to run these assertions against B's adapter.
The JSON uses the same ``task_service: module:factory`` registration as the CLI.
Keep these assertions unchanged; they specify facts, not implementation internals.
"""
from datetime import timedelta
from importlib import import_module
import os

import pytest

from deskpet.contracts import (
    DecisionEnvelope, DisplayReceipt, ErrorCode, Observation, ProposedPlan,
    TaskCommand, TaskDraft, UserStateEstimate,
)
from deskpet.m0.trace import TraceRecorder
from deskpet.mocks.clock import FakeClock
from deskpet.mocks.task_service import MemoryTaskService


class DisplayProbe:
    def __init__(self, clock):
        self.clock = clock
        self.commands = []
        self.dismissed = []
        self.mode = "displayed"
        self.receipts = {}

    def show(self, command):
        self.commands.append(command)
        if command.delivery_id in self.receipts:
            return self.receipts[command.delivery_id]
        if self.mode == "missing":
            return None
        if self.mode == "exception":
            raise RuntimeError("injected display failure")
        result = DisplayReceipt(
            delivery_id=command.delivery_id, task_id=command.task_id,
            task_version=command.task_version, rendered_at=self.clock.wall_now(),
            outcome="displayed" if self.mode == "displayed" else "failed",
            source="simulated", error_code=None if self.mode == "displayed" else ErrorCode.FAILED,
        )
        if result.outcome == "displayed":
            self.receipts[command.delivery_id] = result
        return result

    def dismiss(self, task_id):
        self.dismissed.append(task_id)


@pytest.fixture
def task_service_factory():
    configuration = os.environ.get("M0_ADAPTERS")
    if configuration:
        from deskpet.m0.composition import load_adapters
        adapter = load_adapters(configuration).get("task_service")
        if adapter:
            module, symbol = adapter.split(":")
            return getattr(import_module(module), symbol)
    return MemoryTaskService


@pytest.fixture
def system(task_service_factory):
    clock = FakeClock()
    display = DisplayProbe(clock)
    trace = TraceRecorder(clock)
    service = task_service_factory(clock=clock, delivery=display, emit=trace.emit)
    return service, clock, display, trace


def command(service, kind, command_id=None, task_id="milk", **payload):
    current = service.get(task_id)
    version = current.version if current else 0
    return service.apply(TaskCommand(command_id=command_id or f"{task_id}-{kind}-{version}",
                                     kind=kind, task_id=task_id, expected_version=version,
                                     payload=payload))


def register(service, clock, *, mode="soft", due=10, width=20, task_id="milk"):
    earliest = clock.wall_now() + timedelta(seconds=due)
    draft = TaskDraft(content="Buy milk", mode=mode, earliest_at=earliest,
                      latest_first_delivery_at=earliest + timedelta(seconds=width if mode == "soft" else 0))
    assert command(service, "create", task_id=task_id,
                   draft=draft.model_dump(mode="json")).status == "applied"
    result = command(service, "confirm", task_id=task_id)
    assert result.status == "applied"
    return result.view


def observe(service, clock, state="working", revision=1, valid_until=None):
    service.observe(Observation(observed_at=clock.wall_now(), context_revision=revision,
                                estimate=UserStateEstimate(state=state, valid_until=valid_until)))


def envelope(service, clock, task_id="milk", **updates):
    view = service.get(task_id)
    data = dict(request_id="request-1", task_id=task_id, task_version=view.version,
                context_revision=1, created_at=clock.wall_now(),
                expires_at=clock.wall_now()+timedelta(seconds=100))
    data.update(updates)
    return DecisionEnvelope(**data)


def kinds(trace):
    return [event.kind for event in trace.events]


def test_only_confirmed_tasks_can_be_scheduled(system):
    service, clock, display, trace = system
    now = clock.wall_now()
    draft = TaskDraft(content="draft", earliest_at=now, latest_first_delivery_at=now)
    command(service, "create", draft=draft.model_dump(mode="json"))
    clock.advance(100)
    service.pump()
    assert display.commands == []
    assert service.get("milk").status == "draft"


def test_hard_reminder_ignores_busy_state_and_receipt_does_not_complete(system):
    service, clock, display, trace = system
    view = register(service, clock, mode="hard")
    observe(service, clock, "working")
    service.pump()
    assert not display.commands
    clock.advance(10)
    service.pump()
    assert len(display.commands) == 1
    assert display.commands[0].reason == "hard_due"
    actual = service.get("milk")
    assert actual.status == "registered"
    assert actual.version == view.version
    assert actual.displayed_at == clock.wall_now()
    assert "delivery.displayed" in kinds(trace)
    receipt_event = next(e for e in trace.events if e.kind == "delivery.displayed")
    assert receipt_event.simulated and receipt_event.source == "simulated"
    command(service, "complete")
    assert service.get("milk").status == "completed"


def test_soft_window_rechecks_then_available_opportunity(system):
    service, clock, display, trace = system
    register(service, clock)
    observe(service, clock, "working")
    assert service.schedule_recheck(envelope(service, clock), clock.wall_now()+timedelta(seconds=15)) is None
    clock.advance(10)
    service.pump()
    assert display.commands == []
    assert "delivery.deferred" in kinds(trace)
    clock.advance(5)
    observe(service, clock, "available", revision=2)
    service.pump()
    assert "recheck.fired" in kinds(trace)
    assert len(display.commands) == 1
    assert display.commands[0].reason == "opportunity"


@pytest.mark.parametrize("state", ["working", "away", "unknown"])
def test_soft_deadline_fallback_does_not_need_model_or_opportunity(system, state):
    service, clock, display, trace = system
    register(service, clock)
    observe(service, clock, state)
    clock.advance(30)
    service.pump()
    assert len(display.commands) == 1
    assert display.commands[0].reason == "deadline_fallback"


def test_expired_available_estimate_behaves_as_unknown(system):
    service, clock, display, trace = system
    register(service, clock)
    observe(service, clock, "available", valid_until=clock.wall_now()+timedelta(seconds=5))
    clock.advance(10)
    service.pump()
    assert not display.commands
    clock.advance(20)
    service.pump()
    assert display.commands[0].reason == "deadline_fallback"


def test_confirm_and_commands_use_compare_and_swap(system):
    service, clock, display, trace = system
    register(service, clock)
    result = service.apply(TaskCommand(command_id="old-complete", task_id="milk",
                                       expected_version=1, kind="complete"))
    assert result.status == "rejected"
    assert result.error_code == ErrorCode.STALE_TASK
    assert service.get("milk").version == 2
    result = command(service, "confirm", command_id="confirm-again")
    assert result.error_code == ErrorCode.INVALID_TRANSITION


def test_duplicate_command_preserves_one_effect_and_reused_id_requires_same_input(system):
    service, clock, display, trace = system
    register(service, clock)
    cancel = TaskCommand(command_id="cancel-once", kind="cancel", task_id="milk", expected_version=2)
    first = service.apply(cancel)
    duplicate = service.apply(cancel)
    assert first.status == "applied" and duplicate.status == "duplicate"
    assert duplicate.error_code == ErrorCode.DUPLICATE
    assert service.get("milk").version == 3
    assert kinds(trace).count("task.cancelled") == 1
    wrong = service.apply(cancel.model_copy(update={"kind": "complete"}))
    assert wrong.error_code == ErrorCode.INVALID_ARGUMENT
    assert service.get("milk").status == "cancelled"


def test_cancel_rejects_inflight_decision_and_receipt_and_suppresses_due(system):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, mode="hard", due=0)
    old_envelope = envelope(service, clock)
    service.pump()
    issued = display.commands[0]
    assert service.get("milk").displayed_at is None
    assert command(service, "cancel").status == "applied"
    assert "milk" in display.dismissed
    assert service.validate(old_envelope) == ErrorCode.CANCELLED
    assert service.accept_decision(ProposedPlan(envelope=old_envelope)) == ErrorCode.CANCELLED
    receipt = DisplayReceipt(delivery_id=issued.delivery_id, task_id="milk",
                             task_version=issued.task_version, rendered_at=clock.wall_now(),
                             outcome="displayed", source="native")
    assert service.record_receipt(receipt) == ErrorCode.CANCELLED
    clock.advance(100)
    service.pump()
    assert len(display.commands) == 1
    assert service.get("milk").displayed_at is None


def test_edit_invalidates_old_result_and_old_job(system):
    service, clock, display, trace = system
    before = register(service, clock, mode="hard", due=10)
    old = envelope(service, clock)
    new_draft = before.draft.model_copy(update={
        "earliest_at": clock.wall_now()+timedelta(seconds=50),
        "latest_first_delivery_at": clock.wall_now()+timedelta(seconds=50),
    })
    after = command(service, "edit", draft=new_draft.model_dump(mode="json")).view
    assert after.version == before.version+1 and after.status == "registered"
    assert service.validate(old) == ErrorCode.STALE_TASK
    clock.advance(10)
    service.pump()
    assert not display.commands
    clock.advance(40)
    service.pump()
    assert len(display.commands) == 1 and display.commands[0].task_version == after.version


def test_snooze_creates_new_occurrence_and_invalidates_old_receipt(system):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, mode="hard", due=0)
    service.pump()
    old = display.commands[0]
    result = command(service, "snooze", seconds=20)
    assert result.view.occurrence == 2 and result.view.version == 3
    assert service.record_receipt(DisplayReceipt(
        delivery_id=old.delivery_id, task_id="milk", task_version=2,
        rendered_at=clock.wall_now(), outcome="displayed", source="simulated",
    )) == ErrorCode.STALE_TASK
    clock.advance(19)
    service.pump()
    assert len(display.commands) == 1
    display.mode = "displayed"
    clock.advance(1)
    service.pump()
    assert len(display.commands) == 2
    assert display.commands[1].delivery_id != old.delivery_id
    assert service.get("milk").status == "registered"


def test_snooze_preserves_soft_window_width(system):
    service, clock, display, trace = system
    register(service, clock, due=10, width=20)
    after = command(service, "snooze", seconds=5).view
    assert after.draft.earliest_at == clock.wall_now()+timedelta(seconds=5)
    assert (after.draft.latest_first_delivery_at-after.draft.earliest_at).total_seconds() == 20


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), 1e307, True, "5"])
def test_bad_snooze_has_no_side_effects(system, seconds):
    service, clock, display, trace = system
    register(service, clock)
    result = command(service, "snooze", seconds=seconds)
    assert result.error_code == ErrorCode.INVALID_ARGUMENT
    assert service.get("milk").version == 2


def test_invalid_payload_and_invalid_draft_are_rejected(system):
    service, clock, display, trace = system
    result = command(service, "create", command_id="bad-draft", draft={"content": "missing time"})
    assert result.error_code == ErrorCode.INVALID_ARGUMENT
    assert service.get("milk") is None
    register(service, clock)
    result = command(service, "cancel", unexpected=True)
    assert result.error_code == ErrorCode.INVALID_ARGUMENT
    assert service.get("milk").status == "registered"


def test_pending_receipt_times_out_then_same_delivery_is_retried(system):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, mode="hard", due=0)
    service.pump()
    assert len(display.commands) == 1
    assert service.get("milk").displayed_at is None
    service.pump()
    clock.advance(4)
    service.pump()
    assert len(display.commands) == 1
    display.mode = "displayed"
    clock.advance(1)
    service.pump()
    assert len(display.commands) == 2
    assert display.commands[0].delivery_id == display.commands[1].delivery_id
    assert kinds(trace).count("delivery.timeout") == 1
    assert service.get("milk").displayed_at is not None
    clock.advance(100)
    service.pump()
    assert len(display.commands) == 2


@pytest.mark.parametrize("mode", ["failed", "exception"])
def test_delivery_failure_is_observable_and_retry_is_throttled(system, mode):
    service, clock, display, trace = system
    display.mode = mode
    register(service, clock, mode="hard", due=0)
    service.pump()
    assert "delivery.failed" in kinds(trace)
    assert service.get("milk").displayed_at is None
    service.pump()
    assert len(display.commands) == 1
    clock.advance(1)
    display.mode = "displayed"
    service.pump()
    assert len(display.commands) == 2
    assert display.commands[0].delivery_id == display.commands[1].delivery_id


def test_duplicate_and_malformed_receipt_cannot_rewrite_display_fact(system):
    service, clock, display, trace = system
    register(service, clock, mode="hard", due=0)
    service.pump()
    receipt = next(iter(display.receipts.values()))
    assert service.record_receipt(receipt) == ErrorCode.DUPLICATE
    assert kinds(trace).count("delivery.displayed") == 1
    assert service.record_receipt(receipt.model_copy(update={"delivery_id": "unknown"})) == ErrorCode.NOT_FOUND
    assert service.record_receipt(receipt.model_copy(update={"task_version": 100})) == ErrorCode.STALE_TASK


def test_receipt_cannot_claim_future_or_pre_request_rendering(system):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, mode="hard", due=0)
    service.pump()
    issued = display.commands[0]
    receipt = DisplayReceipt(delivery_id=issued.delivery_id, task_id="milk", task_version=2,
                             rendered_at=clock.wall_now()+timedelta(seconds=1),
                             outcome="displayed", source="native")
    assert service.record_receipt(receipt) == ErrorCode.INVALID_ARGUMENT
    assert service.record_receipt(receipt.model_copy(update={
        "rendered_at": clock.wall_now()-timedelta(seconds=1),
    })) == ErrorCode.INVALID_ARGUMENT
    assert service.get("milk").displayed_at is None


def test_context_revision_expiry_and_receipt_window_checks(system):
    service, clock, display, trace = system
    register(service, clock)
    original = envelope(service, clock)
    assert service.validate(original) is None
    observe(service, clock, "working", revision=2)
    assert service.validate(original) == ErrorCode.STALE_CONTEXT
    observe(service, clock, "available", revision=1)
    assert service.validate(original) == ErrorCode.STALE_CONTEXT
    current = envelope(service, clock, context_revision=2,
                       expires_at=clock.wall_now()+timedelta(seconds=1))
    assert service.validate(current) is None
    assert service.request_delivery(current).error_code == ErrorCode.OUTSIDE_WINDOW
    clock.advance(1)
    assert service.validate(current) == ErrorCode.EXPIRED_REQUEST


def test_accepted_plan_deduplicates_and_never_executes_itself(system):
    service, clock, display, trace = system
    register(service, clock)
    plan = ProposedPlan(envelope=envelope(service, clock))
    assert service.accept_decision(plan) is None
    assert service.accept_decision(plan) == ErrorCode.DUPLICATE
    assert not display.commands
    assert "decision.accepted" in kinds(trace)


def test_recheck_bounds_and_cancel_invalidation(system):
    service, clock, display, trace = system
    register(service, clock)
    e = envelope(service, clock)
    assert service.schedule_recheck(e, clock.wall_now()-timedelta(seconds=1)) == ErrorCode.OUTSIDE_WINDOW
    assert service.schedule_recheck(e, clock.wall_now()+timedelta(seconds=31)) == ErrorCode.OUTSIDE_WINDOW
    assert service.schedule_recheck(e, clock.wall_now()+timedelta(seconds=20)) is None
    command(service, "cancel")
    clock.advance(100)
    service.pump()
    assert "recheck.fired" not in kinds(trace)
    assert not display.commands


def test_tool_delivery_stays_pending_until_actual_receipt(system):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, due=0, width=20)
    e = envelope(service, clock)
    result = service.request_delivery(e)
    assert result.status == "pending" and result.receipt_id is None
    issued = display.commands[0]
    service.record_receipt(DisplayReceipt(delivery_id=issued.delivery_id,
                                          task_id="milk", task_version=2,
                                          rendered_at=clock.wall_now(),
                                          outcome="displayed", source="native"))
    result = service.request_delivery(e)
    assert result.status == "succeeded" and result.receipt_id == issued.delivery_id
    assert len(display.commands) == 1
    assert service.get("milk").status == "registered"


def test_malformed_display_adapter_output_is_a_failure_not_a_domain_crash(system):
    service, clock, display, trace = system
    display.show = lambda command: {"pretend": "success"}
    register(service, clock, mode="hard", due=0)
    service.pump()
    assert service.get("milk").displayed_at is None
    assert "delivery.failed" in kinds(trace)
    failure = next(e for e in trace.events if e.kind == "delivery.failed")
    assert failure.payload["error_code"] == ErrorCode.INVALID_ARGUMENT.value


@pytest.mark.parametrize("mode,width", [("hard", 0), ("soft", 1)])
def test_pending_delivery_result_can_be_read_after_window_without_new_side_effect(system, mode, width):
    service, clock, display, trace = system
    display.mode = "missing"
    register(service, clock, mode=mode, due=0, width=width)
    request = envelope(service, clock)
    first = service.request_delivery(request)
    assert first.status == "pending"
    issued = display.commands[0]
    clock.advance(2)
    assert service.request_delivery(request).status == "pending"
    assert len(display.commands) == 1
    receipt = DisplayReceipt(delivery_id=issued.delivery_id, task_id="milk", task_version=2,
                             rendered_at=clock.wall_now(), outcome="displayed", source="native")
    assert service.record_receipt(receipt) is None
    actual = service.request_delivery(request)
    assert actual.status == "succeeded"
    assert actual.receipt_id == issued.delivery_id
    assert len(display.commands) == 1
    # Reading a result cannot bypass authority once the user cancels.
    command(service, "cancel")
    assert service.request_delivery(request).error_code == ErrorCode.CANCELLED


def test_new_delivery_after_window_is_still_rejected(system):
    service, clock, display, trace = system
    register(service, clock, due=0, width=1)
    clock.advance(2)
    result = service.request_delivery(envelope(service, clock))
    assert result.error_code == ErrorCode.OUTSIDE_WINDOW
    assert display.commands == []


def test_expired_user_evidence_rejects_old_request_without_new_observation(system):
    service, clock, display, trace = system
    register(service, clock, due=0, width=20)
    service.observe(Observation(
        observed_at=clock.wall_now(), context_revision=2,
        estimate=UserStateEstimate(state="available", source="user",
                                   valid_until=clock.wall_now()+timedelta(seconds=5)),
    ))
    request = envelope(service, clock, context_revision=2,
                       expires_at=clock.wall_now()+timedelta(seconds=30))
    assert service.validate(request) is None
    clock.advance(6)
    # No source.read() or replacement observation is needed for safe expiry.
    assert service.validate(request) == ErrorCode.STALE_CONTEXT
    assert service.accept_decision(ProposedPlan(envelope=request)) == ErrorCode.STALE_CONTEXT
    assert service.request_delivery(request).error_code == ErrorCode.STALE_CONTEXT
    assert service.schedule_recheck(request, clock.wall_now()+timedelta(seconds=1)) == ErrorCode.STALE_CONTEXT
    service.pump()
    assert display.commands == []
    deferred = [e for e in trace.events if e.kind == "delivery.deferred"]
    assert deferred[-1].payload["state"] == "unknown"
    clock.advance(14)
    service.pump()
    assert len(display.commands) == 1
    assert display.commands[0].reason == "deadline_fallback"


def test_expired_evidence_does_not_block_hard_due_or_fresh_unknown_context(system):
    service, clock, display, trace = system
    register(service, clock, mode="hard", due=10)
    observe(service, clock, "working", revision=2,
            valid_until=clock.wall_now()+timedelta(seconds=5))
    old = envelope(service, clock, context_revision=2)
    clock.advance(6)
    assert service.validate(old) == ErrorCode.STALE_CONTEXT
    observe(service, clock, "unknown", revision=3)
    fresh = envelope(service, clock, context_revision=3, request_id="fresh")
    assert service.validate(fresh) is None
    assert service.validate(old) == ErrorCode.STALE_CONTEXT
    clock.advance(4)
    service.pump()
    assert len(display.commands) == 1
    assert display.commands[0].reason == "hard_due"


def test_observation_sampling_refreshes_ttl_without_duplicate_semantic_events(system):
    service, clock, display, trace = system
    register(service, clock, due=10)
    observe(service, clock, "available", revision=2,
            valid_until=clock.wall_now()+timedelta(seconds=5))
    request = envelope(service, clock, context_revision=2)
    clock.advance(4)
    observe(service, clock, "available", revision=2,
            valid_until=clock.wall_now()+timedelta(seconds=10))
    assert kinds(trace).count("observation.updated") == 1
    clock.advance(2)
    assert service.validate(request) is None  # Refreshed evidence, not the original expired TTL.
    service.observe(Observation(observed_at=clock.wall_now(), context_revision=2,
                                estimate=UserStateEstimate(state="available"),
                                capabilities={"foreground": True}))
    assert kinds(trace).count("observation.updated") == 2
