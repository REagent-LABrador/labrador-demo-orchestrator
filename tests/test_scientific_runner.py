from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.scientific_runner import select_focus_nodes
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import OBJECT_SCHEMA, FixtureProject, write_json
from tests.test_api import RunningServer

FAKE_SCIENTIFIC_SOURCE = r'''#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--stage", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--fail-focus")
parser.add_argument("--sleep", action="store_true")
args = parser.parse_args()
value = json.loads(Path(args.input).read_text(encoding="utf-8"))
if args.sleep:
    time.sleep(0.2)

if args.stage == "evidence_mapper":
    output = {
        "status": "ok",
        "question": value.get("target"),
        "graph_id": "graph-three-focuses",
        "things": [
            {"id": "b1", "kind": "biomarker", "name": "Marker one", "mentions": 2},
            {"id": "b2", "kind": "biomarker", "name": "Marker two", "mentions": 1},
            {"id": "p1", "kind": "process", "name": "Pathway readout", "mentions": 3},
            {"id": "p2", "kind": "process", "name": "Unsupported process", "mentions": 9},
        ],
        "papers": [{"id": "paper-1", "pmid": "12345"}],
        "findings": [
            {
                "id": "f1", "from": "b1", "to": "p1", "says": "yes",
                "claim": "one", "confidence": 0.8, "paper": "paper-1",
            },
            {
                "id": "f2", "from": "b2", "to": "p1", "says": "yes",
                "claim": "two", "confidence": 0.7, "paper": "paper-1",
            },
        ],
        "links": [
            {"id": "l1", "from": "b1", "to": "p1", "yes": ["f1"]},
            {"id": "l2", "from": "b2", "to": "p1", "yes": ["f2"]},
        ],
    }
elif args.stage == "hypothesis_generator":
    focus = value["focus_thing_id"]
    if args.fail_focus == focus:
        Path(args.output).write_text(json.dumps({
            "status": "CANNOT_COMPLETE",
            "reason_code": "CREDENTIAL_MISSING",
            "message": "ANTHROPIC_API_KEY is missing",
        }), encoding="utf-8")
        sys.exit(2)
    document = {
        "schema_version": "2.0",
        "provenance": {"graph_id": value["graph"]["graph_id"]},
        "hypothesis": {
            "id": "H-" + focus,
            "motif": "gap_closure",
            "subject": focus,
            "object": "p1",
            "subject_name": focus,
            "object_name": "Pathway readout",
            "hops": 1,
            "articulation": {
                "statement": focus + " predicts a pathway change",
                "mechanism": focus + " changes the pathway",
                "claims": [],
                "novel_because": "test",
                "falsifier": "no change",
                "decisive_experiment": "measure it",
            },
        },
        "asks": [],
    }
    output = {
        "status": "COMPLETE",
        "execution_mode": "REPLAY",
        "output_origin": "DETERMINISTIC_REPLAY",
        "hypothesis": document,
        "cards": {"interpretability": {}},
        "roi_request": {
            "request_id": value["roi"]["request_id"],
            "program": {"program_id": "P-" + focus},
        },
        "error": None,
    }
elif args.stage == "clinical_simulation":
    output = {
        "status": "ok",
        "simulated_months_to_enroll": 20,
        "simulated_months_range": [18, 24],
        "input": value,
    }
elif args.stage == "roi_calculator":
    output = {"status": "ok", "request_id": value["request_id"], "payload": value}
else:
    output = {
        "status": "ok",
        "input": value,
        "target": {"uniprot_accession": value["uniprot_accession"]},
        "verdict": "tractable",
    }
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
'''


