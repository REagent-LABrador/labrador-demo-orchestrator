"""Projection for the functional frontend's snake-case HTTP contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ContractError
from .projection import VISUAL_STAGE_ORDER, load_validated_outputs, project_ui_state
from .registry import ModuleRegistry

FRONTEND_STAGE_STATUSES = {
    "QUEUED",
    "RUNNING",
    "COMPLETE",
    "COMPLETE_WITH_WARNINGS",
    "FAILED",
}


def _wire_status(stage: dict[str, Any]) -> str:
    status = stage.get("status")
    if status == "SKIPPED":
        return "COMPLETE_WITH_WARNINGS"
    if status not in FRONTEND_STAGE_STATUSES:
        raise ContractError(f"stage {stage.get('id')} has unsupported frontend status {status!r}")
    return str(status)


def _stage_by_ui(manifest: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage in manifest["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise ContractError(f"manifest has no UI stage {stage_id}")


def project_frontend_snapshot(
    root: Path,
    registry: ModuleRegistry,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Return the functional frontend v0 snapshot without altering native outputs."""

    state = project_ui_state(root, registry, manifest)
    outputs = load_validated_outputs(registry.root, manifest)
    run_data = state["uiProjection"]["runData"]
    setup = manifest["setup"]
    lane_ceiling = setup["maxBiomarkers"] * setup["maxHypothesesPerBiomarker"]
    programs = run_data["programs"]
    biomarkers = run_data["biomarkers"]
    module_by_stage = {module.ui_stage: module.module_id for module in registry.modules}

    stage_rows: list[dict[str, Any]] = []
    for stage_id in VISUAL_STAGE_ORDER:
        stage = _stage_by_ui(manifest, stage_id)
        if stage_id == "biomarker":
            requested = setup["maxBiomarkers"]
            returned = len(biomarkers) if stage["status"] != "RUNNING" else 0
        elif stage_id == "hypothesis":
            requested = lane_ceiling
            returned = len(programs) if stage["status"] != "RUNNING" else 0
        else:
            requested = lane_ceiling
            returned = len(programs) if module_by_stage[stage_id] in outputs else 0
        stage_rows.append(
            {
                "stage_id": stage_id,
                "execution_status": _wire_status(stage),
                "note": stage.get("note") or _wire_status(stage).lower(),
                "requested": requested,
                "returned": returned,
            }
        )

    station_payloads = {
        module.ui_stage: outputs[module.module_id]
        for module in registry.modules
        if module.module_id in outputs
    }
    wire_programs = [
        {
            "id": program["id"],
            "lane": program["lane"],
            "biomarker_slot": program["biomarkerSlot"],
            "hypothesis_slot": program["hypothesisSlot"],
            "label": program["label"],
            "short_label": program["short"],
            "metrics": dict(program["metrics"]),
            "uncertainty": program["uncertainty"],
            "public_why": program["publicWhy"],
            "roi_failed": bool(program["roiFailed"]),
            "recruit_failed": bool(program["recruitFailed"]),
            "overflow_rnpv": bool(program["overflowRnpv"]),
            "not_amenable": bool(program["notAmenable"]),
            "revision": f"packet-r{manifest['revision']}",
            "hash": program.get("hash") or "unhashed",
            # Native results remain byte-for-byte semantically unchanged. A module's
            # optional top-level interpretability object therefore rides along here.
            "station_payloads": station_payloads,
        }
        for program in programs
    ]
    wire_biomarkers = [
        {
            "slot": item["slot"],
            "label": item["label"],
            "summary": item.get("summary", ""),
            "metrics": dict(item.get("metrics") or {}),
            "uncertainty": item.get("uncertainty") or "not supplied",
        }
        for item in biomarkers
    ]
    nonterminal = sum(
        row["execution_status"] in {"QUEUED", "RUNNING"} for row in stage_rows
    )
    ready = bool(manifest["highlander"]["ready"] and wire_programs and nonterminal == 0)
    if ready:
        blocked_reason = None
    elif nonterminal:
        blocked_reason = f"{nonterminal} stages nonterminal"
    elif not wire_programs:
        blocked_reason = "no candidate programs returned"
    else:
        blocked_reason = "candidate packet is not ready"
    return {
        "run_id": manifest["run_id"],
        "updated_at": manifest["updated_at"],
        "last_event_id": manifest["revision"],
        "stages": stage_rows,
        "biomarkers": wire_biomarkers,
        "programs": wire_programs,
        "highlander_ready": ready,
        "highlander_blocked_reason": blocked_reason,
    }


def project_frontend_meta(registry: ModuleRegistry) -> dict[str, Any]:
    """Describe configured capabilities without claiming they executed live."""

    return {
        "backend": "labrador-orchestrator",
        "truth_labels": [
            "PROPOSED TARGET",
            "LOCAL ORCHESTRATOR",
            "MIXED LIVE / CACHED / FALLBACK EXECUTION",
        ],
        "modules": [
            {
                "name": module.module_id,
                "runtime_maturity": module.runtime_maturity,
                "configured_mode": module.mode.upper(),
                "git_sha": module.commit,
            }
            for module in registry.modules
        ],
    }
