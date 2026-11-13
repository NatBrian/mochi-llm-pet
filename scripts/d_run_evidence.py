"""Save and check file evidence for D's recorded offline replay runs.

Hashes describe saved bytes. They detect accidental edits, not hostile rewriting
of both an artifact and its manifest. They do not establish native GUI behavior.
"""

import json
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


MANIFEST_NAME = "d-run-manifest.json"


def file_sha256(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def input_fingerprint(inputs):
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def save_manifest(output, manifest):
    (Path(output) / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def git_info(root):
    def command(*args):
        return subprocess.run(
            ["git", f"--git-dir={root / '.git'}", f"--work-tree={root}", *args],
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    try:
        commit = command("rev-parse", "HEAD")
        status = command("status", "--porcelain", "--untracked-files=normal")
        return {"commit": commit, "dirty": bool(status), "status": status}
    except (OSError, subprocess.SubprocessError) as error:
        return {"commit": None, "dirty": None, "error": str(error)}


def start_manifest(root, output, files, scenarios, adapters):
    """Snapshot declared inputs before replay; output must be a fresh directory."""
    root, output = Path(root).resolve(), Path(output).resolve()
    git_state = git_info(root)
    inputs = {}
    for source in sorted({Path(path).resolve() for path in files}):
        name = source.relative_to(root).as_posix()
        data = source.read_bytes()
        snapshot = output / "inputs" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
        inputs[name] = sha256(data).hexdigest()
    manifest = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git": git_state,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hash_algorithm": "SHA256 of exact file bytes (no newline normalization)",
        "inputs": inputs,
        "input_sha256": input_fingerprint(inputs),
        "scenarios": scenarios,
        "adapters": adapters,
        "artifacts": {},
        "errors": [],
    }
    save_manifest(output, manifest)
    return manifest


def finish_manifest(root, output, manifest):
    """Detect input changes during replay and bind its reports/traces."""
    root, output = Path(root), Path(output)
    for name, expected in manifest["inputs"].items():
        try:
            if file_sha256(root / name) != expected:
                manifest["errors"].append(f"INPUT_CHANGED_DURING_RUN: {name}")
        except OSError as error:
            manifest["errors"].append(f"INPUT_UNREADABLE: {name}: {error}")
    names = ["report.json"]
    for scenario_id in manifest["scenarios"]:
        names.extend(f"{scenario_id}/{name}" for name in (
            "report.json", "trace.jsonl", "d-sink-info.json"
        ))
    for name in names:
        try:
            manifest["artifacts"][name] = file_sha256(output / name)
        except OSError as error:
            manifest["errors"].append(f"ARTIFACT_UNREADABLE: {name}: {error}")
    manifest["state"] = "failed" if manifest["errors"] else "completed"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_manifest(output, manifest)


def _safe_path(base, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Evidence paths must use nonempty relative POSIX paths")
    base = Path(base).resolve()
    path = (base / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(base) or path == base:
        raise ValueError(f"Unsafe evidence path: {name!r}")
    return path


def check_run_manifest(output, scenario_ids):
    """Check saved evidence, never use current working files to date old logs."""
    output = Path(output)
    path = output / MANIFEST_NAME
    if not path.exists():
        return {"status": "unavailable", "version_bound": False,
                "reason": "No run-time manifest; generating versions are unknown",
                "errors": []}
    errors = []
    result = {"status": "failed", "version_bound": False, "errors": errors}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        result.update({"run_id": manifest["run_id"], "git": manifest["git"],
                       "input_sha256": manifest["input_sha256"],
                       "manifest_sha256": file_sha256(path)})
        if manifest["schema_version"] != 1 or manifest["state"] != "completed":
            errors.append("RUN_NOT_COMPLETED: unsupported schema or incomplete run")
        errors.extend(manifest["errors"])
        inputs = manifest["inputs"]
        if not isinstance(inputs, dict) or not inputs:
            raise ValueError("Manifest has no input file hashes")
        if input_fingerprint(inputs) != manifest["input_sha256"]:
            errors.append("INPUT_FINGERPRINT_MISMATCH")
        required_inputs = {"d_metrics.py", "scripts/d_count_deliveries.py",
                           "scripts/d_summarize_suite.py", "scripts/d_replay.py",
                           "scripts/d_run_evidence.py", "tests/test_d_evaluation.py",
                           "d-cancel-before-due.json", manifest["adapters"]}
        scenarios = manifest["scenarios"]
        if set(scenarios) != set(scenario_ids):
            errors.append("SCENARIO_SET_MISMATCH")
        required_inputs.update(scenarios.values())
        if required_inputs - inputs.keys():
            errors.append("REQUIRED_INPUTS_MISSING")
        for name, expected in inputs.items():
            if file_sha256(_safe_path(output / "inputs", name)) != expected:
                errors.append(f"INPUT_SNAPSHOT_MISMATCH: {name}")
        artifacts = manifest["artifacts"]
        required_artifacts = {"report.json"}
        for scenario_id in scenario_ids:
            required_artifacts.update(f"{scenario_id}/{name}" for name in (
                "report.json", "trace.jsonl", "d-sink-info.json"
            ))
        if set(artifacts) != required_artifacts:
            errors.append("ARTIFACT_SET_MISMATCH")
        for name, expected in artifacts.items():
            if file_sha256(_safe_path(output, name)) != expected:
                errors.append(f"ARTIFACT_HASH_MISMATCH: {name}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f"MANIFEST_ERROR: {type(error).__name__}: {error}")
    if not errors:
        result.update(status="verified", version_bound=True)
    return result
