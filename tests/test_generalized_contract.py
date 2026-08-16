from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import ContractError
from labrador_orchestrator.projection import project_ui_state
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import VALID_SETUP, FixtureProject, write_json


def custom_request() -> dict[str, object]:
    """A non-example program with explicit analyst-owned module inputs."""

    return {
        "schemaVersion": "labrador.run-setup.v2",
        "exploration": {
            "evidenceRequest": {
                "ask": "new_question",
                "target": "Can EGFR inhibition alter glioblastoma invasion?",
                "depth": "standard",
            },
            "hypothesis": {"boldnessRange": [2, 8]},
            "presentation": {
                "biomarkerRange": [1, 10],
                "maxBiomarkers": 2,
                "maxLiteraturePapers": 25,
                "maxHypothesesPerBiomarker": 2,
            },
        },
        "program": {
            "frame": {
                "schemaVersion": "labrador.program-frame.v1",
                "frameId": "egfr-gbm.v1",
                "basis": "ANALYST_SUPPLIED",
                "identity": {
                    "programId": "EGFR-GBM-001",
                    "displayName": "EGFR inhibition in glioblastoma",
                    "indication": "Glioblastoma",
                    "targetSymbol": "EGFR",
                    "uniprotAccession": "P00533",
                    "modality": "small_molecule",
                },
                "clinicalThesis": {
                    "id": "egfr-gbm-clinical",
                    "asset": {"name": "EGFR inhibitor", "modality": "small_molecule"},
                    "target": {
                        "symbol": "EGFR",
                        "direction": "inhibit",
                        "uniprot_accession": "P00533",
                    },
                    "disease": {"name": "Glioblastoma"},
                    "biomarker_population": {
                        "marker": "EGFR amplified",
                        "prevalence_in_disease": 0.45,
                        "assay_available": True,
                    },
                    "endpoint": {
                        "name": "progression-free survival",
                        "type": "continuous",
                        "expected_effect_size": 0.25,
                    },
                    "mechanism": "Inhibit oncogenic EGFR signaling.",
                },
                "plannedEnrollmentMonths": None,
                "simulationContext": {"interactionToDisrupt": "EGFR ATP site"},
                "roiRequest": {
                    "contract_version": "1.0.0",
                    "module": "rnpv_roi_calculator",
                    "request_id": "egfr-gbm-roi",
                    "program": {
                        "program_id": "EGFR-GBM-001",
                        "program_name": "EGFR inhibition in glioblastoma",
                        "target": "EGFR",
                        "modality": "SMALL_MOLECULE",
                        "initial_indication": {"name": "Glioblastoma"},
                    },
                    "comparables": [],
                    "execution": {},
                },
                "notes": ["Hermetic non-example program"],
            }
        },
    }


