from __future__ import annotations

import base64
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import ContractError
from labrador_orchestrator.highlander_packet import (
    HighlanderSpec,
    build_scientific_comparison_request,
    canonical_json_sha256,
    raw_sha256,
)
from labrador_orchestrator.registry import ModuleRegistry
from labrador_orchestrator.runner import SequentialRunner
from labrador_orchestrator.server import LabradorApplication
from labrador_orchestrator.store import RunStore, validate_setup
from tests._support import FixtureProject, write_json
from tests.test_api import RunningServer
from tests.test_scientific_runner import (
    configure_scientific_fixture,
    scientific_request,
)

FAKE_HIGHLANDER = r'''from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
compare = sub.add_parser("compare")
compare.add_argument("--request", required=True)
compare.add_argument("--out", required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding="utf-8"))
candidate_ids = [item["hypothesisId"] for item in request["candidatePackets"]]
incomplete = [
    item["hypothesisId"]
    for item in request["candidatePackets"]
    if any(
        module["executionStatus"] not in {"COMPLETE", "COMPLETED", "SUCCEEDED"}
        for module in item["modulePackets"]
    )
]
frontier = [item for item in candidate_ids if item not in incomplete]
result = {
    "schemaVersion": "highlander.portfolio-result.v1",
    "snapshotId": request["snapshotId"],
    "createdAt": request["createdAt"],
    "inputPackets": [item["packetHash"] for item in request["candidatePackets"]],
    "objectivePolicy": request["objectivePolicy"],
    "candidates": candidate_ids,
    "comparisonGroups": [],
    "frontier": frontier,
    "dominated": [],
    "incomparable": incomplete,
    "dominanceRelationships": [],
    "equivalenceGroups": [],
    "qualifiers": ["FAKE_CLI_TEST"],
    "nextEvidenceAction": {
        "actionId": "action-fixture",
        "actionType": "test_gap",
        "target": "gap-fixture",
        "description": "Resolve the largest producer-grounded evidence gap.",
        "producerModuleId": "hypothesis-generator",
        "producerOutputSha256": "0" * 64,
        "sourceId": "gap-fixture",
        "sourcePath": "$.hypothesis.asks[0]",
        "candidateIds": candidate_ids,
        "candidateCount": len(candidate_ids),
        "selectionBasis": "PRODUCER_EMITTED_MOST_BRANCHES_STABLE_TIEBREAK",
    },
}
Path(args.out).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
'''


def configure_highlander(fixture: FixtureProject) -> None:
    module_root = fixture.root / "modules" / "hypothesis_highlander"
    package = module_root / "highlander"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(FAKE_HIGHLANDER, encoding="utf-8")
    fixture.registry_json["portfolio_consumer"] = {
        "id": "hypothesis_highlander",
        "repository": "test/hypothesis-highlander",
        "repository_url": "https://example.invalid/highlander.git",
        "commit": "b" * 40,
        "module_root": "modules/hypothesis_highlander",
        "mode": "SERVER_NATIVE",
        "active_comparison": "HIGHLANDER_PACKET_CONSUMER",
        "command": [
            sys.executable,
            "-m",
            "highlander",
            "compare",
            "--request",
            "{request}",
            "--out",
            "{output}",
        ],
        "timeout_seconds": 240,
        "adapter_version": "packet-adapters.v1",
        "producer_contracts": {
            "evidence_mapper": {
                "module_id": "research-evidence-mapper",
                "native_schema_id": "EvidenceGraph",
                "native_schema_version": "1.1",
            },
            "hypothesis_generator": {
                "module_id": "hypothesis-generator",
                "native_schema_id": "https://labrador.dev/schemas/headless-output.schema.json",
                "native_schema_version": "unversioned",
            },
            "clinical_simulation": {
                "module_id": "trial-recruitment-forecaster",
                "native_schema_id": "https://github.com/REagent-LABrador/clinical_simulation/schemas/output.schema.json",
                "native_schema_version": "1.0.0",
            },
            "roi_calculator": {
                "module_id": "therapeutic-program-economics",
                "native_schema_id": "labrador_roi.engine.AnalysisResult",
                "native_schema_version": "1.3.0",
            },
            "simulation": {
                "module_id": "small-molecule-tractability-review",
                "native_schema_id": "https://github.com/REagent-LABrador/simulation/schema/output.schema.json",
                "native_schema_version": "1.0.0",
            },
        },
        "objective_policy": {
            "policyId": "labrador.scientific-portfolio.v1",
            "objectives": [
                {"objectiveId": "support", "direction": "MAX"},
                {"objectiveId": "novelty", "direction": "MAX"},
                {"objectiveId": "testability", "direction": "MAX"},
                {"objectiveId": "contradiction_risk", "direction": "MIN"},
                {"objectiveId": "recruitability", "direction": "MAX"},
                {"objectiveId": "roi", "direction": "MAX"},
            ],
        },
    }
    fixture.flush_registry()


