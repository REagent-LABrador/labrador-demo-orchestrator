"""Browser-safe projections for the explicit scientific branch runner."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .contracts import load_json
from .registry import ModuleRegistry

FRONTEND_STATUS = {
    "SKIPPED": "COMPLETE_WITH_WARNINGS",
    "QUEUED": "QUEUED",
    "RUNNING": "RUNNING",
    "COMPLETE": "COMPLETE",
    "COMPLETE_WITH_WARNINGS": "COMPLETE_WITH_WARNINGS",
    "FAILED": "FAILED",
}


def _highlander_result(root: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    result_ref = manifest["highlander"].get("result_ref")
    if not isinstance(result_ref, str):
        return None
    path = root / "runs" / manifest["run_id"] / result_ref
    if not path.is_file():
        return None
    value = load_json(path)
    return value if isinstance(value, dict) else None


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_id": node.get("module_id"),
        "status": node.get("status"),
        "reason_code": node.get("reason_code"),
        "message": node.get("message"),
        "output_origin": node.get("output_origin"),
        "input_ref": node.get("input_ref"),
        "input_hash": node.get("input_hash"),
        "output_ref": node.get("output_ref"),
        "output_hash": node.get("output_hash"),
        "duration_ms": node.get("duration_ms"),
        "exit_code": node.get("exit_code"),
        "producer": copy.deepcopy(node.get("producer")),
        "artifact": copy.deepcopy(node.get("output")),
    }

def _branches(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for branch in manifest.get("scientific", {}).get("branches", []):
        if not isinstance(branch, dict):
            continue
        nodes = branch.get("nodes") if isinstance(branch.get("nodes"), dict) else {}
        result.append(
            {
                "branch_id": branch.get("branch_id"),
                "status": branch.get("status"),
                "focus": copy.deepcopy(branch.get("focus")),
                "nodes": {
                    module_id: _public_node(node)
                    for module_id, node in nodes.items()
                    if isinstance(module_id, str) and isinstance(node, dict)
                },
            }
        )
    return result


def _stages(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "stage_id": stage["id"],
            "result_status": FRONTEND_STATUS[stage["status"]],
            "execution_status": FRONTEND_STATUS[stage["status"]],
            "module_execution_status": stage["execution_status"],
            "output_origin": stage["output_origin"],
            "result_basis": list(stage["result_basis"]),
            "runtime_maturity": stage["runtime_maturity"],
            "reason_code": stage["reason_code"],
            "qualifiers": list(stage["qualifiers"]),
            "warnings": list(stage["warnings"]),
            "note": stage["note"],
        }
        for stage in manifest["stages"]
    ]


def project_scientific_snapshot(
    root: Path, registry: ModuleRegistry, manifest: dict[str, Any]
) -> dict[str, Any]:
    branches = _branches(manifest)
    setup = manifest["setup"]
    result = _highlander_result(root, manifest)
    representative = setup.get("presentationMode") == "REPRESENTATIVE_DEMO"
    return {
        "schema_version": "labrador.scientific-snapshot.v1",
        "run_id": manifest["run_id"],
        "run_status": manifest["run_status"],
        "updated_at": manifest["updated_at"],
        "last_event_id": manifest["revision"],
        "execution_mode": setup.get("executionMode"),
        "presentation_mode": setup.get("presentationMode"),
        "representative_demo": representative,
        "watermark": "REPRESENTATIVE DEMO VALUES" if representative else None,
        "scientific_packet_excludes_representative_values": True,
        "stages": _stages(manifest),
        "branches": branches,
        "highlander_ready": bool(manifest["highlander"]["ready"]),
        "highlander": {
            "launched": bool(manifest["highlander"]["launched"]),
            "job_id": manifest["highlander"]["job_id"],
            "packet_snapshot": copy.deepcopy(manifest["highlander"]["packet_snapshot"]),
            "result_hash": manifest["highlander"].get("result_hash"),
            "result": result,
        },
        "modules": [
            {
                "module_id": module.module_id,
                "repository": module.repository,
                "git_sha": module.commit,
                "timeout_seconds": module.timeout_seconds,
            }
            for module in registry.modules
        ],
        "warnings": copy.deepcopy(manifest["warnings"]),
        "errors": copy.deepcopy(manifest["errors"]),
    }


def project_scientific_ui_state(
    root: Path, registry: ModuleRegistry, manifest: dict[str, Any]
) -> dict[str, Any]:
    snapshot = project_scientific_snapshot(root, registry, manifest)
    setup = manifest["setup"]
    identity = setup["programFrame"]["identity"]
    programs = [
        {
            "id": branch["branch_id"],
            "label": branch["focus"].get("display_label"),
            "status": branch["status"],
            "hash": (
                branch["nodes"].get("hypothesis_generator", {}).get("output_hash")
            ),
        }
        for branch in snapshot["branches"]
    ]
    return {
        "schemaVersion": "labrador.ui-run-state.v1",
        "revision": manifest["revision"],
        "runId": manifest["run_id"],
        "runStatus": manifest["run_status"],
        "updatedAt": manifest["updated_at"],
        "setupSnapshot": {
            "indication": setup["validatedIndication"],
            "submittedIndication": setup["clinicalIndication"],
            "biomarkers": setup["maxFocusBranches"],
            "papers": setup["maxLiteraturePapers"],
            "hypotheses": 1,
            "biomarkerRange": list(setup["biomarkerRange"]),
            "hypothesisRange": list(setup["hypothesisRange"]),
            "profileRef": None,
            "programIdentity": copy.deepcopy(identity),
            "executionMode": setup["executionMode"],
            "presentationMode": setup["presentationMode"],
        },
        "truth": {
            "executionMode": setup["executionMode"],
            "presentationMode": setup["presentationMode"],
            "representativeValuesExcludedFromScientificPackets": True,
        },
        "stages": snapshot["stages"],
        "uiProjection": {
            "runData": {
                "biomarkers": [branch["focus"] for branch in snapshot["branches"]],
                "programs": programs,
                "requestedLanes": setup["maxFocusBranches"],
                "biomarkerShortfall": max(
                    0, setup["maxFocusBranches"] - len(snapshot["branches"])
                ),
                "hypothesisShortfall": max(
                    0, setup["maxFocusBranches"] - len(programs)
                ),
            },
            "nodes": snapshot["branches"],
        },
        "highlander": {
            "ready": snapshot["highlander_ready"],
            "launched": snapshot["highlander"]["launched"],
            "jobId": snapshot["highlander"]["job_id"],
            "packetSnapshot": snapshot["highlander"]["packet_snapshot"],
            "result": snapshot["highlander"]["result"],
            "requiresGapAcknowledgement": bool(
                manifest["highlander"]["requires_gap_acknowledgement"]
            ),
            "counts": {
                "complete": sum(branch["status"] == "COMPLETE" for branch in programs),
                "partial": len(programs),
                "blocked": sum(branch["status"] != "COMPLETE" for branch in programs),
                "nonterminal": sum(
                    stage["status"] in {"QUEUED", "RUNNING"}
                    for stage in manifest["stages"]
                ),
            },
            "programs": programs,
            "comparisonBasis": "server-native producer packets only",
        },
        "scientific": snapshot,
        "modules": snapshot["modules"],
        "warnings": snapshot["warnings"],
        "errors": snapshot["errors"],
    }
