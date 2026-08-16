from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import load_json, sha256_file, validate_json
from labrador_orchestrator.frontend_projection import project_frontend_snapshot
from labrador_orchestrator.projection import project_ui_state
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import OBJECT_SCHEMA, VALID_SETUP, FixtureProject, write_json

ROOT = Path(__file__).resolve().parents[1]
HYPGEN_ROOT = ROOT / ".modules" / "Hypothesis_Generator"

CARD_IDS = [
    "H-analog-t4-t2-via-t1",
    "H-cond-L3",
    "H-chain-t2-t5-2",
]
BIOMARKER_THING_IDS = ["t2", "t3", "t5"]
PROGRAM_IDS = [
    f"ctx-{thing_id}--{card_id}"
    for thing_id in BIOMARKER_THING_IDS
    for card_id in CARD_IDS
]
ASSOCIATION_BASES = [
    "SOURCE_PATH",
    "CONTEXT_ONLY",
    "SOURCE_PATH",
    "CONTEXT_ONLY",
    "SOURCE_PATH",
    "CONTEXT_ONLY",
    "CONTEXT_ONLY",
    "CONTEXT_ONLY",
    "SOURCE_PATH",
]
SHARED_FRAME_QUALIFIERS = {
    "SHARED_RA_ANALYST_FRAME",
    "NOT_CANDIDATE_SPECIFIC",
}
SHARED_SLATE_QUALIFIERS = {
    "SHARED_HYPOTHESIS_SLATE_ACROSS_BIOMARKERS",
    "NOT_INDEPENDENT_GENERATIONS",
}
REPRESENTATIVE_BASIS = "REPRESENTATIVE_DEMO_SCENARIO_V1"
REPRESENTATIVE_DISPLAY_METRICS = [
    {
        "boldness": 7,
        "evidence": 72,
        "plausibility": 79,
        "rnpv": 145,
        "positive": 62,
        "impact": 82,
        "recruit": 82,
        "duration": 18,
        "screens": 2.3,
        "risk": 18,
        "tractability_fit": 86,
    },
    {
        "boldness": 6,
        "evidence": 58,
        "plausibility": 71,
        "rnpv": 132,
        "positive": 57,
        "impact": 74,
        "recruit": 69,
        "duration": 24,
        "screens": 3.2,
        "risk": 31,
        "tractability_fit": 64,
    },
    {
        "boldness": 8,
        "evidence": 64,
        "plausibility": 73,
        "rnpv": 108,
        "positive": 51,
        "impact": 70,
        "recruit": 75,
        "duration": 21,
        "screens": 2.8,
        "risk": 25,
        "tractability_fit": 78,
    },
    {
        "boldness": 7,
        "evidence": 50,
        "plausibility": 67,
        "rnpv": 115,
        "positive": 53,
        "impact": 70,
        "recruit": 68,
        "duration": 25,
        "screens": 3.4,
        "risk": 32,
        "tractability_fit": 62,
    },
    {
        "boldness": 6,
        "evidence": 69,
        "plausibility": 74,
        "rnpv": 195,
        "positive": 69,
        "impact": 79,
        "recruit": 62,
        "duration": 28,
        "screens": 4.0,
        "risk": 38,
        "tractability_fit": 84,
    },
    {
        "boldness": 8,
        "evidence": 76,
        "plausibility": 86,
        "rnpv": 120,
        "positive": 55,
        "impact": 88,
        "recruit": 88,
        "duration": 16,
        "screens": 2.0,
        "risk": 12,
        "tractability_fit": 88,
    },
    {
        "boldness": 8,
        "evidence": 67,
        "plausibility": 70,
        "rnpv": 170,
        "positive": 64,
        "impact": 76,
        "recruit": 55,
        "duration": 33,
        "screens": 4.9,
        "risk": 45,
        "tractability_fit": 80,
    },
    {
        "boldness": 7,
        "evidence": 55,
        "plausibility": 68,
        "rnpv": 185,
        "positive": 67,
        "impact": 71,
        "recruit": 48,
        "duration": 36,
        "screens": 5.7,
        "risk": 52,
        "tractability_fit": 60,
    },
    {
        "boldness": 9,
        "evidence": 63,
        "plausibility": 72,
        "rnpv": 128,
        "positive": 56,
        "impact": 77,
        "recruit": 77,
        "duration": 20,
        "screens": 2.7,
        "risk": 23,
        "tractability_fit": 66,
    },
]


