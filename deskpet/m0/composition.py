"""Explicit, replaceable M0 wiring. No native GUI or model is started on import."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any

from deskpet.contracts.models import SCHEMA_VERSION
from deskpet.mocks.clock import FakeClock, RealClock
from .trace import JsonlMetricsSink, TraceRecorder

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_KEYS = frozenset({"task_service", "work_sessions", "metrics", "gui", "ui_surface", "sampling_source", "state_estimator"})


def load_adapters(value=None) -> dict[str, str]:
    if value is None:
        return {}
    data = json.loads(Path(value).read_text(encoding="utf-8")) if isinstance(value, (str, Path)) else dict(value)
    unknown = set(data) - ADAPTER_KEYS
    if unknown:
        raise ValueError(f"unknown adapter roles: {sorted(unknown)}")
    for role, spec in data.items():
        if not isinstance(spec, str) or spec.count(":") != 1:
            raise ValueError(f"{role} factory must be module:callable")
    return data


def _factory(adapters, role, default):
    if role not in adapters:
        return default
    module, symbol = adapters[role].split(":")
    factory = getattr(import_module(module), symbol)
    if not callable(factory):
        raise ValueError(f"{role} adapter is not callable")
    return factory


def resolve_gui(adapters=None):
    """Resolve an explicitly configured developer entry; default Qt stays lazy.

    A GUI entry is ``run_gui(args) -> int`` rather than a domain factory. It is
    resolved only by GUI/native-smoke commands, never during replay composition.
    """
    configured = load_adapters(adapters)
    if "gui" in configured and "ui_surface" in configured:
        raise ValueError("choose ui_surface inside the workbench, or gui as a complete entry; do not configure both")
    if "gui" in configured:
        return _factory(configured, "gui", None)
    from .gui import run_gui
    return run_gui


def resolve_ui_surface(adapters=None):
    """Return the optional embedded-widget factory; headless callers never load it."""
    configured = load_adapters(adapters)
    if "gui" in configured and "ui_surface" in configured:
        raise ValueError("ui_surface uses the workbench host and cannot be combined with gui")
    return _factory(configured, "ui_surface", None)


def resolve_sampling_source(*, profile="scenario", clock, adapters=None):
    """Construct one sampler on its required host thread.

    The native default must be resolved by the GUI main thread. Domain services
    use a BufferedSamplingSource for externally supplied native samples.
    """
    configured = load_adapters(adapters)
    if profile == "scenario":
        from deskpet.mocks.sampling import ScriptedSamplingSource
        default = ScriptedSamplingSource
    elif profile == "live-smoke":
        from .live_observation import LiveSamplingSource
        default = LiveSamplingSource
    else:
        raise ValueError(f"unknown profile: {profile}")
    return _factory(configured, "sampling_source", default)(clock=clock)


def resolve_state_estimator(*, clock, adapters=None):
    """Estimators receive measurements only and are independently replaceable."""
    from deskpet.mocks.state_estimator import RuleStateEstimator
    configured = load_adapters(adapters)
    return _factory(configured, "state_estimator", RuleStateEstimator)(clock=clock)


@dataclass
class ObservationEnvironment:
    clock: Any
    trace: TraceRecorder
    sampling: Any
    estimator: Any
    observations: Any
    profile: str
    adapters: dict[str, str]


def build_observation_environment(*, profile="scenario", clock=None, adapters=None,
                                  sampling_source=None, state_estimator=None,
                                  external_sampling=False, trace=None, output=None,
                                  scenario_id="observation") -> ObservationEnvironment:
    """Wire only measurement/estimation/correction; no task, GUI or model clients."""
    from deskpet.mocks.sampling import BufferedSamplingSource
    from .observation import ObservationPipeline
    if profile not in {"scenario", "live-smoke"}:
        raise ValueError(f"unknown profile: {profile}")
    clock = clock if clock is not None else (FakeClock() if profile == "scenario" else RealClock())
    configured = load_adapters(adapters)
    if trace is None:
        sink = None
        if output is not None:
            output = Path(output)
            output.mkdir(parents=True, exist_ok=True)
            sink = _factory(configured, "metrics", JsonlMetricsSink)(path=output / "trace.jsonl")
        elif "metrics" in configured:
            raise ValueError("a metrics adapter requires an output directory")
        trace = TraceRecorder(clock, run_id=f"m0-{scenario_id}", scenario_id=scenario_id, sink=sink)
    if sampling_source is None:
        if external_sampling or profile == "live-smoke":
            sampling_source = BufferedSamplingSource(clock=clock)
        else:
            sampling_source = resolve_sampling_source(profile=profile, clock=clock, adapters=configured)
    estimator = state_estimator if state_estimator is not None else resolve_state_estimator(clock=clock, adapters=configured)
    pipeline = ObservationPipeline(clock, sampling_source, estimator, trace.emit)
    return ObservationEnvironment(clock, trace, sampling_source, estimator, pipeline, profile, configured)


@dataclass
class Environment:
    clock: Any
    trace: TraceRecorder
    tasks: Any
    sessions: Any
    observations: Any
    runtime: Any
    delivery: Any
    profile: str
    adapters: dict[str, str]
    sampling: Any
    estimator: Any

    def refresh_observation(self):
        """Refresh TTL/override semantics without running due work or changing input order."""
        observation = self.observations.read()
        self.tasks.observe(observation)
        return observation

    def pump(self):
        self.refresh_observation()
        self.tasks.pump()
        self.sessions.pump()


def build_environment(*, profile="scenario", scenario_id="manual", output=None,
                      clock=None, delivery=None, adapters=None, sampling_source=None,
                      state_estimator=None, external_sampling=False) -> Environment:
    """Factory contracts: task(clock, delivery, emit), session(clock, emit), sink(path).

    Call from the service's owner thread. Native observations are supplied by the
    Qt main thread; live-smoke changes the clock only, never constructs OS objects.
    """
    from deskpet.mocks.task_service import MemoryTaskService
    from deskpet.mocks.work_session import MemoryWorkSessionService
    from deskpet.mocks.delivery import FakeDeliveryPort
    from .runtime import ToolRuntime

    if profile not in {"scenario", "live-smoke"}:
        raise ValueError(f"unknown profile: {profile}")
    clock = clock if clock is not None else (FakeClock() if profile == "scenario" else RealClock())
    adapters = load_adapters(adapters)
    sink = None
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "trace.jsonl").write_text("", encoding="utf-8")
        sink = _factory(adapters, "metrics", JsonlMetricsSink)(path=output / "trace.jsonl")
    elif "metrics" in adapters:
        raise ValueError("a metrics adapter requires an output directory")
    trace = TraceRecorder(clock, run_id=f"m0-{scenario_id}", scenario_id=scenario_id, sink=sink)
    delivery = delivery if delivery is not None else FakeDeliveryPort(clock)
    tasks = _factory(adapters, "task_service", MemoryTaskService)(clock=clock, delivery=delivery, emit=trace.emit)
    sessions = _factory(adapters, "work_sessions", MemoryWorkSessionService)(clock=clock, emit=trace.emit)
    observation_env = build_observation_environment(
        profile=profile, clock=clock, adapters=adapters, sampling_source=sampling_source,
        state_estimator=state_estimator, external_sampling=external_sampling, trace=trace)
    observations = observation_env.observations
    tasks.observe(observations.read())
    runtime = ToolRuntime(tasks=tasks, sessions=sessions, clock=clock, emit=trace.emit)
    return Environment(clock, trace, tasks, sessions, observations, runtime, delivery, profile, adapters,
                       observation_env.sampling, observation_env.estimator)


def provenance() -> dict:
    """Hash executable sources and fixtures, excluding local configs, DBs and secrets."""
    files = set((ROOT / "deskpet").rglob("*.py"))
    files.update((ROOT / "deskpet/m0/fixtures").glob("*.json"))
    files.update((ROOT / "scripts").glob("*m0*"))
    files.update((ROOT / "scripts").glob("*.ps1"))
    files.update((ROOT / "tests/m0").glob("*.py"))
    files.update((ROOT / "docs/contracts").glob("*.json"))
    files.update((ROOT / "examples/m0").glob("*.json"))
    files.update(ROOT / name for name in ("pyproject.toml", "requirements.txt", "uv.lock", ".python-version"))
    hashes = {p.relative_to(ROOT).as_posix(): sha256(
                  p.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
              ).hexdigest() for p in sorted(files) if p.is_file()}
    digest = sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    versions = {}
    for package in ("PyQt6", "PyQt6-Qt6", "pydantic"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"commit": commit, "source_sha256": digest, "file_hashes": hashes,
            "hash_algorithm": "SHA256 of sorted path-to-SHA256 map; UTF-8 text with LF line endings",
            "hash_normalization": "utf8_lf",
            "contract_version": SCHEMA_VERSION,
            "fixture_sha256": {p: h for p, h in hashes.items() if "/fixtures/" in p},
            "python": platform.python_version(), "os": platform.platform(),
            "machine": platform.machine(), "packages": versions,
            "qt_platform_override": os.environ.get("QT_QPA_PLATFORM"),
            "model_source": "scripted", "real_model_calls": 0}