def completed_scientific_run(
    fixture: FixtureProject,
    *,
    fail_focus: str | None = None,
    presentation: str = "SCIENTIFIC",
    roi_structured_error: bool = False,
) -> tuple[RunStore, dict]:
    configure_scientific_fixture(
        fixture,
        fail_focus=fail_focus,
        roi_structured_error=roi_structured_error,
    )
    configure_highlander(fixture)
    registry = ModuleRegistry.load(fixture.root)
    store = RunStore(fixture.root, registry)
    created = store.create(
        validate_setup(scientific_request(presentation=presentation), registry=registry)
    )
    return store, SequentialRunner(fixture.root, registry, store).run(created["run_id"])


class ScientificHighlanderTests(unittest.TestCase):
    def test_packet_hashes_bind_exact_terminal_artifacts_with_rfc8785(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            spec = HighlanderSpec.load(fixture.root)
            built = build_scientific_comparison_request(
                fixture.root, manifest, spec, created_at="2026-08-16T12:00:00Z"
            )

            self.assertEqual(len(built.request["candidatePackets"]), 3)
            candidate = built.request["candidatePackets"][0]
            body = {key: value for key, value in candidate.items() if key != "packetHash"}
            self.assertEqual(candidate["packetHash"], canonical_json_sha256(body))
            self.assertEqual(len(candidate["modulePackets"]), 5)
            for packet in candidate["modulePackets"]:
                envelope = {
                    key: value
                    for key, value in packet.items()
                    if key != "envelopeCanonicalSha256"
                }
                self.assertEqual(
                    packet["envelopeCanonicalSha256"],
                    canonical_json_sha256(envelope),
                )
                input_raw = base64.b64decode(
                    built.request["artifactPayloads"][packet["inputArtifactRef"]]
                )
                self.assertEqual(packet["inputRawSha256"], raw_sha256(input_raw))
                if packet["outputArtifactRef"] is not None:
                    output_raw = base64.b64decode(
                        built.request["artifactPayloads"][packet["outputArtifactRef"]]
                    )
                    self.assertEqual(packet["outputRawSha256"], raw_sha256(output_raw))
                else:
                    execution_raw = base64.b64decode(
                        built.request["artifactPayloads"][packet["executionArtifactRef"]]
                    )
                    self.assertEqual(
                        packet["executionArtifactRawSha256"], raw_sha256(execution_raw)
                    )

    def test_fake_python_module_cli_is_invoked_and_result_is_exposed(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            application = LabradorApplication(fixture.root)

            state = application.launch_highlander(
                manifest["run_id"], {"acknowledgeGaps": True}
            )
            snapshot = application.frontend_snapshot(manifest["run_id"])

            self.assertTrue(state["highlander"]["launched"])
            self.assertEqual(
                snapshot["highlander"]["result"]["schemaVersion"],
                "highlander.portfolio-result.v1",
            )
            self.assertIsNotNone(
                snapshot["highlander"]["result"]["nextEvidenceAction"]
            )
            self.assertEqual(
                set(snapshot["highlander"]["result"]["nextEvidenceAction"]),
                {
                    "actionId",
                    "actionType",
                    "target",
                    "description",
                    "producerModuleId",
                    "producerOutputSha256",
                    "sourceId",
                    "sourcePath",
                    "candidateIds",
                    "candidateCount",
                    "selectionBasis",
                },
            )
            for field in (
                "request_ref",
                "request_raw_hash",
                "request_canonical_hash",
                "result_ref",
                "result_raw_hash",
                "result_canonical_hash",
                "execution_ref",
            ):
                self.assertTrue(snapshot["highlander"][field], field)

    def test_snapshot_api_exposes_server_native_pareto_and_action(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            running = RunningServer(fixture)
            try:
                status, _, launched = running.json_request(
                    "POST",
                    f"/api/runs/{manifest['run_id']}/highlander",
                    {"acknowledgeGaps": True},
                )
                self.assertEqual(status, 200)
                self.assertTrue(launched["highlander"]["launched"])

                status, _, snapshot = running.json_request(
                    "GET", f"/api/runs/{manifest['run_id']}/snapshot"
                )
                self.assertEqual(status, 200)
                result = snapshot["highlander"]["result"]
                self.assertEqual(
                    result["schemaVersion"], "highlander.portfolio-result.v1"
                )
                self.assertIn("frontier", result)
                self.assertEqual(
                    result["nextEvidenceAction"]["selectionBasis"],
                    "PRODUCER_EMITTED_MOST_BRANCHES_STABLE_TIEBREAK",
                )
            finally:
                running.close()

    def test_failed_module_packets_remain_visible_with_exact_terminal_reasons(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture, fail_focus="b2")
            application = LabradorApplication(fixture.root)
            application.launch_highlander(manifest["run_id"], {"acknowledgeGaps": True})

            run_dir = fixture.root / "runs" / manifest["run_id"]
            request = json.loads(
                (run_dir / "highlander" / "request.json").read_text(encoding="utf-8")
            )
            failed = next(
                item for item in request["candidatePackets"] if item["hypothesisId"].endswith("b2")
            )
            modules = {item["moduleId"]: item for item in failed["modulePackets"]}
            hypgen = modules["hypothesis-generator"]
            clinical = modules["trial-recruitment-forecaster"]
            tractability = modules["small-molecule-tractability-review"]
            self.assertEqual(hypgen["executionStatus"], "FAILED")
            self.assertIsNone(hypgen["outputArtifactRef"])
            self.assertIsNotNone(hypgen["executionArtifactRef"])
            self.assertEqual(
                hypgen["executionReason"],
                "CANNOT_COMPLETE: CREDENTIAL_MISSING: ANTHROPIC_API_KEY is missing",
            )
            self.assertEqual(clinical["executionStatus"], "FAILED")
            self.assertIn("UPSTREAM_FAILED", clinical["executionReason"])
            self.assertEqual(tractability["executionStatus"], "COMPLETE")
            self.assertEqual(tractability["dependsOn"], [])
            result = json.loads(
                (run_dir / "highlander" / "result.json").read_text(encoding="utf-8")
            )
            self.assertIn(failed["hypothesisId"], result["incomparable"])

    def test_all_failed_hypgen_branches_can_launch_as_incomparable_packets(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture, fail_focus="*")
            self.assertTrue(manifest["highlander"]["ready"])
            self.assertTrue(
                all(
                    branch["nodes"]["hypothesis_generator"]["status"]
                    == "CANNOT_COMPLETE"
                    for branch in manifest["scientific"]["branches"]
                )
            )
            application = LabradorApplication(fixture.root)

            application.launch_highlander(
                manifest["run_id"], {"acknowledgeGaps": True}
            )

            snapshot = application.frontend_snapshot(manifest["run_id"])
            result = snapshot["highlander"]["result"]
            self.assertEqual(len(result["incomparable"]), 3)
            self.assertEqual(result["frontier"], [])

    def test_partial_hypgen_artifact_keeps_canonical_identity_and_exact_reason(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            branch = manifest["scientific"]["branches"][0]
            hypgen = branch["nodes"]["hypothesis_generator"]
            hypgen_output = copy.deepcopy(hypgen["output"])
            hypgen_output.update(
                {
                    "status": "CANNOT_COMPLETE",
                    "roi_request": None,
                    "error": {
                        "reason_code": "ROI_REQUEST_INVALID",
                        "message": "valuation frame could not be adapted",
                    },
                }
            )
            hypgen.update(
                {
                    "status": "CANNOT_COMPLETE",
                    "reason_code": "ROI_REQUEST_INVALID",
                    "message": "valuation frame could not be adapted",
                    "output": hypgen_output,
                }
            )
            hypgen_path = fixture.root / "runs" / hypgen["output_ref"]
            write_json(hypgen_path, hypgen_output)
            roi = branch["nodes"]["roi_calculator"]
            roi_output = {
                "status": "CANNOT_COMPLETE",
                "reason_code": "UPSTREAM_FAILED",
                "message": "required upstream ROI request did not complete",
            }
            roi.update(
                {
                    "status": "CANNOT_COMPLETE",
                    "reason_code": "UPSTREAM_FAILED",
                    "message": roi_output["message"],
                    "output": roi_output,
                }
            )
            write_json(fixture.root / "runs" / roi["output_ref"], roi_output)

            built = build_scientific_comparison_request(
                fixture.root,
                manifest,
                HighlanderSpec.load(fixture.root),
                created_at="2026-08-16T12:00:00Z",
            )
            candidate = built.request["candidatePackets"][0]
            modules = {item["moduleId"]: item for item in candidate["modulePackets"]}
            partial = modules["hypothesis-generator"]
            clinical = modules["trial-recruitment-forecaster"]
            self.assertEqual(candidate["hypothesisId"], "H-b1")
            self.assertEqual(partial["executionStatus"], "PARTIAL")
            self.assertEqual(
                partial["executionReason"],
                "CANNOT_COMPLETE: ROI_REQUEST_INVALID: valuation frame could not be adapted",
            )
            self.assertEqual(clinical["hypothesisId"], "H-b1")
            self.assertEqual(
                clinical["dependsOn"][0]["envelopeCanonicalSha256"],
                partial["envelopeCanonicalSha256"],
            )

    def test_roi_structured_nonzero_failure_survives_node_and_packet(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(
                fixture, roi_structured_error=True
            )
            for branch in manifest["scientific"]["branches"]:
                roi = branch["nodes"]["roi_calculator"]
                self.assertEqual(roi["status"], "CANNOT_COMPLETE")
                self.assertEqual(roi["reason_code"], "ROI_ERROR_MISSING")
                self.assertEqual(
                    roi["message"], "Required valuation assumption is missing"
                )
                self.assertEqual(roi["output"]["status"], "error")
                self.assertEqual(roi["output"]["errors"][0]["path"], ["program", "assumptions"])

            built = build_scientific_comparison_request(
                fixture.root,
                manifest,
                HighlanderSpec.load(fixture.root),
                created_at="2026-08-16T12:00:00Z",
            )
            candidate = built.request["candidatePackets"][0]
            roi_packet = next(
                item
                for item in candidate["modulePackets"]
                if item["moduleId"] == "therapeutic-program-economics"
            )
            self.assertEqual(roi_packet["executionStatus"], "FAILED")
            self.assertEqual(
                roi_packet["executionReason"],
                "CANNOT_COMPLETE: ROI_ERROR_MISSING: Required valuation assumption is missing",
            )
            terminal_raw = base64.b64decode(
                built.request["artifactPayloads"][roi_packet["executionArtifactRef"]]
            )
            terminal = json.loads(terminal_raw)
            self.assertEqual(terminal["status"], "error")
            self.assertEqual(
                terminal["errors"][0]["message"],
                "Required valuation assumption is missing",
            )

    def test_representative_presentation_cannot_change_packet_bytes_or_membership(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            spec = HighlanderSpec.load(fixture.root)
            representative = copy.deepcopy(manifest)
            representative["setup"]["presentationMode"] = "REPRESENTATIVE_DEMO"
            representative["scientific"]["presentation_mode"] = "REPRESENTATIVE_DEMO"
            created_at = "2026-08-16T12:00:00Z"

            scientific = build_scientific_comparison_request(
                fixture.root, manifest, spec, created_at=created_at
            ).request
            demo = build_scientific_comparison_request(
                fixture.root, representative, spec, created_at=created_at
            ).request

            self.assertEqual(canonical_json_sha256(scientific), canonical_json_sha256(demo))
            self.assertEqual(
                [item["hypothesisId"] for item in scientific["candidatePackets"]],
                [item["hypothesisId"] for item in demo["candidatePackets"]],
            )

    def test_duplicate_producer_hypothesis_ids_fail_before_consumer_invocation(self) -> None:
        with FixtureProject() as fixture:
            _, manifest = completed_scientific_run(fixture)
            duplicate_branch = manifest["scientific"]["branches"][1]
            hypgen = duplicate_branch["nodes"]["hypothesis_generator"]
            output = copy.deepcopy(hypgen["output"])
            output["hypothesis"]["hypothesis"]["id"] = "H-b1"
            hypgen["output"] = output
            write_json(fixture.root / "runs" / hypgen["output_ref"], output)

            with self.assertRaisesRegex(
                ContractError, "duplicate canonical hypothesis id 'H-b1'"
            ):
                build_scientific_comparison_request(
                    fixture.root,
                    manifest,
                    HighlanderSpec.load(fixture.root),
                    created_at="2026-08-16T12:00:00Z",
                )


if __name__ == "__main__":
    unittest.main()