def _card(
    card_id: str,
    *,
    headline: str,
    trace: str,
    motif: str,
    support: float,
    novelty: float,
    testability: float,
    rank: float,
) -> dict[str, Any]:
    """A minimal official Hypothesis_Generator WebPayload card."""

    return {
        "id": card_id,
        "headline": headline,
        "statement": None,
        "trace": trace,
        "motif": motif,
        "hops": 1 if motif in {"analogical_transfer", "condition_split"} else 2,
        "metrics": {
            "support": support,
            "novelty": novelty,
            "testability": testability,
            "rank": rank,
        },
        "status": {
            "verification": "qualified",
            "critics": None,
            "flags": [],
        },
        "highlights": [],
    }


def _cards_payload() -> dict[str, Any]:
    """Three real deterministic c=1.0 candidates in official WebPayload form."""

    return {
        "schema_version": "1.0",
        "graph_id": "g_1a4f",
        "round": 2,
        "question": (
            "can a small-molecule IRAK4 inhibitor suppress synovial fibroblast-driven "
            "inflammation in rheumatoid arthritis, or is its effect confined to the "
            "myeloid compartment?"
        ),
        "generated_at": "2026-08-15T17:30:00Z",
        "coverage": {
            "depth": "deep",
            "found": 49,
            "read": 6,
            "used": 2,
            "truncated": True,
        },
        "warnings": [
            "Absence of a link is not evidence of absence: this search read 6 of 49 results."
        ],
        "hypotheses": [
            _card(
                CARD_IDS[0],
                headline="MyD88 dimerization inhibition → myeloid inflammatory signalling",
                trace=(
                    "MyD88 dimerization inhibition <--suppresses-- IRAK4 inhibition "
                    "--suppresses--> myeloid inflammatory signalling"
                ),
                motif="analogical_transfer",
                support=0.512,
                novelty=0.0,
                testability=0.7,
                rank=0.4692,
            ),
            _card(
                CARD_IDS[1],
                headline="IRAK4 inhibition → synovial fibroblast driven inflammation",
                trace=(
                    "IRAK4 inhibition --suppresses--> synovial fibroblast driven inflammation"
                ),
                motif="condition_split",
                support=0.423,
                novelty=0.0,
                testability=1.0,
                rank=0.3448,
            ),
            _card(
                CARD_IDS[2],
                headline="myeloid inflammatory signalling → TLR/MyD88/NF-kB signalling axis",
                trace=(
                    "myeloid inflammatory signalling <--suppresses-- IRAK4 inhibition "
                    "--blocks--> TLR/MyD88/NF-kB signalling axis"
                ),
                motif="transitive_chain",
                support=0.512,
                novelty=0.21,
                testability=0.7,
                rank=0.4084,
            ),
        ],
        "interpretability": {
            "schema_version": "1.0.0",
            "headline": {
                "title": "Hypothesis slate",
                "result": "HYPOTHESIS_GENERATED_QUALIFIED",
                "plain_language": "Three structural candidates survived deterministic selection.",
                "status": "QUALIFIED",
                "basis": ["INFERRED"],
            },
            "metrics": [],
            "steps": [],
            "evidence": [],
            "assumptions": [],
            "uncertainty": {
                "method": "none",
                "intervals": [],
                "seed": None,
                "draws": None,
                "limitations": ["Deterministic heuristic scores are not probabilities."],
            },
            "limitations": [
                {
                    "code": "STRUCTURAL_CANDIDATE_NOT_ARTICULATED",
                    "severity": "WARNING",
                    "message": "Dry-run candidates have no model-authored statement.",
                    "field_path": "hypotheses[].statement",
                }
            ],
            "counterfactuals": [],
            "lineage": [],
            "extensions": {
                "run_mode": "DRY_RUN",
            },
        },
    }


