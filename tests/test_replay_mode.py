from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.adapters import build_module_input
from labrador_orchestrator.contracts import load_json
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import VALID_SETUP, FixtureProject

ROOT = Path(__file__).resolve().parents[1]


class ReplayModeContractTests(unittest.TestCase):
    def test_pinned_simulation_runs_its_native_local_cache_resolver(self) -> None:
        registry = ModuleRegistry.load(ROOT)
        module = next(item for item in registry.modules if item.module_id == "simulation")
        self.assertEqual(module.command[0], "python3")
        self.assertTrue(module.command[1].endswith("scripts/run_simulation_replay.py"))
        self.assertIn("--recorded-output", module.command)
        self.assertEqual(module.mode, "replay")

        with tempfile.TemporaryDirectory(prefix="labrador-native-simulation-") as temporary:
            output = Path(temporary) / "output.json"
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "simulation",
                    "run",
                    "--input",
                    str(ROOT / "fixtures/golden/simulation-input.json"),
                    "--output",
                    str(output),
                ],
                cwd=module.module_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            dossier = load_json(output)
            extensions = dossier["interpretability"]["extensions"]
            self.assertEqual(extensions["runtime_maturity"], "LOCAL")
            self.assertEqual(extensions["output_origin"], "cached_dossier")
            self.assertIn("CACHED_DOSSIER", extensions["qualifiers"])
            self.assertEqual(extensions["cache_hit"]["kind"], "exact")

    def test_replay_executes_command_without_claiming_live_science(self) -> None:
        with FixtureProject(modes={"simulation": "replay"}) as fixture:
            registry = ModuleRegistry.load(fixture.root)
            registry.preflight(check_git=False)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP), registry=registry))

            manifest = SequentialRunner(fixture.root, registry, store).run(created["run_id"])

            simulation = next(
                stage for stage in manifest["stages"] if stage["module_id"] == "simulation"
            )
            self.assertEqual(simulation["status"], "COMPLETE_WITH_WARNINGS")
            self.assertEqual(simulation["execution_status"], "COMPLETE")
            self.assertEqual(simulation["output_origin"], "CACHED")
            self.assertEqual(simulation["reason_code"], "PINNED_ARTIFACT_REVALIDATED")
            self.assertEqual(simulation["attempts"][-1]["status"], "COMPLETE")
            self.assertEqual(manifest["run_status"], "COMPLETED_WITH_WARNINGS")

            replay_events = [
                event["event"]
                for event in fixture.read_trace()
                if event["stage"] == "simulation"
            ]
            self.assertEqual(replay_events, ["start", "end"])

    def test_golden_simulation_input_is_the_recorded_request_verbatim(self) -> None:
        registry = ModuleRegistry.load(ROOT)
        setup = validate_setup(dict(VALID_SETUP), registry=registry)

        adapted = build_module_input(
            ROOT,
            "simulation",
            setup=setup,
            outputs={},
        )

        self.assertEqual(adapted, load_json(ROOT / "fixtures/golden/simulation-input.json"))

    def test_recorded_replay_refuses_an_identity_mismatch(self) -> None:
        request = load_json(ROOT / "fixtures/golden/simulation-input.json")
        request["uniprot_accession"] = "P00533"

        with tempfile.TemporaryDirectory(prefix="labrador-replay-red-") as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / "input.json"
            output_path = temporary_root / "output.json"
            input_path.write_text(json.dumps(request), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_simulation_replay.py"),
                    "--module-root",
                    str(ROOT / ".modules/simulation"),
                    "--recorded-output",
                    str(ROOT / "fixtures/golden/fallbacks/simulation.json"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("RECORDED_INPUT_MISMATCH", result.stderr)
            self.assertFalse(output_path.exists())

    def test_evidence_replay_refuses_a_different_search_depth(self) -> None:
        request = load_json(ROOT / "fixtures/golden/evidence.request.json")
        request["depth"] = "quick"

        with tempfile.TemporaryDirectory(prefix="labrador-evidence-replay-red-") as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / "input.json"
            output_path = temporary_root / "output.json"
            input_path.write_text(json.dumps(request), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_evidence_replay.py"),
                    "--module-root",
                    str(ROOT / ".modules/research-evidence-mapper"),
                    "--recorded-input",
                    str(ROOT / "fixtures/golden/evidence.request.json"),
                    "--recorded-output",
                    str(ROOT / "fixtures/golden/fallbacks/evidence.json"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("RECORDED_INPUT_MISMATCH", result.stderr)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
