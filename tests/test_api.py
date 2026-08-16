from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.server import create_server
from tests._support import VALID_SETUP, FixtureProject
from tests.test_generalized_contract import custom_request


class RunningServer:
    def __init__(self, fixture: FixtureProject):
        self.server = create_server(fixture.root, "127.0.0.1", 0)
        self.server.RequestHandlerClass.log_message = lambda *_args: None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        request_headers = dict(headers or {})
        encoded: bytes | None = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = (
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            payload,
        )
        connection.close()
        return result

    def json_request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        status, response_headers, raw = self.request(method, path, body, headers)
        return status, response_headers, json.loads(raw)

    def wait_for_terminal(self, run_id: str, timeout: float = 4) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            status, _, last = self.json_request("GET", f"/api/runs/{run_id}/state")
            if status == 200 and last["runStatus"] in {
                "COMPLETED",
                "COMPLETED_WITH_WARNINGS",
                "FAILED",
            }:
                return last
            time.sleep(0.01)
        raise AssertionError(f"run did not become terminal: {last}")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = FixtureProject()
        self.running = RunningServer(self.fixture)

    def tearDown(self) -> None:
        self.running.close()
        self.fixture.close()

    def test_root_health_and_missing_run_routes(self) -> None:
        status, headers, body = self.running.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn(b'data-screen="setup"', body)
        self.assertNotIn(str(self.fixture.root).encode(), body)

        status, _, health = self.running.json_request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["schemaVersion"], "labrador.health.v1")

        status, _, error = self.running.json_request("GET", "/api/runs/LR-DOES-NOT-EXIST/state")
        self.assertEqual(status, 404)
        self.assertEqual(error["error"]["code"], "RUN_NOT_FOUND")

    def test_post_run_and_poll_terminal_state(self) -> None:
        status, headers, created = self.running.json_request("POST", "/api/runs", VALID_SETUP)

        self.assertEqual(status, 202)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(created["schemaVersion"], "labrador.ui-run-state.v1")
        self.assertTrue(created["runId"].startswith("LR-"))
        final = self.running.wait_for_terminal(created["runId"])
        self.assertEqual(final["runStatus"], "COMPLETED")
        self.assertEqual([stage["executionStatus"] for stage in final["stages"]], ["COMPLETE"] * 5)
        self.assertNotIn(str(self.fixture.root), json.dumps(final))

    def test_post_versioned_custom_run_uses_the_analyst_frame(self) -> None:
        status, _, created = self.running.json_request("POST", "/api/runs", custom_request())

        self.assertEqual(status, 202)
        final = self.running.wait_for_terminal(created["runId"])
        self.assertEqual(final["setupSnapshot"]["programIdentity"]["targetSymbol"], "EGFR")
        self.assertEqual(final["setupSnapshot"]["programIdentity"]["indication"], "Glioblastoma")

    def test_post_run_validates_body_and_ten_point_ranges(self) -> None:
        invalid = dict(VALID_SETUP, hypothesisRange=[0, 11])
        status, _, error = self.running.json_request("POST", "/api/runs", invalid)
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "INVALID_REQUEST")
        self.assertIn("1 <= lower <= upper <= 10", error["error"]["message"])

        status, _, raw = self.running.request("POST", "/api/runs", body="not an object")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "INVALID_REQUEST")

    def test_idempotency_key_returns_same_run_without_duplicate_directory(self) -> None:
        headers = {"Idempotency-Key": "one-browser-click"}
        first_status, _, first = self.running.json_request(
            "POST", "/api/runs", VALID_SETUP, headers
        )
        second_status, _, second = self.running.json_request(
            "POST", "/api/runs", VALID_SETUP, headers
        )

        self.assertEqual(first_status, 202)
        self.assertEqual(second_status, 200)
        self.assertEqual(first["runId"], second["runId"])
        run_dirs = [path for path in (self.fixture.root / "runs").iterdir() if path.is_dir()]
        self.assertEqual(len(run_dirs), 1)

    def test_idempotency_key_reuse_with_different_payload_is_a_conflict(self) -> None:
        headers = {"Idempotency-Key": "one-logical-request"}
        first_status, _, first = self.running.json_request(
            "POST", "/api/runs", VALID_SETUP, headers
        )
        changed = dict(VALID_SETUP, maxLiteraturePapers=41)
        second_status, _, second = self.running.json_request(
            "POST", "/api/runs", changed, headers
        )

        self.assertEqual(first_status, 202)
        self.assertEqual(second_status, 409)
        self.assertEqual(second["error"]["code"], "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")
        self.assertTrue(first["runId"].startswith("LR-"))
        run_dirs = [path for path in (self.fixture.root / "runs").iterdir() if path.is_dir()]
        self.assertEqual(len(run_dirs), 1)

    def test_idempotency_key_survives_local_server_restart(self) -> None:
        headers = {"Idempotency-Key": "survive-restart"}
        first_status, _, first = self.running.json_request(
            "POST", "/api/runs", VALID_SETUP, headers
        )
        self.assertEqual(first_status, 202)
        self.running.wait_for_terminal(first["runId"])
        self.running.close()
        self.running = RunningServer(self.fixture)

        second_status, _, second = self.running.json_request(
            "POST", "/api/runs", VALID_SETUP, headers
        )

        self.assertEqual(second_status, 200)
        self.assertEqual(second["runId"], first["runId"])
        run_dirs = [path for path in (self.fixture.root / "runs").iterdir() if path.is_dir()]
        self.assertEqual(len(run_dirs), 1)

    def test_highlander_requires_terminal_state_and_gap_acknowledgement(self) -> None:
        self.running.close()
        self.fixture.close()
        self.fixture = FixtureProject(
            behaviors={"evidence_mapper": "slow"},
            timeouts={"evidence_mapper": 0.3},
        )
        self.running = RunningServer(self.fixture)

        _, _, created = self.running.json_request("POST", "/api/runs", VALID_SETUP)
        run_id = created["runId"]
        status, _, error = self.running.json_request(
            "POST", f"/api/runs/{run_id}/highlander", {"acknowledgeGaps": True}
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "HIGHLANDER_NOT_READY")

        final = self.running.wait_for_terminal(run_id)
        self.assertTrue(final["highlander"]["ready"])
        status, _, error = self.running.json_request(
            "POST", f"/api/runs/{run_id}/highlander", {"acknowledgeGaps": False}
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "GAP_ACKNOWLEDGEMENT_REQUIRED")

        status, _, launched = self.running.json_request(
            "POST", f"/api/runs/{run_id}/highlander", {"acknowledgeGaps": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(launched["highlander"]["launched"])
        self.assertIsNotNone(launched["highlander"]["packetSnapshot"])
        first_job = launched["highlander"]["jobId"]

        status, _, repeated = self.running.json_request(
            "POST", f"/api/runs/{run_id}/highlander", {"acknowledgeGaps": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(repeated["highlander"]["jobId"], first_job)

    def test_highlander_requires_a_candidate_not_merely_terminal_artifacts(self) -> None:
        self.running.close()
        self.fixture.close()
        self.fixture = FixtureProject(behaviors={"hypothesis_generator": "no_candidate"})
        self.running = RunningServer(self.fixture)

        _, _, created = self.running.json_request("POST", "/api/runs", VALID_SETUP)
        final = self.running.wait_for_terminal(created["runId"])

        self.assertFalse(final["highlander"]["ready"])
        self.assertEqual(final["highlander"]["counts"]["blocked"], 1)
        status, _, error = self.running.json_request(
            "POST",
            f"/api/runs/{created['runId']}/highlander",
            {"acknowledgeGaps": True},
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "HIGHLANDER_NOT_READY")

    def test_static_path_traversal_symlink_and_internal_files_are_not_served(self) -> None:
        outside = self.fixture.root / "module-lock.json"
        symlink = self.fixture.root / "ui" / "escape.json"
        symlink.symlink_to(outside)
        for path in (
            "/../module-lock.json",
            "/%2e%2e%2fmodule-lock.json",
            "/escape.json",
            "/verify_mockup.mjs",
            "/runs/anything/manifest.json",
        ):
            with self.subTest(path=path):
                status, _, error = self.running.json_request("GET", path)
                self.assertEqual(status, 404)
                self.assertIn(error["error"]["code"], {"NOT_FOUND", "RUN_NOT_FOUND"})

    def test_unsupported_method_returns_json_405(self) -> None:
        status, headers, error = self.running.json_request("PUT", "/api/runs")
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "GET, POST")
        self.assertEqual(error["error"]["code"], "METHOD_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
