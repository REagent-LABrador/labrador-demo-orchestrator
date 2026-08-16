from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import ContractError
from labrador_orchestrator.registry import ModuleRegistry
from tests._support import STAGES, FixtureProject, write_json


class RegistryPreflightTests(unittest.TestCase):
    def test_valid_registry_preflights_five_schema_example_contracts(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            reports = registry.preflight(check_git=False)

            self.assertEqual(
                [module.module_id for module in registry.modules], [item[0] for item in STAGES]
            )
            self.assertEqual(len(reports), 5)
            self.assertTrue(all(report["ok"] for report in reports))
            self.assertTrue(
                all("schemas and examples: valid" in report["checks"] for report in reports)
            )

    def test_preflight_rejects_invalid_example_before_any_module_runs(self) -> None:
        with FixtureProject() as fixture:
            bad = fixture.root / "modules" / "clinical_simulation" / "example-output.json"
            write_json(bad, {"stage": "clinical_simulation"})

            with self.assertRaisesRegex(
                ContractError, r"clinical_simulation example output failed"
            ):
                ModuleRegistry.load(fixture.root).preflight(check_git=False)
            self.assertEqual(fixture.read_trace(), [])

    def test_preflight_applies_runtime_semantics_to_fallbacks(self) -> None:
        with FixtureProject() as fixture:
            fallback = fixture.root / "modules" / "evidence_mapper" / "fallback-output.json"
            value = __import__("json").loads(fallback.read_text())
            value.pop("status")
            write_json(fallback, value)

            with self.assertRaisesRegex(ContractError, "evidence mapper payload status"):
                ModuleRegistry.load(fixture.root).preflight(check_git=False)

    def test_preflight_rejects_missing_contract_file(self) -> None:
        with FixtureProject() as fixture:
            missing = fixture.root / "modules" / "roi_calculator" / "input.schema.json"
            missing.unlink()

            with self.assertRaisesRegex(ContractError, r"roi_calculator: missing input schema"):
                ModuleRegistry.load(fixture.root).preflight(check_git=False)

    def test_registry_rejects_duplicate_module_ids_and_execution_orders(self) -> None:
        for field, value, message in (
            ("id", "evidence_mapper", "module ids must be unique"),
            ("order", 1, "module execution orders must be unique"),
        ):
            with self.subTest(field=field), FixtureProject() as fixture:
                fixture.registry_json["modules"][1][field] = value
                fixture.flush_registry()
                with self.assertRaisesRegex(ContractError, message):
                    ModuleRegistry.load(fixture.root)

    def test_registry_requires_the_five_ui_stage_bindings(self) -> None:
        with FixtureProject() as fixture:
            fixture.registry_json["modules"][-1]["ui_stage"] = "made_up_stage"
            fixture.flush_registry()
            with self.assertRaisesRegex(ContractError, "module UI stages"):
                ModuleRegistry.load(fixture.root)

    def test_registry_rejects_dependency_order_inversion(self) -> None:
        with FixtureProject() as fixture:
            clinical = fixture.module_entry("clinical_simulation")
            roi = fixture.module_entry("roi_calculator")
            clinical["order"], roi["order"] = roi["order"], clinical["order"]
            fixture.flush_registry()

            with self.assertRaisesRegex(
                ContractError, "roi_calculator depends on clinical_simulation"
            ):
                ModuleRegistry.load(fixture.root)

    def test_check_git_requires_metadata_for_a_pinned_module(self) -> None:
        with FixtureProject() as fixture, self.assertRaisesRegex(
            ContractError, "missing Git metadata"
        ):
            ModuleRegistry.load(fixture.root).preflight(check_git=True)

    def test_preflight_rejects_contract_path_escape(self) -> None:
        with FixtureProject() as fixture:
            outside = fixture.root.parent / f"{fixture.root.name}-outside-schema.json"
            try:
                write_json(outside, {"type": "object"})
                fixture.module_entry("evidence_mapper")["input_schema"] = f"../{outside.name}"
                fixture.flush_registry()
                with self.assertRaisesRegex(ContractError, "escapes allowed root"):
                    ModuleRegistry.load(fixture.root).preflight(check_git=False)
            finally:
                outside.unlink(missing_ok=True)

    def test_strict_json_rejects_duplicate_keys_in_registry(self) -> None:
        with FixtureProject() as fixture:
            path = fixture.root / "module-lock.json"
            path.write_text(
                '{"registry_version":"1.0","registry_version":"1.0"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "duplicate JSON object key"):
                ModuleRegistry.load(fixture.root)


if __name__ == "__main__":
    unittest.main()
