"""Learning adapter for D's MetricsSink port.

Preserve complete TraceEvents. This sink does not change business state.
"""

import json
from pathlib import Path


class DMetricsSink:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

        # A separate diagnostic file shows which adapter was constructed.
        info_path = self.path.with_name("d-sink-info.json")
        info_path.write_text(
            json.dumps({"implementation": "d_metrics.DMetricsSink",
                        "trace_file": self.path.name}, indent=2) + "\n",
            encoding="utf-8",
        )

    def record(self, event):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")


def factory(*, path):
    return DMetricsSink(path)
