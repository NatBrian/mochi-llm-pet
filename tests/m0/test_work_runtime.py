"""Behavior tests for the replaceable work/session and bounded runtime ports."""
from datetime import timedelta

import pytest

from deskpet.contracts import (
    DecisionEnvelope, DisplayCommand, ErrorCode, ProposedPlan, SessionCommand,
    TaskCommand, TaskDraft, ToolCall,
)
from deskpet.m0.runtime import ToolRuntime
from deskpet.m0.trace import TraceRecorder
from deskpet.mocks.clock import FakeClock
from deskpet.mocks.delivery import FakeDeliveryPort
from deskpet.mocks.observation import ScriptedObservationSource
from deskpet.mocks.policy import ScriptedFastPolicy, ScriptedSlowPlanner
from deskpet.mocks.task_service import MemoryTaskService
from deskpet.mocks.work_session import MemoryWorkSessionService


@pytest.fixture
def environment():
    clock = FakeClock()
    trace = TraceRecorder(clock)
    delivery = FakeDeliveryPort(clock)
    tasks = MemoryTaskService(clock, delivery, trace.emit)
    sessions = MemoryWorkSessionService(clock, trace.emit)
    observations = ScriptedObservationSource(clock)
    tasks.observe(observations.set("available"))
    draft = TaskDraft(content="buy milk", earliest_at=clock.wall_now(),
                      latest_first_delivery_at=clock.wall_now()+timedelta(seconds=60))
    tasks.apply(TaskCommand(command_id="create", kind="create", task_id="milk",
                            payload={"draft": draft.model_dump(mode="json")}))
    tasks.apply(TaskCommand(command_id="confirm", kind="confirm", task_id="milk", expected_version=1))
    runtime = ToolRuntime(tasks, sessions, clock, trace.emit)
    return clock, trace, delivery, tasks, sessions, observations, runtime


def call_for(env, name, args=None, *, key="call", expires=30):
    clock, _, _, tasks, _, observations, _ = env
    return ToolCall(call_id=key, tool_name=name, args=args or {}, idempotency_key=key,
                    envelope=DecisionEnvelope(request_id="request", task_id="milk",
                                              task_version=tasks.get("milk").version,
                                              context_revision=observations.read().context_revision,
                                              created_at=clock.wall_now(),
                                              expires_at=clock.wall_now()+timedelta(seconds=expires)))


def session_command(command_id, kind, version=0, payload=None):
    return SessionCommand(command_id=command_id, session_id="work", kind=kind,
                          expected_version=version, payload=payload or {})


def test_session_pause_resume_end_preserves_remaining(environment):
    clock, trace, _, _, service, _, _ = environment
    start = service.apply(session_command("start", "start", payload={"goal": "write report", "duration_s": 60}))
    assert start.view.state == "focusing"
    clock.advance(20)
    pause = service.apply(session_command("pause", "pause", 1))
    assert pause.view.remaining_s == 40
    assert pause.view.phase_due_at is None
    clock.advance(500)
    assert service.get("work").remaining_s == 40
    resume = service.apply(session_command("resume", "resume", 2))
    assert resume.view.phase_due_at == clock.wall_now()+timedelta(seconds=40)
    clock.advance(39)
    assert service.get("work").state == "focusing"
    clock.advance(1)
    service.pump()
    service.pump()
    assert service.get("work").state == "break_due"
    assert len([e for e in trace.events if e.kind == "session.break_due"]) == 1
    ended = service.apply(session_command("end", "end", 4,
                                           {"confirmed_outcomes": ["outline done"], "next_steps": ["edit draft"]}))
    assert ended.view.state == "ended"
    assert ended.view.confirmed_outcomes == ("outline done",)
    assert ended.view.next_steps == ("edit draft",)


def test_session_duplicate_stale_and_invalid_transitions(environment):
    service = environment[4]
    start = session_command("start", "start", payload={"goal": "work", "duration_s": 60})
    assert service.apply(start).status == "applied"
    assert service.apply(start).status == "duplicate"
    assert service.get("work").version == 1
    collision = start.model_copy(update={"payload": {"goal": "different", "duration_s": 60}})
    assert service.apply(collision).error_code == ErrorCode.INVALID_ARGUMENT
    assert service.apply(session_command("stale", "pause", 0)).error_code == ErrorCode.STALE_TASK
    assert service.apply(session_command("resume", "resume", 1)).error_code == ErrorCode.INVALID_TRANSITION
    assert service.apply(session_command("bad-end", "end", 1,
                                         {"confirmed_outcomes": "model claims success"})).error_code == ErrorCode.INVALID_ARGUMENT
    assert service.get("work").state == "focusing"


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_session_invalid_duration_has_no_side_effect(environment, duration):
    service = environment[4]
    result = service.apply(session_command("start", "start", payload={"goal": "work", "duration_s": duration}))
    assert result.error_code == ErrorCode.INVALID_ARGUMENT
    assert service.list_sessions() == []


