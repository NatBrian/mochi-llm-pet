"""D's offline replay entry point with run-time source and artifact evidence.

Use a NEW output directory for every run. Original M0 ports are unchanged.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d_run_evidence import finish_manifest, save_manifest, start_manifest
from d_summarize_suite import summarize_suite


D_FILES = (
    "d_metrics.py", "d-cancel-before-due.json",
    "scripts/d_count_deliveries.py", "scripts/d_summarize_suite.py",
    "scripts/d_replay.py", "scripts/d_run_evidence.py",
    "tests/test_d_evaluation.py",
)


def run_recorded_replay(*, output, scenario=None, adapters="d-adapters.json"):
    from deskpet.m0.composition import load_adapters, provenance
    from deskpet.m0.replay import fixture_paths, load_scenario, run_suite

    # This first D runner records the known D sink, with M0's default modules.
    # Other adapters require adding their implementation files to this manifest.
    adapter_path = Path(adapters).resolve()
    if load_adapters(adapter_path) != {"metrics": "d_metrics:factory"}:
        raise ValueError("This D runner requires only metrics=d_metrics:factory")
    paths = fixture_paths(scenario)
    scenarios = {}
    for path in paths:
        scenario_id = load_scenario(path).scenario_id
        if (not scenario_id or scenario_id in {".", "..", "inputs"}
                or "/" in scenario_id or "\\" in scenario_id or scenario_id in scenarios):
            raise ValueError(f"Unsafe or duplicated scenario ID: {scenario_id!r}")
        scenarios[scenario_id] = path.resolve().relative_to(ROOT).as_posix()
    if not scenarios:
        raise ValueError("No scenarios were selected")
    baseline = provenance()
    files = [ROOT / name for name in baseline["file_hashes"]]
    files.extend(ROOT / name for name in D_FILES)
    files.extend(paths)
    files.append(adapter_path)
    adapter_name = adapter_path.relative_to(ROOT).as_posix()
    # Preflight missing inputs before creating a run directory.
    for path in files:
        if not path.is_file():
            raise ValueError(f"Required input file is missing: {path}")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = start_manifest(ROOT, output, files, scenarios, adapter_name)
    try:
        run_suite(scenario=scenario, output=output, adapters=adapter_path)
        finish_manifest(ROOT, output, manifest)
    except Exception as error:
        manifest["state"] = "failed"
        manifest["errors"].append(f"REPLAY_ERROR: {type(error).__name__}: {error}")
        save_manifest(output, manifest)
        raise
    summary = summarize_suite(output)
    (output / "delivery-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="Scenario name or JSON path; omit for all M0 cases")
    parser.add_argument("--adapters", default="d-adapters.json")
    parser.add_argument("--output", required=True, help="New output directory (must not exist)")
    args = parser.parse_args()
    try:
        summary = run_recorded_replay(**vars(args))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"D replay failed: {error}\n")
    print(json.dumps({key: summary[key] for key in (
        "status", "scenario_count", "passed_cases", "failed_cases",
        "metrics_case_count", "evidence_gap_cases", "trace_count_totals", "run_provenance",
    )}, ensure_ascii=False, indent=2))
    print("Saved:", Path(args.output) / "delivery-summary.json")
    if summary["status"] != "passed" or not summary["run_provenance"]["version_bound"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
