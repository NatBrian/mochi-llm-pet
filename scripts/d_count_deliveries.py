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
    failed_events = 0
    requested_ids = set()
    displayed_ids = set()
    requests = {}
    evidence_errors = []

    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event["kind"]
        if kind not in {
            "delivery.requested", "delivery.displayed", "delivery.timeout", "delivery.failed"
        }:
            continue
        if not event.get("delivery_id"):
            raise ValueError(f"Line {line_number}: delivery_id is missing")
        key = (event["run_id"], event["scenario_id"], event["delivery_id"])
        identity = (event.get("task_id"), event.get("task_version"))
        seq = event.get("seq")

        if kind == "delivery.requested":
            request_events += 1
            requested_ids.add(key)
            if (not isinstance(identity[0], str) or not identity[0]
                    or type(identity[1]) is not int or identity[1] < 1
                    or type(seq) is not int or seq < 1):
                evidence_errors.append(
                    f"INVALID_DELIVERY_REQUEST at line {line_number}: "
                    "task_id, positive task_version and seq are required"
                )
                continue
            if key in requests:
                if requests[key][0] != identity:
                    evidence_errors.append(
                        f"DELIVERY_IDENTITY_CONFLICT at seq {seq}: "
                        f"{event['delivery_id']} was reused for another task or version"
                    )
            else:
                requests[key] = (identity, seq)
        elif kind == "delivery.displayed":
            request = requests.get(key)
            if request is None:
                evidence_errors.append(
                    f"DISPLAY_WITHOUT_PRIOR_REQUEST at seq {seq}: "
                    f"{event['delivery_id']} has no earlier valid request"
                )
                continue
            if request[0] != identity:
                evidence_errors.append(
                    f"DISPLAY_TASK_MISMATCH at seq {seq}: "
                    f"{event['delivery_id']} has a different task_id or task_version"
                )
                continue
            if type(seq) is not int or seq <= request[1]:
                evidence_errors.append(
                    f"DISPLAY_ORDER_MISMATCH at line {line_number}: "
                    f"{event['delivery_id']} must follow its request"
                )
                continue
            displayed_ids.add(key)
        elif kind == "delivery.timeout":
            timeout_events += 1
        elif kind == "delivery.failed":
            failed_events += 1

    return {
        "trace_path": str(trace_path),
        "request_events": request_events,
        "unique_deliveries": len(requested_ids),
        "retry_events": request_events - len(requested_ids),
        "displayed_deliveries": len(displayed_ids),
        "timeout_events": timeout_events,
        "without_display_receipt": len(requested_ids - displayed_ids),
        "failed_events": failed_events,
        "evidence_errors": evidence_errors,
    }


def main():
    """Read the command argument, call the function, and save its result."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/d_count_deliveries.py TRACE_PATH")

    trace_path = Path(sys.argv[1])
    metrics = count_deliveries(trace_path)

    for name, value in metrics.items():
        if name not in {"trace_path", "evidence_errors"}:
            print(f"{name}: {value}")
    if metrics["evidence_errors"]:
        print("evidence_errors:", json.dumps(metrics["evidence_errors"]))

    metrics_path = trace_path.with_name("delivery-metrics.json")
    metrics_path.write_text(
        json.dumps(metrics, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Saved:", metrics_path)
    if metrics["evidence_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
