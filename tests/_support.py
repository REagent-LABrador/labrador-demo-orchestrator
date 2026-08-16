"""Temporary five-module registry and fake-process helpers for hermetic tests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

STAGES = (
    ("evidence_mapper", "biomarker"),
    ("hypothesis_generator", "hypothesis"),
    ("clinical_simulation", "recruitability"),
    ("roi_calculator", "roi"),
    ("simulation", "simulation"),
)

VALID_SETUP: dict[str, Any] = {
    "clinicalIndication": "Rheumatoid arthritis",
    "biomarkerRange": [1, 10],
    "maxBiomarkers": 3,
    "maxLiteraturePapers": 40,
    "hypothesisRange": [1, 10],
    "maxHypothesesPerBiomarker": 3,
}

OBJECT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["stage", "visited_stages", "value"],
    "properties": {
        "stage": {"type": "string"},
        "visited_stages": {"type": "array", "items": {"type": "string"}},
        "value": {"type": "integer"},
        "qualifiers": {"type": "array", "items": {"type": "string"}},
        "status": {"type": "string"},
        "simulated_months_to_enroll": {"type": "number"},
        "simulated_months_range": {
            "type": "array",
            "prefixItems": [{"type": "number"}, {"type": "number"}],
            "minItems": 2,
            "maxItems": 2,
        },
        "hypothesis": {"type": "object"},
        "counterfactual": {"type": ["object", "null"]},
        "input": {"type": "object"},
        "request_id": {"type": "string"},
        "payload": {"type": "object"},
        "target": {"type": "object"},
        "verdict": {"type": "string"},
    },
    "additionalProperties": False,
}

FAKE_MODULE_SOURCE = r"""#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--stage", required=True)
parser.add_argument("--behavior", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--trace", required=True)
parser.add_argument("--craziness")
parser.add_argument("--subject", default="IRAK4")
args = parser.parse_args()

trace = Path(args.trace)
trace.parent.mkdir(parents=True, exist_ok=True)

def record(event):
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "stage": args.stage,
            "event": event,
            "monotonic_ns": time.monotonic_ns(),
        }, sort_keys=True) + "\n")

record("start")
if args.behavior == "slow":
    time.sleep(2)
if args.behavior == "exit_nonzero":
    print("intentional fake-module failure", file=sys.stderr)
    sys.exit(7)
if args.behavior == "missing_output":
    record("end")
    sys.exit(0)

input_value = json.loads(Path(args.input).read_text(encoding="utf-8"))
visited = list(input_value.get("visited_stages", [])) if isinstance(input_value, dict) else []
visited.append(args.stage)
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
if args.behavior == "invalid_json":
    output.write_text("{", encoding="utf-8")
elif args.behavior == "invalid_schema":
    output.write_text(json.dumps({"stage": args.stage}), encoding="utf-8")
else:
    payload = {
        "stage": args.stage,
        "visited_stages": visited,
        "value": len(visited),
        "qualifiers": ["NOT_DECISION_GRADE"] if args.stage == "roi_calculator" else [],
    }
    if args.stage in {"evidence_mapper", "roi_calculator"}:
        payload["status"] = "ok"
    if args.stage == "clinical_simulation":
        payload["simulated_months_to_enroll"] = 24
        payload["simulated_months_range"] = [18, 30]
        payload["input"] = input_value
        if args.behavior == "null_counterfactual":
            payload["counterfactual"] = None
    if args.stage == "hypothesis_generator" and args.behavior != "no_candidate":
        payload["hypothesis"] = {
            "subject_name": args.subject,
            "object_name": "test mechanism",
            "scores": {"support": 0.8},
            "rank_score": 0.75,
            "caveats": [],
            "provenance": "hermetic contract candidate",
        }
    if args.stage == "roi_calculator":
        payload["request_id"] = str(input_value.get("request_id", "test-request"))
        program = input_value.get("program", {})
        payload["payload"] = {
            "summary": {"program_id": program.get("program_id")},
            "decision_grade": "NOT_DECISION_GRADE",
        }
    if args.stage == "simulation":
        payload["input"] = input_value
        payload["target"] = {"uniprot_accession": input_value.get("uniprot_accession")}
        payload["verdict"] = "insufficient_evidence"
    output.write_text(json.dumps(payload), encoding="utf-8")
