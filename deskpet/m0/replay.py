"""Replay ordered *inputs* through the same ports used by the Qt workbench."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from deskpet.contracts import (DecisionEnvelope, DisplayReceipt, ProposedPlan,
                              RawSignals, UserCorrection, Scenario, SessionCommand, TaskCommand, ToolCall)
from deskpet.mocks.clock import FakeClock
from .composition import build_environment, provenance
from .trace import summarize

FIXTURES = Path(__file__).with_name("fixtures")


def fixture_paths(scenario=None) -> list[Path]:
    if scenario is not None:
        candidate = Path(scenario)
        if not candidate.is_file():
            candidate = FIXTURES / (str(scenario).removesuffix(".json") + ".json")
        if not candidate.is_file():
            raise ValueError(f"scenario not found: {scenario}")
        return [candidate]
    return sorted(FIXTURES.glob("*.json"))


def load_scenario(path: str | Path) -> Scenario:
    return Scenario.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _error(error) -> str | None:
    return getattr(error, "value", error) if error is not None else None


def _check(expected: dict, actual: dict) -> list[str]:
    """Expectations are executable acceptance criteria, not descriptive comments."""
    allowed = {"tasks", "sessions", "displays", "delivery_requests", "errors", "tool_statuses", "events", "observation", "observation_states"}
    failures = [f"unknown expectation: {k}" for k in expected.keys() - allowed]
    for section in ("tasks", "sessions"):
        for identity, fields in expected.get(section, {}).items():
            view = actual[section].get(identity)
            if view is None:
                failures.append(f"{section}.{identity}: missing")
                continue
            for field, value in fields.items():
                if field not in view or view[field] != value:
                    failures.append(f"{section}.{identity}.{field}: expected {value!r}, got {view.get(field)!r}")
    for name in ("displays", "delivery_requests", "errors", "tool_statuses"):
        if name in expected and actual[name] != expected[name]:
            failures.append(f"{name}: expected {expected[name]!r}, got {actual[name]!r}")
    for key, value in expected.get("observation", {}).items():
        if key not in actual["observation"] or actual["observation"][key] != value:
            failures.append(f"observation.{key}: expected {value!r}, got {actual['observation'].get(key)!r}")
    if "observation_states" in expected and actual["observation_states"] != expected["observation_states"]:
        failures.append(f"observation_states: expected {expected['observation_states']!r}, got {actual['observation_states']!r}")
    for kind, count in expected.get("events", {}).items():
        found = actual["event_counts"].get(kind, 0)
        if found != count:
            failures.append(f"events.{kind}: expected {count}, got {found}")
    return failures


def run_scenario(scenario: Scenario, *, output=None, adapters=None) -> dict:
    if not scenario.expected_outcomes:
        raise ValueError("a replay scenario needs expected_outcomes")
    output = Path(output) if output is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        # Each replay is a new run, not an append to a previous experiment.
        (output / "trace.jsonl").write_text("", encoding="utf-8")
    clock = FakeClock(scenario.initial_time)
    env = build_environment(clock=clock, scenario_id=scenario.scenario_id,
                            output=output, adapters=adapters)
    commands, tools, sessions, errors = [], [], [], []
    observation_states = []
    for event in scenario.events:
        delta = event.at_s - clock.monotonic_now()
        if delta < 0:
            raise ValueError(f"event {event.seq} precedes the current clock")
        clock.advance(delta)
        # Evidence can expire before this input, without a new sensor event.
        # Refresh context only; pumping deadlines here would reorder cancel-vs-due.
        env.refresh_observation()
        data = dict(event.payload)
        env.trace.emit("scenario.input", source="scenario", payload=event.model_dump(mode="json"))
        if event.kind == "command":
            result = env.tasks.apply(TaskCommand.model_validate(data))
            commands.append(result.model_dump(mode="json"))
            error = result.error_code
        elif event.kind == "session":
            result = env.sessions.apply(SessionCommand.model_validate(data))
            sessions.append(result.model_dump(mode="json"))
            error = result.error_code
        elif event.kind == "observation":
            state = data.pop("state", "unknown")
            observation = env.observations.set(state, **data)
            env.tasks.observe(observation)
            error = None
        elif event.kind == "raw_sample":
            if "sample_id" in data:
                env.observations.submit_sample(RawSignals.model_validate(data))
            else:
                env.observations.set_signals(**data)
            error = None
        elif event.kind == "correction":
            env.observations.apply_correction(UserCorrection.model_validate({**data, "origin": "scripted"}))
            error = None
        elif event.kind == "clear_correction":
            if data:
                raise ValueError("clear_correction payload must be empty")
            env.observations.clear_override()
            error = None
        elif event.kind == "advance":
            if data:
                raise ValueError("advance uses at_s; its payload must be empty")
            error = None
        elif event.kind == "fault":
            unknown = data.keys() - {"delivery", "tools"}
            if unknown:
                raise ValueError(f"unknown fault controls: {sorted(unknown)}")
            if "delivery" in data:
                if data["delivery"] not in {"displayed", "failed", "missing"}:
                    raise ValueError("delivery fault must be displayed, failed or missing")
                env.delivery.outcome = data["delivery"]
            if "tools" in data:
                env.runtime.faults = dict(data["tools"])
            error = None
        elif event.kind == "receipt":
            if data.get("delivery_id") == "$last":
                if not env.delivery.commands:
                    raise ValueError("$last receipt has no preceding delivery command")
                last = env.delivery.commands[-1]
                data = {"delivery_id": last.delivery_id, "task_id": last.task_id,
                        "task_version": last.task_version, "rendered_at": clock.wall_now(),
                        "source": "simulated", "outcome": "displayed", **{k:v for k,v in data.items() if k != "delivery_id"}}
            error = env.tasks.record_receipt(DisplayReceipt.model_validate(data))
        elif event.kind == "tool":
            result = env.runtime.execute(ToolCall.model_validate(data))
            tools.append(result.model_dump(mode="json"))
            error = result.error_code
        elif event.kind == "decision":
            if data.get("scripted") is True:
                if set(data) != {"scripted", "envelope"}:
                    raise ValueError("scripted decision requires only scripted and envelope")
                from deskpet.mocks.policy import ScriptedFastPolicy, ScriptedSlowPlanner
                envelope = DecisionEnvelope.model_validate(data["envelope"])
                task = env.tasks.get(envelope.task_id)
                if task is None:
                    raise ValueError("scripted decision requires an existing task")
                observation = env.observations.read()
                env.trace.emit("policy.fast", task_id=task.task_id, task_version=task.version,
                               request_id=envelope.request_id,
                               payload={"decision": ScriptedFastPolicy().decide(observation, task)})
                results = env.runtime.run(observation, task, ScriptedSlowPlanner(clock=clock), envelope)
            else:
                results = env.runtime.execute_plan(ProposedPlan.model_validate(data))
            tools.extend(r.model_dump(mode="json") for r in results)
            errors.extend(_error(r.error_code) for r in results if r.error_code is not None)
            error = None
        else:
            raise ValueError(f"unsupported input kind: {event.kind}")
        if error is not None:
            errors.append(_error(error))
        # Same-time ordering is exactly (at_s, seq): process the input, then due work.
        env.pump()
        observation_states.append(env.observations.read().estimate.state)
    env.pump()
    tasks = {}
    for task in env.tasks.list_tasks():
        data = task.model_dump(mode="json")
        data["displayed"] = task.displayed_at is not None
        tasks[task.task_id] = data
    session_views = {s.session_id:s.model_dump(mode="json") for s in env.sessions.list_sessions()}
    counts = summarize(env.trace.events)["event_counts"]
    frame = env.observations.snapshot()
    observation = frame.observation
    observation_summary = {
        "state": observation.estimate.state, "estimated_state": frame.estimated.state,
        "context_revision": observation.context_revision, "source": observation.source,
        "application": observation.application, "raw_status": frame.raw_status,
        "correction_status": frame.correction_status,
        "sampling_name": frame.sampling_name, "estimator_name": frame.estimator_name,
    }
    outcome = {"observation": observation_summary, "observation_states": observation_states,
               "observation_frame": frame.model_dump(mode="json"), "tasks": tasks, "sessions": session_views,
               "displays": len({e.delivery_id for e in env.trace.events if e.kind == "delivery.displayed"}),
               "delivery_requests": len(env.delivery.commands), "errors": errors,
               "tool_statuses": [r["status"] for r in tools], "event_counts": counts,
               "command_results": commands, "session_results": sessions,
               "tool_results": tools}
    failures = _check(scenario.expected_outcomes, outcome)
    if env.trace.sink_errors:
        failures.append(f"metrics sink failed: {env.trace.sink_errors}")
    report = {"scenario_id": scenario.scenario_id, "status": "passed" if not failures else "failed",
              "profile": "scenario", "clock": "virtual", "receipt_source": "simulated",
              "native_gui": "not_run", "real_sensors": "not_run", "failures": failures,
              "outcomes": outcome, "metrics": summarize(env.trace.events)}
    if output is not None:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


def run_suite(*, scenario=None, output=None, adapters=None) -> dict:
    paths = fixture_paths(scenario)
    if not paths:
        raise ValueError("M0 suite has no scenarios")
    output = Path(output) if output is not None else None
    cases = []
    for path in paths:
        model = load_scenario(path)
        cases.append(run_scenario(model, output=output / model.scenario_id if output else None, adapters=adapters))
    report = {"status": "passed" if all(c["status"] == "passed" for c in cases) else "failed",
              "profile": "scenario", "cases": cases, "provenance": provenance(),
              "platform_acceptance": {"headless": "passed" if all(c["status"] == "passed" for c in cases) else "failed",
                                      "native_gui": "not_run", "live_observation": "not_run"}}
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report
