"""Learning exercise: count delivery events in one scenario trace.

These counts describe logged receipts, not verified native GUI performance.
"""

import json
import sys
from pathlib import Path

def count_deliveries(trace_path):
    """Read a trace and return counts, without printing or writing a report."""
    trace_path = Path(trace_path)
    request_events = 0
    timeout_events = 0
    requested_ids = set()
    displayed_ids = set()

    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event["kind"]
        if kind not in {"delivery.requested", "delivery.displayed", "delivery.timeout"}:
            continue
        if not event.get("delivery_id"):
            raise ValueError(f"Line {line_number}: delivery_id is missing")
        key = (event["run_id"], event["scenario_id"], event["delivery_id"])

        if kind == "delivery.requested":
            request_events += 1
            requested_ids.add(key)
        elif kind == "delivery.displayed":
            displayed_ids.add(key)
        elif kind == "delivery.timeout":
            timeout_events += 1

    return {
        "trace_path": str(trace_path),
        "request_events": request_events,
        "unique_deliveries": len(requested_ids),
        "retry_events": request_events - len(requested_ids),
        "displayed_deliveries": len(displayed_ids),
        "timeout_events": timeout_events,
        "without_display_receipt": len(requested_ids - displayed_ids),
    }


def main():
    """Read the command argument, call the function, and save its result."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/d_count_deliveries.py TRACE_PATH")

    trace_path = Path(sys.argv[1])
    metrics = count_deliveries(trace_path)

    for name, value in metrics.items():
        if name != "trace_path":
            print(f"{name}: {value}")

    metrics_path = trace_path.with_name("delivery-metrics.json")
    metrics_path.write_text(
        json.dumps(metrics, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Saved:", metrics_path)


if __name__ == "__main__":
    main()