def scientific_request(*, presentation: str = "SCIENTIFIC", mode: str = "REPLAY") -> dict:
    return {
        "schemaVersion": "labrador.run-setup.v3",
        "execution": {"mode": mode, "presentationMode": presentation},
        "exploration": {
            "evidenceRequest": {
                "ask": "new_question",
                "target": "Can target inhibition change disease biology?",
                "depth": "standard",
            },
            "focus": {"maxBranches": 3},
            "hypothesis": {
                "profile": "default",
                "roi": {
                    "requestId": "scientific-roi",
                    "comparables": [],
                    "execution": {
                        "simulations": 128,
                        "seed": 42,
                        "simulationAssumptions": {},
                    },
                },
            },
        },
        "program": {
            "frame": {
                "schemaVersion": "labrador.scientific-program-frame.v1",
                "frameId": "program-1",
                "basis": "ANALYST_SUPPLIED",
                "asset": {
                    "name": "Target inhibitor",
                    "modality": "small_molecule",
                    "sponsor": None,
                },
                "target": {
                    "symbol": "IRAK4",
                    "direction": "inhibit",
                    "uniprotAccession": "Q9NWZ3",
                },
                "disease": {"name": "Rheumatoid arthritis", "subtype": None},
                "biomarkerDefaults": {
                    "prevalenceInDisease": 0.4,
                    "assayAvailable": True,
                },
                "endpoint": {
                    "name": "ACR50",
                    "type": "binary",
                    "expectedEffectSize": 0.3,
                },
                "tissue": "synovium",
                "simulationContext": {
                    "interactionToDisrupt": "IRAK4 catalytic function",
                    "mechanismHypothesis": "orthosteric",
                    "asOfDate": None,
                },
                "notes": [],
            },
            "valuationFrame": {
                "base_year": 2026,
                "valuation_year": 2026,
                "launch_year": 2034,
                "filing_year": 2026,
                "currency": "USD",
                "geography": "United States",
                "therapeutic_area": "Immunology",
                "target_population": "Adults with active RA",
                "line_of_therapy": "Second line",
                "route": "ORAL",
                "current_stage": "Preclinical",
                "modality": "SMALL_MOLECULE",
                "target": "IRAK4",
                "expansion_launch_year": None,
                "notes": "Analyst-supplied valuation frame",
            },
        },
    }


def configure_scientific_fixture(
    fixture: FixtureProject, *, fail_focus: str | None = None, slow_simulation: bool = False
) -> None:
    script = fixture.root / "fake_scientific.py"
    script.write_text(FAKE_SCIENTIFIC_SOURCE, encoding="utf-8")
    for module in fixture.registry_json["modules"]:
        module_root = fixture.root / module["module_root"]
        write_json(module_root / "input.schema.json", OBJECT_SCHEMA)
        write_json(module_root / "output.schema.json", OBJECT_SCHEMA)
        command = [
            sys.executable,
            "{orchestrator_root}/fake_scientific.py",
            "--stage",
            module["id"],
            "--input",
            "{input}",
            "--output",
            "{output}",
        ]
        if fail_focus is not None and module["id"] == "hypothesis_generator":
            command.extend(["--fail-focus", fail_focus])
        if slow_simulation and module["id"] == "simulation":
            command.append("--sleep")
            module["timeout_seconds"] = 0.02
        module["live_command"] = command
        module["replay_command"] = command
    fixture.flush_registry()


