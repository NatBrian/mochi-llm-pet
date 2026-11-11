"""Acceptance checks for the same reference ports used by the real Qt workbench."""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess
import sys
import types

import pytest

from deskpet.contracts import Scenario, ScenarioEvent
from deskpet.m0.composition import build_environment, load_adapters, provenance
from deskpet.m0.replay import fixture_paths, load_scenario, run_scenario, run_suite


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p:p.stem)
def test_shared_scenario_contract(path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("scenario made a network request")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    result = run_scenario(load_scenario(path))
    assert result["status"] == "passed", result["failures"]
    assert result["receipt_source"] == "simulated"
    assert result["native_gui"] == "not_run"
    assert result["metrics"]["cost"]["model_calls"] == 0


def test_repeat_replay_produces_identical_business_outcomes(tmp_path):
    scenario = load_scenario(fixture_paths("soft-opportunity")[0])
    first = run_scenario(scenario, output=tmp_path / "first")
    second = run_scenario(scenario, output=tmp_path / "second")
    assert first == second
    assert (tmp_path / "first/trace.jsonl").read_bytes() == (tmp_path / "second/trace.jsonl").read_bytes()


def test_trace_file_is_per_run_not_appended(tmp_path):
    scenario = load_scenario(fixture_paths("hard-busy")[0])
    run_scenario(scenario, output=tmp_path)
    first = (tmp_path / "trace.jsonl").read_bytes()
    run_scenario(scenario, output=tmp_path)
    assert (tmp_path / "trace.jsonl").read_bytes() == first


def test_expectation_mismatch_is_not_silently_passed():
    original = load_scenario(fixture_paths("hard-busy")[0])
    broken = original.model_copy(update={"expected_outcomes": {"displays": 99}})
    report = run_scenario(broken)
    assert report["status"] == "failed"
    assert "expected 99" in report["failures"][0]


def test_unknown_expectation_fails():
    original = load_scenario(fixture_paths("hard-busy")[0])
    broken = original.model_copy(update={"expected_outcomes": {"made_up": True}})
    assert run_scenario(broken)["status"] == "failed"


def test_same_time_cancel_precedes_due_by_input_sequence():
    original = load_scenario(fixture_paths("hard-busy")[0])
    events = original.events[:3] + (ScenarioEvent(seq=4, at_s=10, kind="command", payload={
        "command_id":"cancel-at-due", "task_id":"milk", "expected_version":2,
        "kind":"cancel", "payload":{},
    }),)
    scenario = original.model_copy(update={"events":events, "expected_outcomes":{
        "displays":0, "tasks":{"milk":{"status":"cancelled", "version":3}}, "errors":[],
    }})
    assert run_scenario(scenario)["status"] == "passed"


def test_role_factory_can_replace_backend_without_changing_callers(monkeypatch):
    from deskpet.mocks.task_service import MemoryTaskService
    from deskpet.mocks.work_session import MemoryWorkSessionService
    called = []
    module = types.ModuleType("m0_test_adapters")
    def make_tasks(*, clock, delivery, emit):
        called.append("B")
        return MemoryTaskService(clock, delivery, emit)
    def make_sessions(*, clock, emit):
        called.append("C")
        return MemoryWorkSessionService(clock, emit)
    module.make_tasks = make_tasks
    module.make_sessions = make_sessions
    monkeypatch.setitem(sys.modules, module.__name__, module)
    scenario = load_scenario(fixture_paths("reminder-flow")[0])
    result = run_scenario(scenario, adapters={"task_service":"m0_test_adapters:make_tasks",
                                               "work_sessions":"m0_test_adapters:make_sessions"})
    assert result["status"] == "passed"
    assert called == ["B", "C"]


def test_unknown_adapter_role_rejected():
    with pytest.raises(ValueError, match="unknown adapter"):
        load_adapters({"typo":"my.module:factory"})


def test_missing_scenario_is_actionable():
    with pytest.raises(ValueError, match="scenario not found"):
        fixture_paths("no-such-scenario")


def test_headless_import_does_not_load_qt_or_models():
    result = subprocess.run([sys.executable, "-c", "import sys; import deskpet.m0.composition; import deskpet.m0.replay; assert not any(x.startswith(('PyQt6', 'httpx', 'anthropic')) for x in sys.modules)"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_suite_report_identifies_simulation_and_fingerprints(tmp_path):
    report = run_suite(output=tmp_path)
    assert report["status"] == "passed"
    assert len(report["cases"]) >= 10
    assert report["platform_acceptance"]["native_gui"] == "not_run"
    assert len(report["provenance"]["source_sha256"]) == 64
    assert report["provenance"]["fixture_sha256"]
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "passed"


def test_cross_platform_source_hash_normalizes_text_line_endings(tmp_path, monkeypatch):
    import deskpet.m0.composition as composition
    root = tmp_path / "checkout"
    source = root / "deskpet/sample.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# same source\r\nvalue = 1\r\n")
    monkeypatch.setattr(composition, "ROOT", root)
    windows = composition.provenance()
    source.write_bytes(b"# same source\nvalue = 1\n")
    macos = composition.provenance()
    assert windows["source_sha256"] == macos["source_sha256"]
    assert windows["hash_normalization"] == "utf8_lf"


def test_unsupported_task_expectation_field_cannot_pass_as_none():
    original = load_scenario(fixture_paths("hard-busy")[0])
    broken = original.model_copy(update={"expected_outcomes": {"tasks":{"milk":{"not_a_field":None}}}})
    assert run_scenario(broken)["status"] == "failed"


def test_environment_pump_refreshes_expired_override_before_domain_work():
    from deskpet.contracts import DecisionEnvelope, ErrorCode, TaskCommand
    env = build_environment()
    for event in load_scenario(fixture_paths("hard-busy")[0]).events:
        if event.kind == "command":
            env.tasks.apply(TaskCommand.model_validate(event.payload))
    now = env.clock.wall_now()
    original = env.observations.set("working", source="user", valid_until=now+timedelta(seconds=5))
    env.pump()
    envelope = DecisionEnvelope(request_id="before-expiry", task_id="milk", task_version=2,
                                context_revision=original.context_revision,
                                created_at=now, expires_at=now+timedelta(seconds=60))
    assert env.tasks.validate(envelope) is None
    env.clock.advance(6)
    env.pump()
    current = env.observations.read()
    assert current.estimate.state == "unknown"
    assert current.context_revision > original.context_revision
    assert env.tasks.validate(envelope) == ErrorCode.STALE_CONTEXT


@pytest.mark.parametrize("arguments", [
    ["gui", "--profile", "scenario", "--scenario", "custom-case"],
    ["smoke", "--profile", "scenario", "--native-gui"],
    ["smoke", "--profile", "live-smoke"],
])
def test_gui_adapter_routes_explicit_entry_without_importing_qt(tmp_path, arguments):
    (tmp_path / "own_gui.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_gui(args):\n"
        "    Path(args.output).mkdir(parents=True, exist_ok=True)\n"
        "    Path(args.output, 'entry.json').write_text(json.dumps(vars(args)))\n"
        "    return 0\n", encoding="utf-8")
    config = tmp_path / "adapters.json"
    config.write_text(json.dumps({"gui":"own_gui:run_gui"}), encoding="utf-8")
    output = tmp_path / "custom-output"
    argv = arguments + ["--adapters", str(config), "--output", str(output)]
    script = ("import sys; sys.path.insert(0, " + repr(str(tmp_path)) + "); "
              "from deskpet.m0.__main__ import main; "
              "assert main(" + repr(argv) + ") == 0; "
              "assert not any(name.startswith('PyQt6') for name in sys.modules)")
    process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    routed = json.loads((output / "entry.json").read_text())
    assert routed["profile"] == ("live-smoke" if "live-smoke" in arguments else "scenario")
    assert routed["adapters"] == str(config)
    assert routed["auto_smoke"] is (arguments[0] == "smoke")


def test_replay_ignores_gui_role_without_importing_it():
    scenario = load_scenario(fixture_paths("reminder-flow")[0])
    report = run_scenario(scenario, adapters={"gui":"deliberately_missing_gui_module:run_gui"})
    assert report["status"] == "passed"
    assert "deliberately_missing_gui_module" not in sys.modules


def _observation_factories(monkeypatch):
    from deskpet.contracts import RawSignals, UserStateEstimate
    calls = []
    module = types.ModuleType("independent_observation_adapters")
    def make_sampler(*, clock):
        calls.append("sampling_factory")
        class OwnSampler:
            def sample(self):
                calls.append("sample")
                return RawSignals(sample_id="own-sample", sampler_id="OwnSampler",
                                  sampled_at=clock.wall_now(), valid_until=clock.wall_now()+timedelta(seconds=30),
                                  capabilities={"idle":True,"foreground":True}, application="Editor", idle_s=15)
        return OwnSampler()
    def make_estimator(*, clock):
        calls.append("estimator_factory")
        class OwnEstimator:
            def estimate(self, raw):
                calls.append(("estimate", raw.sample_id))
                return UserStateEstimate(state="working", source="model", estimator_id="OwnEstimator",
                                         based_on_sample_id=raw.sample_id, evidence=("own-evidence",),
                                         valid_until=raw.valid_until)
        return OwnEstimator()
    module.make_sampler, module.make_estimator = make_sampler, make_estimator
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.__name__, calls


def test_sampling_and_estimator_factories_can_be_replaced_independently(monkeypatch):
    from deskpet.m0.composition import build_observation_environment
    name, calls = _observation_factories(monkeypatch)
    source_only = build_observation_environment(adapters={"sampling_source":name+":make_sampler"})
    frame = source_only.observations.snapshot()
    assert frame.observation.estimate.state == "available"
    assert frame.raw.sample_id == "own-sample"
    assert frame.estimator_name == "RuleStateEstimator"
    assert "estimator_factory" not in calls
    calls.clear()
    estimator_only = build_observation_environment(adapters={"state_estimator":name+":make_estimator"})
    frame = estimator_only.observations.snapshot()
    assert frame.observation.estimate.state == "working"
    assert frame.estimated.source == "model"
    assert "sampling_factory" not in calls
    calls.clear()
    both = build_observation_environment(adapters={"sampling_source":name+":make_sampler",
                                                   "state_estimator":name+":make_estimator"})
    frame = both.observations.snapshot()
    assert frame.raw.sampler_id == "OwnSampler" and frame.estimated.estimator_id == "OwnEstimator"
    assert ("estimate", "own-sample") in calls


def test_live_service_buffer_does_not_construct_the_gui_sampler():
    from deskpet.m0.composition import build_observation_environment
    env = build_observation_environment(profile="live-smoke", external_sampling=True,
             adapters={"sampling_source":"not_installed_native_sampler:factory"})
    frame = env.observations.snapshot()
    assert frame.raw_status == "missing"
    assert frame.observation.source == "live"
    assert frame.observation.estimate.state == "unknown"
    assert "not_installed_native_sampler" not in sys.modules


def test_full_raw_fixture_preserves_future_capture_time_and_expiry():
    from datetime import datetime, timezone
    from deskpet.contracts import RawSignals
    initial = datetime(2026,1,1,17,tzinfo=timezone.utc)
    raw = RawSignals(sample_id="recorded",sampler_id="fixture",sampled_at=initial+timedelta(seconds=2),
                     valid_until=initial+timedelta(seconds=4),application="Editor",idle_s=20,
                     capabilities={"idle":True,"foreground":True})
    events = (ScenarioEvent(seq=1,at_s=0,kind="raw_sample",payload=raw.model_dump(mode="json")),
              ScenarioEvent(seq=2,at_s=3,kind="advance"),
              ScenarioEvent(seq=3,at_s=5,kind="advance"))
    scenario = Scenario(scenario_id="typed-raw",initial_time=initial,events=events,
                        expected_outcomes={"observation_states":["unknown","available","unknown"],
                                           "observation":{"raw_status":"expired","state":"unknown"}})
    report = run_scenario(scenario)
    assert report["status"] == "passed", report["failures"]
    assert report["outcomes"]["observation_frame"]["raw"]["sampled_at"] == "2026-01-01T17:00:02Z"


def test_independent_observe_cli_does_not_construct_tasks_or_load_qt(tmp_path):
    from datetime import datetime, timezone
    from deskpet.contracts import RawSignals
    initial = datetime(2026,1,1,17,tzinfo=timezone.utc)
    raw = RawSignals(sample_id="input",sampler_id="fixture",sampled_at=initial,
                     valid_until=initial+timedelta(seconds=2),application="Reader",idle_s=20,
                     capabilities={"idle":True,"foreground":True})
    path = tmp_path/"raw.json"
    path.write_text(raw.model_dump_json(),encoding="utf-8")
    output = tmp_path/"inspect"
    argv = ["observe","--input",str(path),"--advance","3","--output",str(output)]
    code = ("import sys; from deskpet.m0.__main__ import main; "
            "assert main("+repr(argv)+") == 1; "
            "assert 'deskpet.mocks.task_service' not in sys.modules; "
            "assert not any(n.startswith(('PyQt6','httpx','anthropic')) for n in sys.modules)")
    process = subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
    assert process.returncode == 0, process.stderr+process.stdout
    report = json.loads((output/"observation-report.json").read_text())
    assert report["sampling_factory_bypassed"] is True
    assert report["input_mode"] == "raw_fixture" and report["real_sensors"] == "not_run"
    assert report["observation_frame"]["raw_status"] == "expired"
    assert report["observation_frame"]["observation"]["estimate"]["state"] == "unknown"


def test_independent_observe_cli_explicitly_rejects_native_without_gui(tmp_path):
    from deskpet.m0.__main__ import main
    assert main(["observe","--profile","live-smoke","--output",str(tmp_path)]) == 2
    report=json.loads((tmp_path/"observation-report.json").read_text())
    assert report["status"] == "unsupported"
    assert report["real_sensors"] == "not_run"


@pytest.mark.parametrize("broken_role", ["sampling_source", "state_estimator"])
def test_broken_observation_adapter_stays_visible_and_does_not_fall_back(monkeypatch, broken_role):
    from deskpet.m0.composition import build_observation_environment
    calls=[]
    module=types.ModuleType("broken_observation_adapter")
    class BrokenSampler:
        def sample(self):
            calls.append("bad_sampler")
            return {"invented_measurement":True}
    class BrokenEstimator:
        def estimate(self, raw):
            calls.append("bad_estimator")
            return {"state":"magically_focused"}
    def factory(*, clock):
        return BrokenSampler() if broken_role == "sampling_source" else BrokenEstimator()
    module.factory=factory
    monkeypatch.setitem(sys.modules,module.__name__,module)
    env=build_observation_environment(adapters={broken_role:module.__name__+":factory"})
    frame=env.observations.snapshot()
    assert frame.observation.estimate.state == "unknown"
    assert frame.error and ("sampling_error" if broken_role=="sampling_source" else "estimator_error") in frame.error
    assert calls == ["bad_sampler" if broken_role=="sampling_source" else "bad_estimator"]


def test_replanning_uses_current_clock_not_the_capture_timestamp():
    from deskpet.contracts import DecisionEnvelope
    from deskpet.mocks.policy import ScriptedSlowPlanner
    from deskpet.mocks.clock import FakeClock
    from deskpet.m0.composition import build_observation_environment
    from deskpet.contracts import TaskDraft, TaskView, ToolResult, ErrorCode
    clock=FakeClock()
    env=build_observation_environment(clock=clock)
    env.observations.set_signals(application="Reader",idle_s=20,ttl_s=60)
    observation=env.observations.read()
    task=TaskView(task_id="t",version=2,status="registered",draft=TaskDraft(content="milk",
        earliest_at=clock.wall_now()+timedelta(seconds=10),latest_first_delivery_at=clock.wall_now()+timedelta(seconds=30)))
    envelope=DecisionEnvelope(request_id="r",task_id="t",task_version=2,
        context_revision=observation.context_revision,created_at=clock.wall_now(),expires_at=clock.wall_now()+timedelta(seconds=60))
    clock.advance(10)
    plan=ScriptedSlowPlanner(clock=clock).plan(observation,task,envelope,
        (ToolResult(call_id="c",status="timeout",error_code=ErrorCode.TIMEOUT),))
    assert observation.observed_at == envelope.created_at
    assert plan.calls[0].args["run_at"] == (clock.wall_now()+timedelta(seconds=5)).isoformat()
