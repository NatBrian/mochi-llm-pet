"""Summarize declared synthetic replay cases; preserve evidence gaps.

Run from the repository root:
    python scripts/d_summarize_suite.py output/m0/d-suite-v1
"""

import json
import sys
from hashlib import sha256
from pathlib import Path

from d_count_deliveries import count_deliveries


COUNT_FIELDS = (
    "request_events", "unique_deliveries", "retry_events",
    "displayed_deliveries", "timeout_events", "without_display_receipt",
)


def summarize_suite(suite_path):
    suite_path = Path(suite_path)
    suite = json.loads((suite_path / "report.json").read_text(encoding="utf-8-sig"))
    if suite.get("profile") != "scenario":
        raise ValueError("This learning tool only summarizes scenario replay suites")
    cases = suite["cases"]
    if not cases:
        raise ValueError("The suite contains no cases")

    rows = []
    seen = set()
    totals = dict.fromkeys(COUNT_FIELDS, 0)
    metrics_case_count = 0
    for case in cases:
        scenario_id = case["scenario_id"]
        if (not scenario_id or scenario_id in {".", ".."}
                or "/" in scenario_id or "\\" in scenario_id or scenario_id in seen):
            raise ValueError(f"Invalid or repeated scenario ID: {scenario_id!r}")
        seen.add(scenario_id)
        trace_path = suite_path / scenario_id / "trace.jsonl"
        row = {
            "scenario_id": scenario_id,
            "case_status": case["status"],
            "case_failures": case.get("failures", []),
            "evidence_errors": [],
            "metrics": None,
        }
        try:
            text = trace_path.read_text(encoding="utf-8-sig")
            events = [json.loads(line) for line in text.splitlines() if line.strip()]
            if not events:
                raise ValueError("Trace is empty")
            if any(event["scenario_id"] != scenario_id for event in events):
                raise ValueError("Trace belongs to another scenario")
            if len({event["run_id"] for event in events}) != 1:
                raise ValueError("Trace mixes multiple runs")
            if [event["seq"] for event in events] != list(range(1, len(events) + 1)):
                raise ValueError("Trace sequence is missing, duplicated or out of order")

            metrics = count_deliveries(trace_path)
            row["metrics"] = metrics
            row["trace_sha256"] = sha256(text.encode("utf-8")).hexdigest()
            row["trace_event_count"] = len(events)
            for metric_name, outcome_name in (
                ("unique_deliveries", "delivery_requests"),
                ("displayed_deliveries", "displays"),
            ):
                expected = case["outcomes"][outcome_name]
                if metrics[metric_name] != expected:
                    row["evidence_errors"].append(
                        f"{metric_name}={metrics[metric_name]} differs from report {outcome_name}={expected}"
                    )
        except (OSError, ValueError, KeyError, TypeError) as error:
            row["evidence_errors"].append(f"{type(error).__name__}: {error}")

        if row["metrics"] is not None and not row["evidence_errors"]:
            metrics_case_count += 1
            for name in COUNT_FIELDS:
                totals[name] += row["metrics"][name]
        rows.append(row)

    valid = (suite.get("status") == "passed"
             and all(row["case_status"] == "passed" and not row["case_failures"]
                     and not row["evidence_errors"] for row in rows))
    scripts_path = Path(__file__).resolve().parent
    analysis_hashes = {
        name: sha256((scripts_path / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
        for name in ("d_count_deliveries.py", "d_summarize_suite.py")
    }
    return {
        "status": "passed" if valid else "failed",
        "evidence_type": "synthetic_replay",
        "scenario_count": len(rows),
        "passed_cases": sum(row["case_status"] == "passed" for row in rows),
        "failed_cases": sum(row["case_status"] != "passed" for row in rows),
        "metrics_case_count": metrics_case_count,
        "evidence_gap_cases": sum(bool(row["evidence_errors"]) for row in rows),
        "trace_count_totals": totals,
        "real_latency": "not_measured",
        "real_model_cost": "not_measured",
        "base_source_sha256": suite.get("provenance", {}).get("source_sha256"),
        "analysis_code_sha256": analysis_hashes,
        "platform_acceptance": suite.get("platform_acceptance", {}),
        "cases": rows,
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/d_summarize_suite.py SUITE_DIRECTORY")
    suite_path = Path(sys.argv[1])
    summary = summarize_suite(suite_path)
    summary_path = suite_path / "delivery-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "status", "scenario_count", "passed_cases", "failed_cases",
        "metrics_case_count", "evidence_gap_cases", "trace_count_totals",
    )}, indent=2))
    print("Saved:", summary_path)
    if summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
