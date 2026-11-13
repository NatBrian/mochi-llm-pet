"""Regression checks for D's offline evidence validation.

These tests construct temporary traces and reports. They do not call models,
open Qt windows, or change existing experiment outputs.
"""

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

SCRIPTS_PATH = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))

from d_count_deliveries import count_deliveries
from d_summarize_suite import summarize_suite
from d_run_evidence import finish_manifest, save_manifest, start_manifest


class DEvidenceValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario_id = "d-evidence-check"

    def make_events(self, display_id="delivery-A", failed_count=0):
        facts = [("scenario.input", None)]
        for _ in range(failed_count):
            facts.extend([
                ("delivery.requested", "delivery-A"),
                ("delivery.failed", "delivery-A"),
            ])
        facts.extend([
            ("delivery.requested", "delivery-A"),
            ("delivery.displayed", display_id),
        ])
        events = []
        for seq, (kind, delivery_id) in enumerate(facts, start=1):
            events.append({
                "schema_version": 1,
                "seq": seq,
                "ts": f"2026-01-01T00:00:0{seq}Z",
                "monotonic_s": float(seq),
                "run_id": "d-test-run",
                "scenario_id": self.scenario_id,
                "kind": kind,
                "source": "test-fixture",
                "simulated": True,
                "clock": "virtual",
                "task_id": "water" if delivery_id else None,
                "task_version": 2 if delivery_id else None,
                "delivery_id": delivery_id,
                "payload": {},
            })
        return events

    def write_trace(self, events):
        path = self.root / self.scenario_id / "trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        return path

    def write_suite(self, events):
        self.write_trace(events)
        report = {
            "profile": "scenario",
            "status": "passed",
            "provenance": {},
            "cases": [{
                "scenario_id": self.scenario_id,
                "status": "passed",
                "failures": [],
                "clock": "virtual",
                "receipt_source": "simulated",
                "outcomes": {"delivery_requests": 1, "displays": 1},
                "metrics": {
                    "event_counts": dict(Counter(event["kind"] for event in events)),
                    "total_events": len(events),
                },
            }],
        }
        (self.root / "report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    def test_valid_trace_passes(self):
        self.write_suite(self.make_events())
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["metrics_case_count"], 1)

    def test_orphan_display_receipt_is_rejected(self):
        self.write_suite(self.make_events(display_id="delivery-B"))
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["cases"][0]["evidence_errors"])
        metrics = count_deliveries(self.root / self.scenario_id / "trace.jsonl")
        self.assertEqual(metrics["displayed_deliveries"], 0)

    def test_changed_event_kind_is_rejected(self):
        events = self.make_events()
        self.write_suite(events)
        events[0]["kind"] = "unexpected.event"
        self.write_trace(events)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["cases"][0]["evidence_errors"])

    def test_total_event_count_mismatch_is_rejected(self):
        self.write_suite(self.make_events())
        path = self.root / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["cases"][0]["metrics"]["total_events"] += 1
        path.write_text(json.dumps(report), encoding="utf-8")
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["cases"][0]["evidence_errors"])

    def test_no_failed_events_is_zero(self):
        self.write_suite(self.make_events())
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["trace_count_totals"]["failed_events"], 0)

    def test_failure_then_recovery_keeps_both_results(self):
        self.write_suite(self.make_events(failed_count=1))
        metrics = count_deliveries(self.root / self.scenario_id / "trace.jsonl")
        self.assertEqual(metrics["failed_events"], 1)
        self.assertEqual(metrics["retry_events"], 1)
        self.assertEqual(metrics["displayed_deliveries"], 1)
        self.assertEqual(metrics["without_display_receipt"], 0)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["trace_count_totals"]["failed_events"], 1)

    def test_multiple_failures_are_counted_per_event(self):
        self.write_suite(self.make_events(failed_count=2))
        metrics = count_deliveries(self.root / self.scenario_id / "trace.jsonl")
        self.assertEqual(metrics["failed_events"], 2)
        self.assertEqual(metrics["unique_deliveries"], 1)
        self.assertEqual(metrics["retry_events"], 2)
        self.assertEqual(metrics["displayed_deliveries"], 1)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["trace_count_totals"]["failed_events"], 2)

    def record_fixture_manifest(self):
        """Bind a synthetic test report to temporary source snapshots."""
        self.write_suite(self.make_events())
        case_dir = self.root / self.scenario_id
        report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        (case_dir / "report.json").write_text(
            json.dumps(report["cases"][0]), encoding="utf-8"
        )
        (case_dir / "d-sink-info.json").write_text("{}", encoding="utf-8")
        source = self.root / "fake-source"
        names = (
            "d_metrics.py", "d-adapters.json", "d-cancel-before-due.json",
            "scripts/d_count_deliveries.py", "scripts/d_summarize_suite.py",
            "scripts/d_replay.py", "scripts/d_run_evidence.py",
            "tests/test_d_evaluation.py", "fixtures/selected.json",
        )
        for name in names:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original input\n", encoding="utf-8")
        manifest = start_manifest(
            source, self.root, [source / name for name in names],
            {self.scenario_id: "fixtures/selected.json"}, "d-adapters.json",
        )
        finish_manifest(source, self.root, manifest)
        return source, manifest

    def test_legacy_trace_has_unknown_generating_version(self):
        self.write_suite(self.make_events())
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["run_provenance"]["status"], "unavailable")
        self.assertFalse(summary["run_provenance"]["version_bound"])

    def test_saved_manifest_verifies_without_current_source_files(self):
        source, manifest = self.record_fixture_manifest()
        # A later working-tree edit must not relabel or invalidate the old run.
        (source / "d_metrics.py").write_text("new code", encoding="utf-8")
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "passed")
        provenance = summary["run_provenance"]
        self.assertEqual(provenance["status"], "verified")
        self.assertTrue(provenance["version_bound"])
        self.assertEqual(provenance["input_sha256"], manifest["input_sha256"])

    def test_modified_input_snapshot_is_rejected(self):
        self.record_fixture_manifest()
        (self.root / "inputs/d_metrics.py").write_text("changed", encoding="utf-8")
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(any("INPUT_SNAPSHOT_MISMATCH" in error
                            for error in summary["run_provenance"]["errors"]))

    def test_modified_trace_payload_is_rejected_even_with_same_counts(self):
        self.record_fixture_manifest()
        events = self.make_events()
        events[0]["payload"] = {"tampered": True}
        self.write_trace(events)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["evidence_gap_cases"], 0)
        self.assertTrue(any("ARTIFACT_HASH_MISMATCH" in error
                            for error in summary["run_provenance"]["errors"]))

    def test_modified_replay_report_is_rejected(self):
        self.record_fixture_manifest()
        path = self.root / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["provenance"]["commit"] = "different-commit"
        path.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(summarize_suite(self.root)["status"], "failed")

    def test_missing_artifact_hash_is_rejected(self):
        _, manifest = self.record_fixture_manifest()
        del manifest["artifacts"][f"{self.scenario_id}/trace.jsonl"]
        save_manifest(self.root, manifest)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("ARTIFACT_SET_MISMATCH", summary["run_provenance"]["errors"])

    def test_incomplete_run_is_rejected(self):
        _, manifest = self.record_fixture_manifest()
        manifest["state"] = "running"
        save_manifest(self.root, manifest)
        self.assertEqual(summarize_suite(self.root)["status"], "failed")

    def test_source_change_during_run_is_rejected(self):
        source, manifest = self.record_fixture_manifest()
        (source / "d_metrics.py").write_text("changed during replay", encoding="utf-8")
        finish_manifest(source, self.root, manifest)
        summary = summarize_suite(self.root)
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(any("INPUT_CHANGED_DURING_RUN" in error
                            for error in summary["run_provenance"]["errors"]))


if __name__ == "__main__":
    unittest.main()
