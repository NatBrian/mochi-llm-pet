"""Run ``python -m deskpet.m0 --help`` for the independent collaboration bench."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from pydantic import ValidationError
from deskpet.contracts import models
from .composition import ROOT, provenance, resolve_gui, build_observation_environment
from .replay import fixture_paths, load_scenario, run_scenario, run_suite


def schema_bundle() -> dict:
    definitions = {}
    names = []
    for name, cls in vars(models).items():
        if isinstance(cls, type) and issubclass(cls, models.Model) and cls is not models.Model:
            schema = cls.model_json_schema()
            definitions.update(schema.pop("$defs", {}))
            definitions[name] = schema
            names.append(name)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Mochi M0 public contracts", "x-contract-version": models.SCHEMA_VERSION,
            "$defs": definitions, "anyOf": [{"$ref": f"#/$defs/{name}"} for name in names]}


def check_examples() -> dict:
    cases = []
    for path in fixture_paths():
        scenario = load_scenario(path)
        case = run_scenario(scenario)
        cases.append({"scenario": scenario.scenario_id, "status": case["status"], "failures": case["failures"]})
    examples_path = ROOT / "docs/contracts/examples.json"
    validated = 0
    if not examples_path.is_file():
        raise ValueError("docs/contracts/examples.json is missing")
    examples = json.loads(examples_path.read_text(encoding="utf-8"))
    for name, instances in examples["models"].items():
        cls = getattr(models, name, None)
        if not isinstance(cls, type) or not issubclass(cls, models.Model):
            raise ValueError(f"unknown example model: {name}")
        if not isinstance(instances, list) or not instances:
            raise ValueError(f"{name} examples must be a nonempty array")
        for data in instances:
            cls.model_validate(data)
            validated += 1
    schema_path = ROOT / "docs/contracts/schema.json"
    if not schema_path.is_file():
        raise ValueError("schema.json is missing; run the schemas command first")
    if json.loads(schema_path.read_text(encoding="utf-8")) != schema_bundle():
        raise ValueError("schema.json differs from code; regenerate with the schemas command")
    return {"status": "passed" if all(c["status"] == "passed" for c in cases) else "failed",
            "example_instances": validated, "scenarios": cases, "schema": "matches_code"}


def observe_once(args) -> dict:
    """Independent raw-source / estimator check; intentionally no task service."""
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.profile == "live-smoke":
        result = {"status": "unsupported", "profile": args.profile,
                  "reason": "native sampling needs the GUI main thread; use smoke --profile live-smoke",
                  "real_sensors": "not_run", "provenance": provenance()}
    else:
        from deskpet.mocks.clock import FakeClock
        from deskpet.mocks.sampling import BufferedSamplingSource
        raw = models.RawSignals.model_validate_json(Path(args.input).read_text(encoding="utf-8")) if args.input else None
        clock = FakeClock(raw.sampled_at) if raw is not None else FakeClock()
        source = BufferedSamplingSource(clock=clock) if raw is not None else None
        env = build_observation_environment(clock=clock, profile="scenario", adapters=args.adapters,
                                            sampling_source=source, output=output)
        if raw is not None:
            env.observations.submit_sample(raw)
        # Read once before advancing so semantic TTL transitions are observable.
        env.observations.read()
        clock.advance(args.advance)
        env.observations.read()
        frame = env.observations.snapshot()
        result = {"status": "failed" if frame.error or env.trace.sink_errors else "passed",
                  "profile": "scenario", "input_mode": "raw_fixture" if raw is not None else "sampling_source",
                  "real_sensors": "not_run", "clock": "virtual",
                  "sampling_factory_bypassed": raw is not None,
                  "configured_sampling_factory": env.adapters.get("sampling_source"),
                  "adapters": env.adapters,
                  "observation_frame": frame.model_dump(mode="json"),
                  "provenance": provenance(), "sink_errors": env.trace.sink_errors}
    path = output / "observation-report.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Mochi M0: deterministic, offline collaboration workbench")
    commands = root.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help="native Qt workbench; uses scripted policies")
    gui.add_argument("--profile", choices=("scenario", "live-smoke"), default="scenario")
    gui.add_argument("--scenario", default="reminder-flow")
    gui.add_argument("--output", default="output/m0/gui")
    gui.add_argument("--auto-smoke", action="store_true")
    gui.add_argument("--adapters", help="JSON role -> module:factory configuration")
    replay = commands.add_parser("replay", help="headless replay of ordered scenario inputs")
    replay.add_argument("--suite", choices=("m0",), default="m0")
    replay.add_argument("--scenario", help="bundled name or a Scenario JSON path")
    replay.add_argument("--output", default="output/m0/replay")
    replay.add_argument("--adapters")
    observe = commands.add_parser("observe", help="independently inspect raw sampling, estimation and correction boundaries")
    observe.add_argument("--profile", choices=("scenario", "live-smoke"), default="scenario")
    observe.add_argument("--input", help="a complete RawSignals JSON fixture; bypasses the sampling-source factory")
    observe.add_argument("--advance", type=float, default=0, help="advance the virtual clock after the initial sample")
    observe.add_argument("--output", default="output/m0/observation")
    observe.add_argument("--adapters")
    schemas = commands.add_parser("schemas", help="export JSON Schema from public Python models")
    schemas.add_argument("--output", default="docs/contracts/schema.json")
    examples = commands.add_parser("examples", help="validate documentation examples, schemas and scenarios")
    examples.add_argument("--check", action="store_true", help="execute all validation checks (default behavior)")
    smoke = commands.add_parser("smoke", help="scenario checks, or explicitly requested native GUI / OS checks")
    smoke.add_argument("--profile", choices=("scenario", "live-smoke"), default="scenario")
    smoke.add_argument("--native-gui", action="store_true", help="also launch a real native Qt window")
    smoke.add_argument("--output", default="output/m0/smoke")
    smoke.add_argument("--adapters")
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "schemas":
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(schema_bundle(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            result = {"status": "passed", "output": str(path), "contract_version": models.SCHEMA_VERSION}
        elif args.command == "examples":
            result = check_examples()
        elif args.command == "observe":
            result = observe_once(args)
        elif args.command == "replay":
            result = run_suite(scenario=args.scenario, output=args.output, adapters=args.adapters)
        elif args.command == "gui":
            return resolve_gui(args.adapters)(args)
        elif args.command == "smoke":
            if args.profile == "live-smoke" or args.native_gui:
                args.auto_smoke = True
                args.scenario = "reminder-flow"
                return resolve_gui(args.adapters)(args)
            result = run_suite(output=args.output, adapters=args.adapters)
        else:
            raise ValueError("unknown command")
    except (ValueError, ValidationError, OSError, ImportError, AttributeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    display = result
    if args.command in {"replay", "smoke"}:
        display = {"status": result["status"], "case_count": len(result["cases"]),
                   "passed": sum(c["status"] == "passed" for c in result["cases"]),
                   "report": str(Path(args.output) / "report.json"),
                   "source_sha256": result["provenance"]["source_sha256"],
                   "platform_acceptance": result["platform_acceptance"],
                   "failures": [{"scenario":c["scenario_id"], "failures":c["failures"]}
                                for c in result["cases"] if c["status"] != "passed"]}
    elif args.command == "observe":
        frame = result.get("observation_frame", {})
        display = {"status": result["status"], "profile": result["profile"],
                   "report": str(Path(args.output) / "observation-report.json"),
                   "raw_status": frame.get("raw_status"),
                   "state": frame.get("observation", {}).get("estimate", {}).get("state"),
                   "sampling_name": frame.get("sampling_name"), "estimator_name": frame.get("estimator_name"),
                   "reason": result.get("reason")}
    elif args.command == "examples":
        display = {"status": result["status"], "example_instances": result["example_instances"],
                   "scenario_count": len(result["scenarios"]), "schema": result["schema"],
                   "failures": [c for c in result["scenarios"] if c["status"] != "passed"]}
    print(json.dumps(display, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else (2 if result.get("status") == "unsupported" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