record("end")
"""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class FixtureProject:
    """Own a disposable orchestrator root with five valid module contracts."""

    def __init__(
        self,
        *,
        behaviors: dict[str, str] | None = None,
        modes: dict[str, str] | None = None,
        timeouts: dict[str, float] | None = None,
        subjects: dict[str, str] | None = None,
    ) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="labrador-orchestrator-test-")
        self.root = Path(self._temporary.name)
        self.behaviors = behaviors or {}
        self.modes = modes or {}
        self.timeouts = timeouts or {}
        self.subjects = subjects or {}
        self.trace_path = self.root / "trace.ndjson"
        (self.root / "runs").mkdir()
        (self.root / "ui").mkdir()
        (self.root / "ui" / "index.html").write_text(
            '<!doctype html><section data-screen="setup"></section>'
            '<section data-screen="graph"></section>'
            '<section data-screen="highlander"></section>',
            encoding="utf-8",
        )
        fake_module = self.root / "fake_module.py"
        fake_module.write_text(FAKE_MODULE_SOURCE, encoding="utf-8")
        fake_module.chmod(0o755)
        write_json(self.root / "fixtures" / "golden" / "evidence.request.json", {"max_papers": 40})
        write_json(
            self.root / "fixtures" / "golden" / "clinical-input.json",
            {
                "target": {"uniprot_accession": "Q9NWZ3"},
                "disease": {"name": "Rheumatoid arthritis"},
            },
        )
        write_json(
            self.root / "fixtures" / "golden" / "simulation-input.json",
            {
                "uniprot_accession": "Q9NWZ3",
                "as_of_date": None,
                "disease_context": "Rheumatoid arthritis",
                "interaction_to_disrupt": "IRAK4 ATP site",
                "mechanism_hypothesis": "orthosteric",
            },
        )
        write_json(
            self.root / "fixtures" / "golden" / "roi-input-template.json",
            {
                "request_id": "template",
                "program": {
                    "assumptions": {},
                    "initial_indication": {"assumptions": {}},
                },
                "execution": {
                    "simulation_assumptions": {
                        "launch_delay_years": {"low": 0, "mode": 0, "high": 0}
                    }
                },
            },
        )

        modules: list[dict[str, Any]] = []
        for order, (module_id, ui_stage) in enumerate(STAGES, start=1):
            module_root = self.root / "modules" / module_id
            module_root.mkdir(parents=True)
            write_json(module_root / "input.schema.json", OBJECT_SCHEMA)
            write_json(module_root / "output.schema.json", OUTPUT_SCHEMA)
            write_json(module_root / "example-input.json", {"visited_stages": []})
            example = {
                "stage": module_id,
                "visited_stages": [module_id],
                "value": 1,
                "qualifiers": ["NOT_DECISION_GRADE"] if module_id == "roi_calculator" else [],
            }
            if module_id in {"evidence_mapper", "roi_calculator"}:
                example["status"] = "ok"
            if module_id == "clinical_simulation":
                example["simulated_months_to_enroll"] = 24
                example["simulated_months_range"] = [18, 30]
            if module_id == "hypothesis_generator":
                example["hypothesis"] = {
                    "subject_name": "TEST_TARGET",
                    "object_name": "test mechanism",
                    "scores": {"support": 0.8},
                    "rank_score": 0.75,
                    "caveats": [],
                    "provenance": "hermetic contract candidate",
                }
            write_json(module_root / "example-output.json", example)
            write_json(module_root / "fallback-output.json", example)
            behavior = self.behaviors.get(module_id, "ok")
            modules.append(
                {
                    "id": module_id,
                    "ui_stage": ui_stage,
                    "order": order,
                    "repository": f"test/{module_id}",
                    "repository_url": f"https://example.invalid/{module_id}.git",
                    "commit": "a" * 40,
                    "module_root": f"modules/{module_id}",
                    "mode": self.modes.get(module_id, "auto"),
                    "setup_command": [],
                    "command": [
                        sys.executable,
                        "{orchestrator_root}/fake_module.py",
                        "--stage",
                        module_id,
                        "--behavior",
                        behavior,
                        "--input",
                        "{input}",
                        "--output",
                        "{output}",
                        "--trace",
                        "{orchestrator_root}/trace.ndjson",
                        "--subject",
                        self.subjects.get(module_id, "IRAK4"),
                    ],
                    "timeout_seconds": self.timeouts.get(module_id, 1.0),
                    "input_schema": f"modules/{module_id}/input.schema.json",
                    "output_schema": f"modules/{module_id}/output.schema.json",
                    "example_input": f"modules/{module_id}/example-input.json",
                    "example_output": f"modules/{module_id}/example-output.json",
                    "fallback_output": f"modules/{module_id}/fallback-output.json",
                    "runtime_maturity": "LOCAL",
                    "result_basis": ["MODELED"],
                    "qualifiers": ["NOT_DECISION_GRADE"] if module_id == "roi_calculator" else [],
                }
            )
        self.registry_json: dict[str, Any] = {
            "registry_version": "1.0",
            "golden_program": {
                "indication": "Rheumatoid arthritis",
                "target": "IRAK4",
                "uniprot_accession": "Q9NWZ3",
                "modality": "small_molecule",
            },
            "modules": modules,
        }
        self.flush_registry()

    def flush_registry(self) -> None:
        write_json(self.root / "module-lock.json", self.registry_json)

    def module_entry(self, module_id: str) -> dict[str, Any]:
        return next(module for module in self.registry_json["modules"] if module["id"] == module_id)

    def read_trace(self) -> list[dict[str, Any]]:
        if not self.trace_path.exists():
            return []
        return [
            json.loads(line) for line in self.trace_path.read_text(encoding="utf-8").splitlines()
        ]

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> FixtureProject:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
