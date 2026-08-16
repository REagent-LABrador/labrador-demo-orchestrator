from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import ContractError
from labrador_orchestrator.projection import VISUAL_STAGE_ORDER, _percent, project_ui_state
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import VALID_SETUP, FixtureProject


class UIProjectionTests(unittest.TestCase):
    def _mixed_projection(
        self, fixture: FixtureProject
    ) -> tuple[dict[str, object], dict[str, object]]:
        registry = ModuleRegistry.load(fixture.root)
        store = RunStore(fixture.root, registry)
        raw_setup = dict(VALID_SETUP, clinicalIndication="  RA  ")
        created = store.create(validate_setup(raw_setup))
        manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
        return project_ui_state(fixture.root, registry, manifest), manifest

    def test_projection_exposes_visual_order_and_mixed_truth_without_absolute_paths(self) -> None:
        with FixtureProject(
            modes={"evidence_mapper": "cached", "simulation": "cached"},
            behaviors={"clinical_simulation": "exit_nonzero"},
        ) as fixture:
            projected, _ = self._mixed_projection(fixture)

            self.assertEqual(projected["schemaVersion"], "labrador.ui-run-state.v1")
            self.assertEqual([stage["id"] for stage in projected["stages"]], VISUAL_STAGE_ORDER)
            self.assertEqual(projected["truth"]["dataBasis"], "LIVE + LABELED FALLBACKS")
            self.assertEqual(projected["setupSnapshot"]["submittedIndication"], "  RA  ")
            self.assertEqual(projected["setupSnapshot"]["indication"], "RA")

            modules = {module["id"]: module for module in projected["modules"]}
            self.assertEqual(modules["evidence_mapper"]["outputOrigin"], "CACHED")
            self.assertEqual(modules["clinical_simulation"]["executionStatus"], "FAILED")
            self.assertEqual(modules["clinical_simulation"]["outputOrigin"], "DEMO_FALLBACK")
            self.assertEqual(modules["clinical_simulation"]["reasonCode"], "PROCESS_EXIT_NONZERO")
            self.assertIn("NOT_DECISION_GRADE", modules["roi_calculator"]["qualifiers"])

            serialized = json.dumps(projected, sort_keys=True)
            self.assertNotIn(str(fixture.root), serialized)
            self.assertNotIn(str(Path.home()), serialized)
            self.assertNotIn("input_ref", serialized)
            self.assertNotIn("output_ref", serialized)

    def test_projection_rejects_output_reference_escape(self) -> None:
        with FixtureProject(modes={"evidence_mapper": "cached"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP)))
            manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            manifest["stages"][0]["output_ref"] = "../../outside.json"

            with self.assertRaisesRegex(ContractError, "escapes allowed root"):
                project_ui_state(fixture.root, registry, manifest)

    def test_projection_rejects_output_tampering_after_manifest_hash(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP)))
            manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            stage = manifest["stages"][0]
            output_path = store.run_dir(created["run_id"]) / stage["output_ref"]
            output_path.write_text('{"tampered":true}\n', encoding="utf-8")

            with self.assertRaisesRegex(ContractError, "output integrity mismatch"):
                project_ui_state(fixture.root, registry, manifest)

    def test_projection_accepts_null_counterfactual_and_normalizes_score_units(self) -> None:
        with FixtureProject(behaviors={"clinical_simulation": "null_counterfactual"}) as fixture:
            projected, _ = self._mixed_projection(fixture)

            recruit = next(
                node
                for node in projected["uiProjection"]["nodes"]
                if node["stage"] == "recruitability"
            )
            self.assertIsNone(recruit["metadata"]["counterevidenceSummary"])

        for raw, expected in ((0.8, 80.0), (80, 80.0), (0, 0.0), (100, 100.0)):
            with self.subTest(raw=raw):
                self.assertEqual(_percent(raw), expected)


if __name__ == "__main__":
    unittest.main()