class ScientificRunnerTests(unittest.TestCase):
    def test_focus_selection_uses_real_biomarkers_then_supported_processes(self) -> None:
        graph = {
            "things": [
                {"id": "b", "kind": "biomarker", "name": "Biomarker", "mentions": 1},
                {"id": "p", "kind": "process", "name": "Process", "mentions": 3},
                {"id": "u", "kind": "process", "name": "Unsupported", "mentions": 9},
            ],
            "findings": [
                {"id": "f", "from": "b", "to": "p", "says": "yes"},
            ],
            "links": [{"id": "l", "from": "b", "to": "p"}],
        }

        selected = select_focus_nodes(graph, maximum=5)

        self.assertEqual([item["thing_id"] for item in selected], ["b", "p"])
        self.assertEqual(selected[1]["display_label"], "Mechanistic/PD readout: Process")
        self.assertNotIn("u", {item["thing_id"] for item in selected})

    def test_replay_runs_three_distinct_branches_with_exact_lineage(self) -> None:
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture)
            registry = ModuleRegistry.load(fixture.root)
            setup = validate_setup(scientific_request(), registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            branches = final["scientific"]["branches"]
            self.assertEqual(final["run_status"], "COMPLETED")
            self.assertEqual(
                [branch["focus"]["thing_id"] for branch in branches], ["b1", "b2", "p1"]
            )
            self.assertTrue(all(branch["status"] == "COMPLETE" for branch in branches))
            for branch in branches:
                self.assertEqual(set(branch["nodes"]), {
                    "hypothesis_generator", "clinical_simulation", "simulation", "roi_calculator"
                })
                for node in branch["nodes"].values():
                    self.assertTrue(node["input_hash"].startswith("sha256:"))
                    self.assertTrue(node["output_hash"].startswith("sha256:"))
                    self.assertIn(created["run_id"], node["input_ref"])
            process_input = json.loads(
                (
                    fixture.root
                    / "runs"
                    / branches[2]["nodes"]["clinical_simulation"]["input_ref"]
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                process_input["biomarker_population"]["marker"],
                "Mechanistic/PD readout: Pathway readout",
            )
            self.assertEqual(process_input["evidence"][0]["source"], "PMID:12345")

    def test_scientific_state_and_snapshot_are_available_through_the_local_api(self) -> None:
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture)
            running = RunningServer(fixture)
            try:
                status, _, created = running.json_request(
                    "POST", "/api/runs", scientific_request()
                )
                self.assertEqual(status, 202)
                final = running.wait_for_terminal(created["runId"])
                snapshot_status, _, snapshot = running.json_request(
                    "GET", f"/api/runs/{created['runId']}/snapshot"
                )
            finally:
                running.close()

            self.assertEqual(final["runStatus"], "COMPLETED")
            self.assertEqual(snapshot_status, 200)
            self.assertEqual(snapshot["schema_version"], "labrador.scientific-snapshot.v1")
            self.assertEqual(len(snapshot["branches"]), 3)
            self.assertTrue(snapshot["scientific_packet_excludes_representative_values"])

    def test_one_branch_failure_does_not_stop_independent_branches_or_simulation(self) -> None:
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture, fail_focus="b2")
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(scientific_request(), registry=registry))

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            by_focus = {
                branch["focus"]["thing_id"]: branch
                for branch in final["scientific"]["branches"]
            }
            self.assertEqual(final["run_status"], "COMPLETED_WITH_WARNINGS")
            self.assertEqual(by_focus["b1"]["status"], "COMPLETE")
            self.assertEqual(by_focus["p1"]["status"], "COMPLETE")
            failed = by_focus["b2"]
            self.assertEqual(
                failed["nodes"]["hypothesis_generator"]["reason_code"],
                "CREDENTIAL_MISSING",
            )
            self.assertEqual(failed["nodes"]["simulation"]["status"], "COMPLETE")
            self.assertEqual(
                failed["nodes"]["clinical_simulation"]["reason_code"],
                "UPSTREAM_FAILED",
            )
            self.assertNotIn("fallback", json.dumps(failed).casefold())

    def test_provider_timeout_is_terminal_and_never_replaced_by_a_fixture(self) -> None:
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture, slow_simulation=True)
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(scientific_request(), registry=registry))

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            for branch in final["scientific"]["branches"]:
                simulation = branch["nodes"]["simulation"]
                self.assertEqual(simulation["status"], "CANNOT_COMPLETE")
                self.assertEqual(simulation["reason_code"], "MODULE_TIMEOUT")
                self.assertEqual(simulation["output_origin"], "NOT_RUN")

    def test_representative_mode_cannot_change_scientific_artifact_hashes(self) -> None:
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture)
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            scientific = store.create(
                validate_setup(scientific_request(presentation="SCIENTIFIC"), registry=registry)
            )
            representative = store.create(
                validate_setup(
                    scientific_request(presentation="REPRESENTATIVE_DEMO"), registry=registry
                )
            )

            first = SequentialRunner(fixture.root, registry, store).run(scientific["run_id"])
            second = SequentialRunner(fixture.root, registry, store).run(
                representative["run_id"]
            )

            first_hashes = [
                node["output_hash"]
                for branch in first["scientific"]["branches"]
                for node in branch["nodes"].values()
            ]
            second_hashes = [
                node["output_hash"]
                for branch in second["scientific"]["branches"]
                for node in branch["nodes"].values()
            ]
            self.assertEqual(first_hashes, second_hashes)


if __name__ == "__main__":
    unittest.main()
