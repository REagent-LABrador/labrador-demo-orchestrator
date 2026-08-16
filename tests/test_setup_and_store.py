from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import ContractError, sha256_json
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import STAGES, VALID_SETUP, FixtureProject


class SetupValidationTests(unittest.TestCase):
    def test_valid_setup_accepts_ra_alias_and_ten_point_ranges(self) -> None:
        value = dict(VALID_SETUP)
        value["clinicalIndication"] = " RA "

        validated = validate_setup(value)

        self.assertEqual(validated["clinicalIndication"], " RA ")
        self.assertEqual(validated["normalizedIndication"], "Rheumatoid arthritis")
        self.assertEqual(validated["biomarkerRange"], [1, 10])
        self.assertEqual(validated["hypothesisRange"], [1, 10])

    def test_setup_rejects_invalid_ten_point_ranges(self) -> None:
        cases = (
            ("biomarkerRange", [0, 10]),
            ("biomarkerRange", [1, 11]),
            ("biomarkerRange", [8, 2]),
            ("hypothesisRange", [1]),
            ("hypothesisRange", [1, 2.5]),
            ("hypothesisRange", [True, 10]),
        )
        for field, invalid in cases:
            with self.subTest(field=field, invalid=invalid):
                value = dict(VALID_SETUP)
                value[field] = invalid
                with self.assertRaises(ContractError):
                    validate_setup(value)

    def test_setup_rejects_invalid_counts_unknown_fields_and_other_indications(self) -> None:
        cases = (
            ("maxBiomarkers", 0),
            ("maxBiomarkers", 6),
            ("maxLiteraturePapers", 101),
            ("maxHypothesesPerBiomarker", True),
        )
        for field, invalid in cases:
            with self.subTest(field=field, invalid=invalid):
                value = dict(VALID_SETUP)
                value[field] = invalid
                with self.assertRaises(ContractError):
                    validate_setup(value)

        wrong_indication = dict(VALID_SETUP, clinicalIndication="Glioblastoma")
        with self.assertRaisesRegex(ContractError, "supports only"):
            validate_setup(wrong_indication)

        unknown = dict(VALID_SETUP, surprise="field")
        with self.assertRaisesRegex(ContractError, "unknown setup fields"):
            validate_setup(unknown)


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = FixtureProject()
        self.registry = ModuleRegistry.load(self.fixture.root)
        self.store = RunStore(self.fixture.root, self.registry)
        self.setup = validate_setup(dict(VALID_SETUP))

    def tearDown(self) -> None:
        self.fixture.close()

    def test_create_writes_immutable_setup_and_five_queued_stages(self) -> None:
        manifest = self.store.create(self.setup)

        self.assertTrue(manifest["run_id"].startswith("LR-"))
        self.assertEqual(manifest["configuration_hash"], sha256_json(self.setup))
        self.assertEqual(
            [stage["module_id"] for stage in manifest["stages"]], [item[0] for item in STAGES]
        )
        self.assertEqual(
            [stage["execution_status"] for stage in manifest["stages"]], ["QUEUED"] * 5
        )
        self.assertEqual([stage["output_origin"] for stage in manifest["stages"]], ["NOT_RUN"] * 5)
        self.assertEqual(self.store.read(manifest["run_id"]), manifest)

        snapshot = json.loads(
            (self.store.run_dir(manifest["run_id"]) / "00_program_input.json").read_text()
        )
        self.assertEqual(snapshot, self.setup)

    def test_mutation_cannot_change_setup_or_configuration_hash(self) -> None:
        manifest = self.store.create(self.setup)
        run_id = manifest["run_id"]

        def mutate_setup(value: dict[str, object]) -> None:
            value["setup"]["maxBiomarkers"] = 5  # type: ignore[index]

        with self.assertRaisesRegex(ContractError, "run setup is immutable"):
            self.store.mutate(run_id, "BAD_MUTATION", mutate_setup)
        self.assertEqual(
            self.store.read(run_id)["configuration_hash"], manifest["configuration_hash"]
        )
        self.assertEqual(self.store.read(run_id)["setup"], self.setup)

        def mutate_hash(value: dict[str, object]) -> None:
            value["configuration_hash"] = "sha256:tampered"

        with self.assertRaisesRegex(ContractError, "configuration hash is immutable"):
            self.store.mutate(run_id, "BAD_HASH", mutate_hash)

    def test_successful_mutation_increments_revision_and_appends_event(self) -> None:
        manifest = self.store.create(self.setup)
        run_id = manifest["run_id"]

        updated = self.store.mutate(
            run_id,
            "RUN_STARTED",
            lambda value: value.update({"run_status": "RUNNING"}),
            {"stage": "biomarker"},
        )

        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["run_status"], "RUNNING")
        events = [
            json.loads(line)
            for line in (self.store.run_dir(run_id) / "events.ndjson").read_text().splitlines()
        ]
        self.assertEqual([event["event"] for event in events], ["RUN_CREATED", "RUN_STARTED"])
        self.assertEqual(events[-1]["payload"], {"stage": "biomarker"})

    def test_terminal_stage_cannot_regress_or_rewrite_module_identity(self) -> None:
        manifest = self.store.create(self.setup)
        final = SequentialRunner(self.fixture.root, self.registry, self.store).run(
            manifest["run_id"]
        )
        run_id = final["run_id"]
        revision = final["revision"]
        event_path = self.store.run_dir(run_id) / "events.ndjson"
        events_before = event_path.read_text()

        def regress(value: dict[str, object]) -> None:
            value["stages"][0]["status"] = "RUNNING"  # type: ignore[index]

        with self.assertRaisesRegex(ContractError, "terminal stage status is immutable"):
            self.store.mutate(run_id, "BAD_REGRESSION", regress)

        def rewrite_provenance(value: dict[str, object]) -> None:
            value["stages"][0]["module"]["git_sha"] = "b" * 40  # type: ignore[index]

        with self.assertRaisesRegex(ContractError, "module identity is immutable"):
            self.store.mutate(run_id, "BAD_PROVENANCE", rewrite_provenance)

        persisted = self.store.read(run_id)
        self.assertEqual(persisted["revision"], revision)
        self.assertEqual(event_path.read_text(), events_before)

    def test_run_id_rejects_path_traversal(self) -> None:
        for run_id in ("../../etc", "LR-../escape", "/tmp/LR-EVIL", "not-a-run"):
            with self.subTest(run_id=run_id), self.assertRaises(ContractError):
                self.store.run_dir(run_id)


if __name__ == "__main__":
    unittest.main()