def _replace_stage_output(
    fixture: FixtureProject,
    manifest: dict[str, Any],
    module_id: str,
    value: dict[str, Any],
) -> None:
    stage = next(item for item in manifest["stages"] if item["module_id"] == module_id)
    output = fixture.root / "runs" / manifest["run_id"] / stage["output_ref"]
    write_json(output, value)
    stage["output_hash"] = sha256_file(output)


class RASlateContractTests(unittest.TestCase):
    def test_hypothesis_adapter_emits_three_official_cards_from_one_crazy_run(self) -> None:
        """The adapter boundary is the published cards schema, not an invented envelope."""

        self.assertTrue(HYPGEN_ROOT.is_dir(), "bootstrap the pinned Hypothesis_Generator first")
        with tempfile.TemporaryDirectory(prefix="labrador-ra-slate-") as temporary:
            output = Path(temporary) / "cards.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_hypothesis.py"),
                    "--module-root",
                    str(HYPGEN_ROOT),
                    "--input",
                    str(ROOT / "fixtures" / "golden" / "fallbacks" / "evidence.json"),
                    "--output",
                    str(output),
                    "--craziness",
                    "1.0",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = load_json(output)
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(
                [card["id"] for card in payload["hypotheses"]],
                CARD_IDS,
            )
            self.assertEqual(len(payload["hypotheses"]), 3)
            self.assertEqual(payload["interpretability"]["schema_version"], "1.0.0")
            self.assertEqual(
                payload["interpretability"]["extensions"]["run_mode"],
                "DRY_RUN",
            )
            schema_path = HYPGEN_ROOT / "schemas" / "cards.schema.json"
            validate_json(
                load_json(schema_path),
                payload,
                label="RA hypothesis cards",
                schema_path=schema_path,
            )

    def test_ra_slate_projects_three_candidates_under_each_grounded_signal(
        self,
    ) -> None:
        """Three native candidates may fill nine lanes, but never become nine generations."""

        with FixtureProject() as fixture:
            # The production registry will point HypGen at cards.schema.json. These
            # hermetic fake modules need only allow the official payload through so
            # this test can isolate the orchestrator projection contract.
            for module_id in ("evidence_mapper", "hypothesis_generator"):
                schema_path = fixture.root / fixture.module_entry(module_id)["output_schema"]
                write_json(schema_path, OBJECT_SCHEMA)

            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP), registry=registry))
            manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            evidence = load_json(ROOT / "fixtures" / "golden" / "fallbacks" / "evidence.json")
            _replace_stage_output(fixture, manifest, "evidence_mapper", evidence)
            cards = _cards_payload()
            cards_schema = HYPGEN_ROOT / "schemas" / "cards.schema.json"
            validate_json(
                load_json(cards_schema),
                cards,
                label="hermetic RA hypothesis cards",
                schema_path=cards_schema,
            )
            _replace_stage_output(fixture, manifest, "hypothesis_generator", cards)

            snapshot = project_frontend_snapshot(fixture.root, registry, manifest)
            internal = project_ui_state(fixture.root, registry, manifest)

            self.assertEqual(
                [item["graph_thing_id"] for item in snapshot["biomarkers"]],
                BIOMARKER_THING_IDS,
            )
            self.assertTrue(
                all(
                    isinstance(item["metrics"].get("evidence"), (int, float))
                    for item in snapshot["biomarkers"]
                )
            )
            self.assertEqual(len({item["graph_thing_id"] for item in snapshot["biomarkers"]}), 3)
            self.assertEqual([program["id"] for program in snapshot["programs"]], PROGRAM_IDS)
            self.assertEqual([program["lane"] for program in snapshot["programs"]], list(range(9)))
            self.assertEqual(
                [program["biomarker_slot"] for program in snapshot["programs"]],
                [0, 0, 0, 1, 1, 1, 2, 2, 2],
            )
            self.assertEqual(
                [program["hypothesis_slot"] for program in snapshot["programs"]],
                [0, 1, 2, 0, 1, 2, 0, 1, 2],
            )
            self.assertEqual(
                [program["source_hypothesis_id"] for program in snapshot["programs"]],
                CARD_IDS * 3,
            )
            self.assertEqual(
                [program["biomarker_graph_thing_id"] for program in snapshot["programs"]],
                [thing_id for thing_id in BIOMARKER_THING_IDS for _ in CARD_IDS],
            )
            self.assertEqual(
                [program["association_basis"] for program in snapshot["programs"]],
                ASSOCIATION_BASES,
            )
            self.assertEqual(
                [program["display_metrics"] for program in snapshot["programs"]],
                REPRESENTATIVE_DISPLAY_METRICS,
            )
            self.assertEqual(
                {program["display_metric_basis"] for program in snapshot["programs"]},
                {REPRESENTATIVE_BASIS},
            )
            self.assertEqual(
                len({program["display_label"] for program in snapshot["programs"]}),
                9,
            )
            self.assertEqual(
                len(
                    {
                        tuple(program["display_metrics"].values())
                        for program in snapshot["programs"]
                    }
                ),
                9,
            )
            self.assertEqual(
                {item["display_metric_basis"] for item in snapshot["biomarkers"]},
                {REPRESENTATIVE_BASIS},
            )
            self.assertEqual(
                [item["display_metrics"] for item in snapshot["biomarkers"]],
                [
                    {"exploration": 4, "evidence": 60.0, "pursuit": 2},
                    {"exploration": 6, "evidence": 50.7, "pursuit": 2},
                    {"exploration": 5, "evidence": 40.7, "pursuit": 3},
                ],
            )

            native_station_payloads = [
                program["station_payloads"] for program in snapshot["programs"]
            ]
            self.assertTrue(
                all(payload == native_station_payloads[0] for payload in native_station_payloads)
            )
            self.assertEqual(
                len({program["metrics"]["rnpv"] for program in snapshot["programs"]}),
                1,
                "native-derived metrics remain shared; only display_metrics may vary",
            )

            stage_rows = {stage["stage_id"]: stage for stage in snapshot["stages"]}
            self.assertEqual(stage_rows["hypothesis"]["native_returned"], 3)
            self.assertEqual(stage_rows["hypothesis"]["returned"], 9)
            self.assertTrue(
                SHARED_SLATE_QUALIFIERS.issubset(stage_rows["hypothesis"]["qualifiers"])
            )
            for stage_id in ("roi", "recruitability", "simulation"):
                with self.subTest(stage=stage_id):
                    self.assertEqual(stage_rows[stage_id]["native_returned"], 1)
                    self.assertEqual(stage_rows[stage_id]["returned"], 9)
                    self.assertTrue(
                        SHARED_FRAME_QUALIFIERS.issubset(stage_rows[stage_id]["qualifiers"])
                    )

            for program in snapshot["programs"]:
                with self.subTest(program=program["id"]):
                    self.assertIn("SHARED_RA_ANALYST_FRAME", program["public_why"])
                    self.assertIn("NOT_CANDIDATE_SPECIFIC", program["public_why"])
                    self.assertIn(
                        "SHARED_HYPOTHESIS_SLATE_ACROSS_BIOMARKERS",
                        program["public_why"],
                    )
                    self.assertIn("NOT_INDEPENDENT_GENERATIONS", program["public_why"])
                    self.assertIn(REPRESENTATIVE_BASIS, program["public_why"])
                    self.assertIn("NOT_NATIVE_MODULE_OUTPUT", program["public_why"])

            nodes = internal["uiProjection"]["nodes"]
            node_ids = [node["id"] for node in nodes]
            self.assertEqual(len(node_ids), len(set(node_ids)))
            self.assertEqual(len([node for node in nodes if node["stage"] == "biomarker"]), 3)
            for stage_id in ("hypothesis", "roi", "recruitability", "simulation"):
                with self.subTest(node_stage=stage_id):
                    self.assertEqual(len([node for node in nodes if node["stage"] == stage_id]), 9)
            for slot in range(3):
                with self.subTest(biomarker_slot=slot):
                    self.assertEqual(
                        len(
                            [
                                node
                                for node in nodes
                                if node["stage"] == "hypothesis"
                                and node["parentId"] == f"bio-slot-{slot}"
                            ]
                        ),
                        3,
                    )


if __name__ == "__main__":
    unittest.main()