class GeneralizedRunContractTests(unittest.TestCase):
    def test_legacy_setup_resolves_an_explicit_golden_profile(self) -> None:
        with FixtureProject(subjects={"hypothesis_generator": "EGFR"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            setup = validate_setup(dict(VALID_SETUP), registry=registry)

            self.assertEqual(setup["profileRef"], "golden.ra-irak4.v1")
            self.assertEqual(setup["programFrame"]["basis"], "PROFILE_FIXTURE")
            self.assertEqual(setup["programFrame"]["identity"]["targetSymbol"], "IRAK4")
            self.assertEqual(setup["fallbackPolicy"], "PROFILE_MATCH_ONLY")

    def test_custom_non_example_payloads_cross_their_exact_stage_boundaries(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            for module in registry.modules:
                write_json(module.example_input, {"example_only": module.module_id})
            setup = validate_setup(custom_request(), registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            self.assertEqual(final["run_status"], "COMPLETED")
            run_dir = store.run_dir(created["run_id"])
            inputs = {
                stage["module_id"]: json.loads((run_dir / stage["input_ref"]).read_text())
                for stage in final["stages"]
            }
            frame = setup["programFrame"]
            self.assertEqual(inputs["evidence_mapper"], frame["evidenceRequest"])
            self.assertEqual(inputs["clinical_simulation"], frame["clinicalThesis"])
            self.assertEqual(inputs["roi_calculator"], frame["roiRequest"])
            self.assertEqual(inputs["simulation"]["uniprot_accession"], "P00533")
            self.assertEqual(inputs["simulation"]["disease_context"], "Glioblastoma")
            self.assertNotIn("example_only", json.dumps(inputs))
            self.assertEqual(
                [stage["input_source"] for stage in final["stages"]],
                [
                    "ANALYST_FRAME",
                    "UPSTREAM_DERIVED",
                    "ANALYST_FRAME",
                    "ANALYST_FRAME",
                    "ANALYST_FRAME",
                ],
            )

    def test_custom_run_never_uses_a_golden_fallback(self) -> None:
        with FixtureProject(behaviors={"hypothesis_generator": "exit_nonzero"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            setup = validate_setup(custom_request(), registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            hypothesis = next(
                stage for stage in final["stages"] if stage["module_id"] == "hypothesis_generator"
            )

            self.assertEqual(hypothesis["status"], "FAILED")
            self.assertNotEqual(hypothesis["output_origin"], "DEMO_FALLBACK")
            self.assertEqual(hypothesis["reason_code"], "NO_MATCHING_CACHED_ARTIFACT")
            attempt = hypothesis["attempts"][-1]
            self.assertEqual(attempt["reason_code"], "PROCESS_EXIT_NONZERO")
            self.assertEqual(attempt["exit_code"], 7)
            self.assertIn("intentional fake-module failure", attempt["stderr"])

    def test_custom_run_refuses_profile_cached_artifacts_at_the_first_boundary(self) -> None:
        with FixtureProject(
            modes={"evidence_mapper": "cached", "simulation": "cached"},
            subjects={"hypothesis_generator": "EGFR"},
        ) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            setup = validate_setup(custom_request(), registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            stages = {stage["module_id"]: stage for stage in final["stages"]}

            self.assertEqual(stages["evidence_mapper"]["status"], "FAILED")
            self.assertEqual(
                stages["evidence_mapper"]["reason_code"], "NO_MATCHING_CACHED_ARTIFACT"
            )
            self.assertEqual(stages["evidence_mapper"]["output_origin"], "NOT_RUN")
            evidence_output = (
                store.run_dir(created["run_id"]) / "01_evidence_mapper/output.json"
            )
            self.assertFalse(evidence_output.exists())

    def test_missing_optional_analyst_inputs_abstain_without_borrowing_examples(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            request = custom_request()
            frame = request["program"]["frame"]  # type: ignore[index]
            frame["clinicalThesis"] = None
            frame["roiRequest"] = None
            setup = validate_setup(request, registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)

            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            stages = {stage["module_id"]: stage for stage in final["stages"]}

            self.assertEqual(stages["clinical_simulation"]["status"], "SKIPPED")
            self.assertEqual(
                stages["clinical_simulation"]["reason_code"], "MISSING_CLINICAL_THESIS"
            )
            self.assertEqual(stages["roi_calculator"]["status"], "SKIPPED")
            self.assertEqual(stages["roi_calculator"]["reason_code"], "MISSING_ROI_REQUEST")
            self.assertEqual(stages["simulation"]["status"], "COMPLETE")
            self.assertTrue(final["highlander"]["ready"])

    def test_frame_identity_mismatch_is_rejected_before_run_creation(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            request = custom_request()
            frame = request["program"]["frame"]  # type: ignore[index]
            frame["clinicalThesis"]["target"]["symbol"] = "JAK1"  # type: ignore[index]

            with self.assertRaisesRegex(ContractError, "FRAME_IDENTITY_MISMATCH"):
                validate_setup(request, registry=registry)
            self.assertEqual(list((fixture.root / "runs").iterdir()), [])

    def test_frame_modality_mismatch_is_rejected(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            request = custom_request()
            frame = request["program"]["frame"]  # type: ignore[index]
            frame["clinicalThesis"]["asset"]["modality"] = "antibody"  # type: ignore[index]

            with self.assertRaisesRegex(ContractError, "FRAME_IDENTITY_MISMATCH.*modality"):
                validate_setup(request, registry=registry)

    def test_golden_profile_cannot_ignore_a_different_evidence_request(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            request = custom_request()
            request["program"] = {"profileRef": "golden.ra-irak4.v1"}

            with self.assertRaisesRegex(ContractError, "PROFILE_REQUEST_MISMATCH"):
                validate_setup(request, registry=registry)

    def test_native_module_input_schema_is_enforced_before_run_creation(self) -> None:
        with FixtureProject() as fixture:
            schema_path = fixture.root / "modules" / "clinical_simulation" / "input.schema.json"
            write_json(
                schema_path,
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "required": ["contract_specific_field"],
                },
            )
            registry = ModuleRegistry.load(fixture.root)

            with self.assertRaisesRegex(ContractError, "clinical_simulation analyst input"):
                validate_setup(custom_request(), registry=registry)
            self.assertEqual(list((fixture.root / "runs").iterdir()), [])

    def test_two_distinct_custom_frames_do_not_collapse_to_one_fixture_identity(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            first = validate_setup(custom_request(), registry=registry)
            second_request = copy.deepcopy(custom_request())
            frame = second_request["program"]["frame"]  # type: ignore[index]
            frame["frameId"] = "braf-melanoma.v1"
            frame["identity"].update(  # type: ignore[union-attr]
                {
                    "programId": "BRAF-MEL-001",
                    "displayName": "BRAF inhibition in melanoma",
                    "indication": "Melanoma",
                    "targetSymbol": "BRAF",
                    "uniprotAccession": "P15056",
                }
            )
            frame["clinicalThesis"]["id"] = "braf-mel-clinical"  # type: ignore[index]
            frame["clinicalThesis"]["target"].update(  # type: ignore[index]
                {"symbol": "BRAF", "uniprot_accession": "P15056"}
            )
            frame["clinicalThesis"]["disease"]["name"] = "Melanoma"  # type: ignore[index]
            frame["roiRequest"]["program"].update(  # type: ignore[index]
                {
                    "program_id": "BRAF-MEL-001",
                    "program_name": "BRAF inhibition in melanoma",
                    "target": "BRAF",
                }
            )
            frame["roiRequest"]["program"]["initial_indication"]["name"] = "Melanoma"  # type: ignore[index]
            second = validate_setup(second_request, registry=registry)

            self.assertEqual(first["programFrame"]["identity"]["targetSymbol"], "EGFR")
            self.assertEqual(second["programFrame"]["identity"]["targetSymbol"], "BRAF")
            self.assertNotEqual(first["programFrame"], second["programFrame"])

    def test_custom_projection_uses_frame_identity_without_golden_labels(self) -> None:
        with FixtureProject(subjects={"hypothesis_generator": "EGFR"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            setup = validate_setup(custom_request(), registry=registry)
            store = RunStore(fixture.root, registry)
            created = store.create(setup)
            final = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            projected = project_ui_state(fixture.root, registry, final)
            serialized = json.dumps(projected)

            self.assertIn("EGFR", serialized)
            self.assertIn("Glioblastoma", serialized)
            self.assertIn("P00533", serialized)
            self.assertNotIn("IRAK4", serialized)
            self.assertNotIn("Rheumatoid arthritis", serialized)
            self.assertNotIn("IRAK4/RA", serialized)
            for fixture_claim in (
                "read 6 of 49",
                "Cached evidence graph",
                "Synthetic rNPV",
                "Analyst-fixture input",
                "Cached integration fixture",
            ):
                self.assertNotIn(fixture_claim, serialized)


if __name__ == "__main__":
    unittest.main()
