from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests._support import FixtureProject
from tests.test_api import RunningServer

FRONTEND_SETUP = {
    "clinical_indication": {"submitted_text": "Rheumatoid arthritis"},
    "biomarker_exploration_range": {"lower": 1, "upper": 10},
    "maximum_biomarkers": 3,
    "maximum_literature_papers": 40,
    "hypothesis_boldness_range": {"lower": 1, "upper": 10},
    "maximum_hypotheses_per_biomarker": 3,
}


class FrontendAPIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = FixtureProject(
            behaviors={
                "evidence_mapper": "interpretability",
                "clinical_simulation": "interpretability",
            }
        )
        self.running = RunningServer(self.fixture)

    def tearDown(self) -> None:
        self.running.close()
        self.fixture.close()

    def test_exact_frontend_setup_creates_run_and_returns_small_receipt(self) -> None:
        status, headers, created = self.running.json_request(
            "POST", "/api/runs", FRONTEND_SETUP
        )

        self.assertEqual(status, 201)
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(set(created), {"run"})
        self.assertTrue(created["run"]["run_id"].startswith("LR-"))

        stored = json.loads(
            (
                self.fixture.root
                / "runs"
                / created["run"]["run_id"]
                / "00_program_input.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(stored["clinicalIndication"], "Rheumatoid arthritis")
        self.assertEqual(stored["requestSchemaVersion"], "labrador.frontend-run-setup.v0")

    def test_snapshot_matches_frontend_wire_contract_and_preserves_payloads(self) -> None:
        _, _, created = self.running.json_request("POST", "/api/runs", FRONTEND_SETUP)
        run_id = created["run"]["run_id"]

        first_status, _, first = self.running.json_request(
            "GET", f"/api/runs/{run_id}/snapshot"
        )
        final_state = self.running.wait_for_terminal(run_id)
        final_status, _, final = self.running.json_request(
            "GET", f"/api/runs/{run_id}/snapshot"
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(final_status, 200)
        self.assertEqual(final["run_id"], run_id)
        self.assertGreaterEqual(final["last_event_id"], first["last_event_id"])
        self.assertEqual(
            [stage["stage_id"] for stage in final["stages"]],
            ["biomarker", "hypothesis", "roi", "recruitability", "simulation"],
        )
        allowed = {"QUEUED", "RUNNING", "COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED"}
        self.assertTrue(all(stage["execution_status"] in allowed for stage in final["stages"]))
        self.assertTrue(
            all(
                stage["result_status"] == stage["execution_status"]
                for stage in final["stages"]
            )
        )
        self.assertTrue(final["programs"])
        program = final["programs"][0]
        self.assertEqual(program["lane"], 0)
        self.assertIsNone(program["metrics"]["support"])
        self.assertEqual(
            program["station_payloads"]["recruitability"]["interpretability"]
            ["schema_version"],
            "1.0.0",
        )
        biomarker_stage = next(
            stage for stage in final["stages"] if stage["stage_id"] == "biomarker"
        )
        self.assertEqual(biomarker_stage["module_execution_status"], "COMPLETE")
        self.assertEqual(biomarker_stage["output_origin"], "LIVE")
        self.assertEqual(biomarker_stage["runtime_maturity"], "LOCAL")
        self.assertIsInstance(biomarker_stage["warnings"], list)
        self.assertEqual(
            final["biomarkers"][0]["station_payload"]["interpretability"]["schema_version"],
            "1.0.0",
        )
        self.assertEqual(final["last_event_id"], final_state["revision"])

    def test_server_prefers_the_pinned_functional_frontend_checkout(self) -> None:
        self.running.close()
        functional_root = self.fixture.root / ".frontend" / "app"
        functional_root.mkdir(parents=True)
        (functional_root / "index.html").write_text(
            "<!doctype html><title>Functional judging frontend</title>",
            encoding="utf-8",
        )
        self.running = RunningServer(self.fixture)

        status, _, body = self.running.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn(b"Functional judging frontend", body)
        self.assertNotIn(b'data-screen="setup"', body)

    def test_meta_and_options_are_cross_origin_safe(self) -> None:
        status, headers, raw = self.running.request(
            "OPTIONS",
            "/api/runs",
            headers={
                "Origin": "http://localhost:4173",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertIn("POST", headers["access-control-allow-methods"])

        status, headers, meta = self.running.json_request("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(meta["backend"], "labrador-orchestrator")
        self.assertEqual(len(meta["modules"]), 5)
        self.assertIn("PROPOSED TARGET", meta["truth_labels"])
        self.assertIn("MIXED LIVE / CACHED REPLAY EXECUTION", meta["truth_labels"])

    def test_frontend_setup_is_strict_and_does_not_guess_another_program(self) -> None:
        malformed = dict(FRONTEND_SETUP)
        malformed["unknown"] = True
        status, _, error = self.running.json_request("POST", "/api/runs", malformed)
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "INVALID_REQUEST")

        unsupported = json.loads(json.dumps(FRONTEND_SETUP))
        unsupported["clinical_indication"]["submitted_text"] = "Glioblastoma"
        status, _, error = self.running.json_request("POST", "/api/runs", unsupported)
        self.assertEqual(status, 400)
        self.assertIn("analyst frame", error["error"]["message"])
        self.assertEqual(list((self.fixture.root / "runs").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
