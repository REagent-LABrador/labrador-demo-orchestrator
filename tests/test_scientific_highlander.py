from __future__ import annotations

import base64
import copy
import json
import subprocess
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

ROOT = Path(__file__).resolve().parents[1]
CURRENT_ROI_COMMIT = "29bf59ea5f64e0f68a58a2e35595f471b0c73311"
CURRENT_SIMULATION_COMMIT = "a0b3d1f56805d9c9e0550291123064e492a16e5b"

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
                "native_schema_id": "urn:reagent-labrador:rnpv_roi_calculator:output:1.0.0",
                "native_schema_version": "1.0.0",
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
    def test_real_pinned_cli_adapts_current_roi_envelope_and_simulation_dossier(
        self,
    ) -> None:
        """Exercise the exact producer artifacts through the real consumer CLI.

        PR #9 intentionally does not advance producer checkouts; the coordinated
        rollup does.  Skip this stacked-boundary test until those two exact draft
        producer commits are present, rather than pretending an older checkout
        emitted the packet bytes.
        """

        roi_root = ROOT / ".modules" / "rnpv-roi-calculator"
        simulation_root = ROOT / ".modules" / "simulation"
        highlander_root = ROOT / ".modules" / "hypothesis-highlander"
        for label, module_root, expected in (
            ("ROI", roi_root, CURRENT_ROI_COMMIT),
            ("simulation", simulation_root, CURRENT_SIMULATION_COMMIT),
        ):
            if not (module_root / ".git").exists():
                self.skipTest(f"{label} checkout is unavailable; run the rollup bootstrap")
            actual = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=module_root,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            if actual != expected:
                self.skipTest(
                    f"{label} checkout is {actual}; the stacked rollup pins {expected}"
                )

        highlander_python = highlander_root / ".venv" / "bin" / "python"
        roi_cli = roi_root / ".venv" / "bin" / "rnpv-roi"
        self.assertTrue(highlander_python.is_file(), "run bootstrap for Highlander")
        self.assertTrue(roi_cli.is_file(), "run bootstrap for the ROI calculator")

        expected_highlander = json.loads(
            (ROOT / "module-lock.json").read_text(encoding="utf-8")
        )["portfolio_consumer"]["commit"]
        actual_highlander = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=highlander_root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(actual_highlander, expected_highlander)

        producer_locks = json.loads(
            (highlander_root / "highlander" / "producer_locks.json").read_text(
                encoding="utf-8"
            )
        )["modules"]
        economics_lock = producer_locks["therapeutic-program-economics"]
        self.assertEqual(
            (
                economics_lock["producerCodeVersion"],
                economics_lock["nativeSchemaId"],
                economics_lock["nativeSchemaVersion"],
            ),
            (
                CURRENT_ROI_COMMIT,
                "urn:reagent-labrador:rnpv_roi_calculator:output:1.0.0",
                "1.0.0",
            ),
        )
        self.assertEqual(
            producer_locks["small-molecule-tractability-review"][
                "producerCodeVersion"
            ],
            CURRENT_SIMULATION_COMMIT,
        )
        with FixtureProject() as fixture:
            configure_scientific_fixture(fixture)
            configure_highlander(fixture)
            contracts = fixture.registry_json["portfolio_consumer"][
                "producer_contracts"
            ]
            for module in fixture.registry_json["modules"]:
                external_id = contracts[module["id"]]["module_id"]
                module["commit"] = producer_locks[external_id][
                    "producerCodeVersion"
                ]
            fixture.flush_registry()

            registry = ModuleRegistry.load(fixture.root)
            store = RunStore(fixture.root, registry)
            created = store.create(
                validate_setup(scientific_request(), registry=registry)
            )
            manifest = SequentialRunner(fixture.root, registry, store).run(
                created["run_id"]
            )
            branch = manifest["scientific"]["branches"][0]

            roi = branch["nodes"]["roi_calculator"]
            roi_input_path = fixture.root / "runs" / roi["input_ref"]
            roi_output_path = fixture.root / "runs" / roi["output_ref"]
            roi_input_raw = (roi_root / "examples" / "input.json").read_bytes()
            roi_input_path.write_bytes(roi_input_raw)
            roi_run = subprocess.run(
                [
                    str(roi_cli),
                    "run",
                    "--input",
                    str(roi_input_path),
                    "--output",
                    str(roi_output_path),
                ],
                cwd=roi_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(roi_run.returncode, 0, roi_run.stderr)
            roi_output_raw = roi_output_path.read_bytes()
            roi_output = json.loads(roi_output_raw)
            self.assertEqual(
                (
                    roi_output["contract_version"],
                    roi_output["status"],
                    roi_output["engine_schema_version"],
                ),
                ("1.0.0", "ok", "1.3.0"),
            )
            roi["output"] = roi_output
            roi["input_hash"] = "sha256:" + raw_sha256(roi_input_raw)
            roi["output_hash"] = "sha256:" + raw_sha256(roi_output_raw)

            # Keep the producer lineage truthful: the packet-bound ROI input is
            # also the request retained in the exact HypGen output artifact.
            hypgen = branch["nodes"]["hypothesis_generator"]
            hypgen_output = copy.deepcopy(hypgen["output"])
            hypgen_output["roi_request"] = json.loads(roi_input_raw)
            hypgen["output"] = hypgen_output
            hypgen_output_path = fixture.root / "runs" / hypgen["output_ref"]
            write_json(hypgen_output_path, hypgen_output)
            hypgen["output_hash"] = "sha256:" + raw_sha256(
                hypgen_output_path.read_bytes()
            )

            simulation = branch["nodes"]["simulation"]
            simulation_input_path = fixture.root / "runs" / simulation["input_ref"]
            simulation_output_path = fixture.root / "runs" / simulation["output_ref"]
            simulation_input_raw = (
                simulation_root / "examples" / "input.json"
            ).read_bytes()
            simulation_input_path.write_bytes(simulation_input_raw)
            simulation_run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "simulation",
                    "run",
                    "--mode",
                    "replay",
                    "--input",
                    str(simulation_input_path),
                    "--output",
                    str(simulation_output_path),
                ],
                cwd=simulation_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(simulation_run.returncode, 0, simulation_run.stderr)
            simulation_output_raw = simulation_output_path.read_bytes()
            simulation_output = json.loads(simulation_output_raw)
            self.assertEqual(
                simulation_output["interpretability"]["extensions"][
                    "output_origin"
                ],
                "cached_dossier",
            )
            simulation["output"] = simulation_output
            simulation["input_hash"] = "sha256:" + raw_sha256(
                simulation_input_raw
            )
            simulation["output_hash"] = "sha256:" + raw_sha256(
                simulation_output_raw
            )

            built = build_scientific_comparison_request(
                fixture.root,
                manifest,
                HighlanderSpec.load(fixture.root),
                created_at="2026-08-16T12:00:00Z",
            )
            request_path = fixture.root / "real-highlander-request.json"
            result_path = fixture.root / "real-highlander-result.json"
            write_json(request_path, built.request)

            candidate_packet = next(
                item
                for item in built.request["candidatePackets"]
                if item["hypothesisId"] == "H-b1"
            )
            by_module = {
                item["moduleId"]: item
                for item in candidate_packet["modulePackets"]
            }
            for module_id, expected_raw in (
                ("therapeutic-program-economics", roi_output_raw),
                ("small-molecule-tractability-review", simulation_output_raw),
            ):
                packet = by_module[module_id]
                artifact_raw = base64.b64decode(
                    built.request["artifactPayloads"][packet["outputArtifactRef"]]
                )
                self.assertEqual(artifact_raw, expected_raw)
                self.assertEqual(packet["outputRawSha256"], raw_sha256(expected_raw))

            compared = subprocess.run(
                [
                    str(highlander_python),
                    "-m",
                    "highlander",
                    "compare",
                    "--request",
                    str(request_path),
                    "--out",
                    str(result_path),
                ],
                cwd=highlander_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(compared.returncode, 0, compared.stderr)
            result = json.loads(result_path.read_bytes())
            candidate = next(
                item
                for item in result["candidates"]
                if item["candidateId"] == "H-b1"
            )
            observations = {
                item["objectiveId"]: item for item in candidate["observations"]
            }
            self.assertIn("roi", observations)
            self.assertEqual(
                observations["roi"]["sourceSchemaId"],
                "urn:reagent-labrador:rnpv_roi_calculator:output:1.0.0",
            )
            self.assertIn("tractability_posture", observations)
            self.assertEqual(
                observations["tractability_posture"]["rawValue"],
                "small_molecule_tractable",
            )
            self.assertFalse(
                any(
                    qualifier.startswith(
                        "MISSING_MODULE_RESULT:therapeutic-program-economics:"
                    )
                    or qualifier.startswith(
                        "MISSING_MODULE_RESULT:small-molecule-tractability-review:"
                    )
                    for qualifier in candidate["qualifiers"]
                ),
                candidate["qualifiers"],
            )

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
