"""Business facts stay separate from optional experiment sinks."""
from __future__ import annotations
import json
from pathlib import Path
from deskpet.contracts import TraceEvent


class JsonlMetricsSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
    def record(self, event: TraceEvent) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")


class TraceRecorder:
    def __init__(self, clock, *, run_id="m0", scenario_id="manual", sink=None):
        self.clock = clock
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.sink = sink
        self.events: list[TraceEvent] = []
        self.sink_errors: list[str] = []
    def emit(self, kind: str, **kwargs) -> None:
        event = TraceEvent(seq=len(self.events)+1, ts=self.clock.wall_now(),
                           monotonic_s=self.clock.monotonic_now(), run_id=self.run_id,
                           scenario_id=self.scenario_id, kind=kind,
                           source=kwargs.pop("source", "reference"),
                           simulated=kwargs.pop("simulated", True),
                           clock=getattr(self.clock, "kind", "virtual"), **kwargs)
        self.events.append(event)
        if self.sink:
            try:
                self.sink.record(event)
            except Exception as exc:
                self.sink_errors.append(type(exc).__name__)


def summarize(events: list[TraceEvent]) -> dict:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return {"event_counts": counts, "total_events": len(events),
            "cost": {"model_calls": 0, "api_charges": None, "simulated": True},
            "latency": {"status": "not_measured", "reason": "replay is not a native latency benchmark"}}