def test_observation_sampling_and_explicit_user_override(environment):
    clock, _, _, _, _, source, _ = environment
    original = source.read()
    clock.advance(5)
    sampled = source.read()
    assert sampled.context_revision == original.context_revision
    assert sampled.observed_at > original.observed_at
    same = source.set("available", application="Code", idle_s=1)
    assert same.context_revision > original.context_revision
    assert source.set("available", idle_s=2).context_revision == same.context_revision
    override = source.set("working", source="user", evidence=["user_correction"],
                          valid_until=clock.wall_now()+timedelta(seconds=10))
    assert override.context_revision > same.context_revision
    ignored = source.set("away", evidence=["idle"])
    assert ignored.estimate.source == "user"
    assert ignored.context_revision == override.context_revision
    clock.advance(10)
    restored = source.read()
    assert restored.estimate.state == "away"
    assert restored.context_revision > override.context_revision
    source.set("available", source="user")
    assert source.clear_override().estimate.state == "away"


def test_delivery_missing_then_success_is_idempotent(environment):
    clock, _, delivery, _, _, _, _ = environment
    command = DisplayCommand(delivery_id="d1", task_id="milk", task_version=2, occurrence=1,
                             content="milk", requested_at=clock.wall_now(),
                             expires_at=clock.wall_now()+timedelta(seconds=20), reason="tool")
    delivery.outcome = "missing"
    assert delivery.show(command) is None
    delivery.outcome = "displayed"
    receipt = delivery.show(command)
    assert receipt.source == "simulated"
    clock.advance(1)
    assert delivery.show(command) == receipt
    assert len(delivery.commands) == len(delivery.visible) == 1
    collision = command.model_copy(update={"content": "different task text"})
    assert delivery.show(collision).error_code == ErrorCode.INVALID_ARGUMENT
    delivery.dismiss("milk")
    assert delivery.visible == {}


def test_tool_display_waits_for_receipt_and_requeries_pending(environment):
    clock, _, delivery, tasks, _, _, runtime = environment
    delivery.outcome = "missing"
    call = call_for(environment, "show_reminder")
    assert runtime.execute(call).status == "pending"
    assert tasks.get("milk").displayed_at is None
    delivery.outcome = "displayed"
    clock.advance(5)
    tasks.pump()
    result = runtime.execute(call)
    assert result.status in ("succeeded", "duplicate")
    assert tasks.get("milk").displayed_at is not None
    assert tasks.get("milk").status == "registered"
    assert len(delivery.commands) == 1


def test_tool_idempotency_and_revalidation(environment):
    clock, _, _, tasks, sessions, _, runtime = environment
    call = call_for(environment, "start_pomodoro", {"session_id": "work", "goal": "write", "duration_s": 60})
    assert runtime.execute(call).status == "succeeded"
    assert runtime.execute(call).status == "duplicate"
    assert len(sessions.list_sessions()) == 1
    altered = call.model_copy(update={"args": {"session_id": "work-2", "goal": "write"}})
    assert runtime.execute(altered).error_code == ErrorCode.INVALID_ARGUMENT
    tasks.apply(TaskCommand(command_id="cancel", task_id="milk", kind="cancel", expected_version=2))
    assert runtime.execute(call).error_code in (ErrorCode.CANCELLED, ErrorCode.STALE_TASK)
    assert len(sessions.list_sessions()) == 1


def test_plan_validation_prevents_partial_effects(environment):
    runtime = environment[-1]
    first = call_for(environment, "save_important_observation", {"state": "working", "evidence": ["input_active"]})
    invalid = first.model_copy(update={"call_id": "invalid", "idempotency_key": "invalid",
                                       "tool_name": "run_shell", "args": {"command": "echo should-never-run"}})
    result = runtime.execute_plan(ProposedPlan(envelope=first.envelope, calls=(first, invalid)))
    assert result[0].error_code == ErrorCode.UNKNOWN_TOOL
    assert runtime.saved_observations == []
    nested = call_for(environment, "execute_plan", {"steps": [{"tool_name": "execute_plan", "args": {"steps": []}}]})
    assert runtime.execute(nested).error_code == ErrorCode.INVALID_ARGUMENT
    assert runtime.saved_observations == []


