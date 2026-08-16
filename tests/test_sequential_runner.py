from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import STAGES, VALID_SETUP, FixtureProject, write_json


class SequentialRunnerTests(unittest.TestCase):
    def _run(self, fixture: FixtureProject) -> tuple[dict[str, object], RunStore]:
        registry = ModuleRegistry.load(fixture.root)
        registry.preflight(check_git=False)
        store = RunStore(fixture.root, registry)
        created = store.create(validate_setup(dict(VALID_SETUP)))
        manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
        return manifest, store

    def test_five_live_modules_execute_in_strict_registry_order(self) -> None:
        with FixtureProject() as fixture:
            manifest, store = self._run(fixture)

            self.assertEqual(manifest["run_status"], "COMPLETED")
            self.assertEqual([stage["status"] for stage in manifest["stages"]], ["COMPLETE"] * 5)
            self.assertEqual(
                [stage["execution_status"] for stage in manifest["stages"]],
                ["COMPLETE"] * 5,
            )
            self.assertEqual([stage["output_origin"] for stage in manifest["stages"]], ["LIVE"] * 5)
            self.assertTrue(manifest["highlander"]["ready"])

            trace = fixture.read_trace()
            starts = [event for event in trace if event["event"] == "start"]
            ends = [event for event in trace if event["event"] == "end"]
            expected = [module_id for module_id, _ in STAGES]
            self.assertEqual([event["stage"] for event in starts], expected)
            self.assertEqual([event["stage"] for event in ends], expected)
            for left, right in zip(ends, starts[1:], strict=False):
                self.assertLess(left["monotonic_ns"], right["monotonic_ns"])

            run_dir = store.run_dir(manifest["run_id"])
            for order, (module_id, _) in enumerate(STAGES, start=1):
                stage_dir = run_dir / f"{order:02d}_{module_id}"
                self.assertTrue((stage_dir / "input.json").is_file())
                self.assertTrue((stage_dir / "output.json").is_file())

    def test_mixed_cached_live_and_timeout_run_preserves_truth_labels(self) -> None:
        with FixtureProject(
            modes={"evidence_mapper": "cached", "simulation": "cached"},
            behaviors={"clinical_simulation": "slow"},
            timeouts={"clinical_simulation": 0.05},
        ) as fixture:
            started = time.monotonic()
            manifest, _ = self._run(fixture)
            elapsed = time.monotonic() - started

            stages = {stage["module_id"]: stage for stage in manifest["stages"]}
            self.assertLess(elapsed, 1.5)
            self.assertEqual(manifest["run_status"], "COMPLETED_WITH_WARNINGS")
            self.assertEqual(
                (
                    stages["evidence_mapper"]["execution_status"],
                    stages["evidence_mapper"]["output_origin"],
                ),
                ("SKIPPED", "CACHED"),
            )
            self.assertEqual(
                (
                    stages["hypothesis_generator"]["execution_status"],
                    stages["hypothesis_generator"]["output_origin"],
                ),
                ("COMPLETE", "LIVE"),
            )
            recruitability = stages["clinical_simulation"]
            self.assertEqual(recruitability["status"], "COMPLETE_WITH_WARNINGS")
            self.assertEqual(recruitability["execution_status"], "TIMED_OUT")
            self.assertEqual(recruitability["output_origin"], "DEMO_FALLBACK")
            self.assertEqual(recruitability["reason_code"], "MODULE_TIMEOUT")
            self.assertEqual(recruitability["attempts"][-1]["status"], "TIMED_OUT")
            self.assertEqual(
                (
                    stages["roi_calculator"]["execution_status"],
                    stages["roi_calculator"]["output_origin"],
                ),
                ("COMPLETE", "LIVE"),
            )
            self.assertIn("NOT_DECISION_GRADE", stages["roi_calculator"]["qualifiers"])
            self.assertEqual(
                (stages["simulation"]["execution_status"], stages["simulation"]["output_origin"]),
                ("SKIPPED", "CACHED"),
            )
            self.assertTrue(manifest["highlander"]["ready"])

    def test_nonzero_exit_uses_validated_fallback_and_continues(self) -> None:
        with FixtureProject(behaviors={"hypothesis_generator": "exit_nonzero"}) as fixture:
            manifest, _ = self._run(fixture)
            stages = {stage["module_id"]: stage for stage in manifest["stages"]}

            failed_attempt = stages["hypothesis_generator"]
            self.assertEqual(failed_attempt["status"], "COMPLETE_WITH_WARNINGS")
            self.assertEqual(failed_attempt["execution_status"], "FAILED")
            self.assertEqual(failed_attempt["output_origin"], "DEMO_FALLBACK")
            self.assertEqual(failed_attempt["reason_code"], "PROCESS_EXIT_NONZERO")
            self.assertEqual(failed_attempt["attempts"][-1]["exit_code"], 7)
            self.assertIn(
                "intentional fake-module failure", failed_attempt["attempts"][-1]["stderr"]
            )
            self.assertEqual(stages["clinical_simulation"]["execution_status"], "COMPLETE")

    def test_invalid_live_output_is_not_promoted_as_live(self) -> None:
        with FixtureProject(behaviors={"hypothesis_generator": "invalid_schema"}) as fixture:
            manifest, _ = self._run(fixture)
            stage = next(
                item for item in manifest["stages"] if item["module_id"] == "hypothesis_generator"
            )

            self.assertEqual(stage["execution_status"], "FAILED")
            self.assertEqual(stage["output_origin"], "DEMO_FALLBACK")
            self.assertEqual(stage["reason_code"], "OUTPUT_INVALID")
            self.assertNotEqual(stage["status"], "COMPLETE")
            stage_dir = (
                fixture.root
                / "runs"
                / manifest["run_id"]
                / "02_hypothesis_generator"
            )
            self.assertTrue((stage_dir / "live-output.invalid.json").is_file())
            self.assertTrue((stage_dir / "output.json").is_file())

    def test_invalid_fallback_fails_run_and_skips_downstream_modules(self) -> None:
        with FixtureProject(behaviors={"hypothesis_generator": "exit_nonzero"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            registry.preflight(check_git=False)
            invalid_fallback = (
                fixture.root / "modules" / "hypothesis_generator" / "fallback-output.json"
            )
            write_json(invalid_fallback, {"stage": "hypothesis_generator"})
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP)))
            manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])
            stages = {stage["module_id"]: stage for stage in manifest["stages"]}

            self.assertEqual(manifest["run_status"], "FAILED")
            self.assertEqual(stages["hypothesis_generator"]["status"], "FAILED")
            self.assertEqual(stages["hypothesis_generator"]["reason_code"], "FALLBACK_INVALID")
            for module_id in ("clinical_simulation", "roi_calculator", "simulation"):
                self.assertEqual(stages[module_id]["status"], "SKIPPED")
                self.assertEqual(stages[module_id]["reason_code"], "UPSTREAM_FAILED")
                self.assertEqual(stages[module_id]["output_origin"], "NOT_RUN")

    def test_adapted_input_is_validated_before_process_start(self) -> None:
        with FixtureProject() as fixture:
            schema_path = fixture.root / "modules" / "clinical_simulation" / "input.schema.json"
            write_json(
                schema_path,
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "required": ["field-that-adapter-does-not-produce"],
                },
            )
            # Keep preflight valid while making the runtime adapter invalid.
            write_json(
                fixture.root / "modules" / "clinical_simulation" / "example-input.json",
                {"field-that-adapter-does-not-produce": True},
            )
            manifest, _ = self._run(fixture)
            stages = {stage["module_id"]: stage for stage in manifest["stages"]}

            self.assertEqual(stages["clinical_simulation"]["reason_code"], "INPUT_ADAPTER_INVALID")
            self.assertNotIn(
                "clinical_simulation",
                [event["stage"] for event in fixture.read_trace() if event["event"] == "start"],
            )
            self.assertEqual(stages["roi_calculator"]["reason_code"], "UPSTREAM_FAILED")


if __name__ == "__main__":
    unittest.main()
