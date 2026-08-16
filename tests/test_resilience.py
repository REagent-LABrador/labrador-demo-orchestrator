from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import RunCoordinator, SequentialRunner
from labrador_orchestrator.server import LabradorApplication
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import VALID_SETUP, FixtureProject


class ResilienceTests(unittest.TestCase):
    def test_concurrent_highlander_launch_is_one_atomic_mutation(self) -> None:
        with FixtureProject() as fixture:
            application = LabradorApplication(fixture.root)
            state, _ = application.create_run(dict(VALID_SETUP), None)
            run_id = state["runId"]
            self.assertTrue(application.coordinator.wait(run_id, timeout=4))
            before = application.store.read(run_id)
            event_path = application.store.run_dir(run_id) / "events.ndjson"
            before_events = event_path.read_text().splitlines()

            barrier = threading.Barrier(2)
            results: list[dict[str, object]] = []
            failures: list[BaseException] = []

            def launch() -> None:
                try:
                    barrier.wait(timeout=2)
                    results.append(
                        application.launch_highlander(run_id, {"acknowledgeGaps": True})
                    )
                except BaseException as error:  # pragma: no cover - thread handoff
                    failures.append(error)

            threads = [
                threading.Thread(target=launch, name=f"launch-{index}")
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=4)

            self.assertFalse(failures, failures)
            self.assertEqual(len(results), 2)
            after = application.store.read(run_id)
            after_events = event_path.read_text().splitlines()
            launch_events = [
                json.loads(line)
                for line in after_events
                if json.loads(line)["event"] == "HIGHLANDER_LAUNCHED"
            ]
            self.assertEqual(after["revision"], before["revision"] + 1)
            self.assertEqual(len(after_events), len(before_events) + 1)
            self.assertEqual(len(launch_events), 1)
            self.assertEqual(results[0]["highlander"]["jobId"], results[1]["highlander"]["jobId"])

    def test_coordinator_exception_terminalizes_every_stage(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            runner = SequentialRunner(fixture.root, registry, store)
            created = store.create(validate_setup(dict(VALID_SETUP), registry=registry))

            def crash(run_id: str) -> None:
                def start(value: dict[str, object]) -> None:
                    value["run_status"] = "RUNNING"
                    first = value["stages"][0]  # type: ignore[index]
                    first["status"] = "RUNNING"
                    first["execution_status"] = "RUNNING"
                    first["attempts"].append(  # type: ignore[index]
                        {"attempt": 1, "status": "RUNNING", "started_at": "now"}
                    )

                store.mutate(run_id, "FORCED_START", start)
                raise RuntimeError("forced coordinator boundary failure")

            runner.run = crash  # type: ignore[method-assign]
            coordinator = RunCoordinator(runner)
            coordinator.start(created["run_id"])
            self.assertTrue(coordinator.wait(created["run_id"], timeout=3))

            final = store.read(created["run_id"])
            self.assertEqual(final["run_status"], "FAILED")
            self.assertTrue(
                all(
                    stage["status"] in {"COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED", "SKIPPED"}
                    for stage in final["stages"]
                )
            )
            self.assertEqual(final["stages"][0]["reason_code"], "ORCHESTRATOR_FAILURE")
            self.assertTrue(
                all(stage["reason_code"] == "UPSTREAM_FAILED" for stage in final["stages"][1:])
            )

    def test_application_startup_terminalizes_an_orphaned_created_run(self) -> None:
        with FixtureProject() as fixture:
            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(validate_setup(dict(VALID_SETUP), registry=registry))

            application = LabradorApplication(fixture.root)
            recovered = application.store.read(created["run_id"])

            self.assertEqual(recovered["run_status"], "FAILED")
            self.assertTrue(all(stage["status"] == "SKIPPED" for stage in recovered["stages"]))
            self.assertTrue(
                all(
                    stage["reason_code"] == "ORCHESTRATOR_RESTARTED"
                    for stage in recovered["stages"]
                )
            )

    def test_source_entrypoint_supports_cli_arguments_without_editable_install(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "app.py", "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("serve", result.stdout)
        self.assertIn("preflight", result.stdout)


if __name__ == "__main__":
    unittest.main()