def test_plan_budget_and_expiry(environment):
    clock, _, _, _, _, _, runtime = environment
    call = call_for(environment, "save_important_observation", {"state": "available", "evidence": ["user"]}, expires=1)
    calls = tuple(call.model_copy(update={"call_id": str(i), "idempotency_key": str(i)}) for i in range(5))
    assert runtime.execute_plan(ProposedPlan(envelope=call.envelope, calls=calls))[0].error_code == ErrorCode.BUDGET_EXCEEDED
    assert runtime.saved_observations == []
    clock.advance(1)
    assert runtime.execute(call).error_code == ErrorCode.EXPIRED_REQUEST
    assert runtime.saved_observations == []


def test_fault_feedback_is_bounded_and_schedules_recheck(environment):
    _, trace, _, tasks, _, observations, runtime = environment
    call = call_for(environment, "show_reminder")
    runtime.faults["show_reminder"] = "timeout"
    results = runtime.run(observations.read(), tasks.get("milk"), ScriptedSlowPlanner(), call.envelope)
    assert [r.status for r in results] == ["timeout", "succeeded"]
    feedback = [e for e in trace.events if e.kind == "runtime.feedback"]
    assert len(feedback) == 2
    assert feedback[0].payload["results"][0]["error_code"] == "TIMEOUT"
    assert tasks.get("milk").displayed_at is None


def test_flat_execute_plan_tool_results(environment):
    runtime = environment[-1]
    call = call_for(environment, "execute_plan", {"steps": [
        {"tool_name": "save_important_observation", "args": {"state": "unknown", "evidence": ["permission_missing"]}},
        {"tool_name": "start_pomodoro", "args": {"session_id": "work", "goal": "review"}},
    ]})
    result = runtime.execute(call)
    assert result.status == "succeeded"
    assert len(result.evidence["steps"]) == 2
    assert len(runtime.saved_observations) == 1
    assert runtime.execute(call).status == "duplicate"
    assert len(runtime.saved_observations) == 1


def test_fast_policy_never_blocks_unknown_with_model(environment):
    _, _, _, tasks, _, source, _ = environment
    policy = ScriptedFastPolicy()
    assert policy.decide(source.set("unknown"), tasks.get("milk")) == "request_slow"
    assert policy.decide(source.set("working"), tasks.get("milk")) == "defer"
    assert policy.decide(source.set("available"), tasks.get("milk")) == "react"


def test_pending_plan_retry_does_not_stop_at_acceptance_dedup(environment):
    clock, _, delivery, tasks, _, _, runtime = environment
    delivery.outcome = "missing"
    call = call_for(environment, "show_reminder")
    plan = ProposedPlan(envelope=call.envelope, calls=(call,))
    assert runtime.execute_plan(plan)[0].status == "pending"
    delivery.outcome = "displayed"
    clock.advance(5)
    tasks.pump()
    assert runtime.execute_plan(plan)[0].status == "succeeded"
    assert runtime.execute_plan(plan)[0].status == "duplicate"
    assert len(delivery.commands) == 1


def test_naive_recheck_tail_rejects_before_any_plan_effect(environment):
    runtime = environment[-1]
    first = call_for(environment, "save_important_observation", {"state": "working", "evidence": ["input"]})
    last = first.model_copy(update={"call_id": "tail", "idempotency_key": "tail", "tool_name": "schedule_recheck", "args": {"run_at": "2026-01-01T17:00:05"}})
    results = runtime.execute_plan(ProposedPlan(envelope=first.envelope, calls=(first,last)))
    assert results[0].error_code == ErrorCode.INVALID_ARGUMENT
    assert runtime.saved_observations == []


def test_session_same_command_payload_key_order_is_duplicate(environment):
    service = environment[4]
    original = session_command("canonical", "start", payload={"goal": "work", "duration_s": 60})
    reordered = session_command("canonical", "start", payload={"duration_s": 60, "goal": "work"})
    assert service.apply(original).status == "applied"
    assert service.apply(reordered).status == "duplicate"
    assert service.get("work").version == 1


def test_live_observation_origin_survives_pump_and_user_override(environment):
    source = environment[5]
    live = source.set("working", source="rule", evidence=["foreground"],
                      application="Editor", observation_source="live")
    assert live.source == source.read().source == "live"
    user = source.set("available", source="user", evidence=["correction"])
    assert user.source == "live"
    assert user.estimate.source == "user"
    subsequent = source.set("away", source="rule", evidence=["idle"], idle_s=200)
    assert subsequent.source == "live"
    assert subsequent.estimate.state == "available"
    assert subsequent.context_revision == user.context_revision
    assert source.clear_override().estimate.state == "away"
    assert source.read().source == "live"
    revision = source.read().context_revision
    changed_app = source.set("away", source="rule", evidence=["idle"], application="Browser")
    assert changed_app.context_revision == revision + 1
    assert source.set("away", source="rule", evidence=["idle"], idle_s=201).context_revision == changed_app.context_revision
    assert source.set("unknown", observation_source="scripted").source == "scripted"
