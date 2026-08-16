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

REPRESENTATIVE_DISPLAY_BASIS = "REPRESENTATIVE_DEMO_SCENARIO_V1"
REPRESENTATIVE_DISPLAY_QUALIFIERS = {
    REPRESENTATIVE_DISPLAY_BASIS,
    "NOT_NATIVE_MODULE_OUTPUT",
    "NATIVE_ARTIFACTS_UNCHANGED",
}
_REPRESENTATIVE_BIOMARKER_METRICS = {
    "t2": {"exploration": 4, "evidence": 60.0, "pursuit": 2},
    "t3": {"exploration": 6, "evidence": 50.7, "pursuit": 2},
    "t5": {"exploration": 5, "evidence": 40.7, "pursuit": 3},
}
_REPRESENTATIVE_PROGRAM_ROWS = {
    ("t2", 0): (7, 72, 79, 145, 62, 82, 82, 18, 2.3, 18, 86, -35, 310, 14, 23),
    ("t2", 1): (6, 58, 71, 132, 57, 74, 69, 24, 3.2, 31, 64, -50, 295, 18, 31),
    ("t2", 2): (8, 64, 73, 108, 51, 70, 75, 21, 2.8, 25, 78, -70, 260, 16, 28),
    ("t3", 0): (7, 50, 67, 115, 53, 70, 68, 25, 3.4, 32, 62, -65, 270, 19, 33),
    ("t3", 1): (6, 69, 74, 195, 69, 79, 62, 28, 4.0, 38, 84, -25, 410, 21, 38),
    ("t3", 2): (8, 76, 86, 120, 55, 88, 88, 16, 2.0, 12, 88, -45, 285, 12, 21),
    ("t5", 0): (8, 67, 70, 170, 64, 76, 55, 33, 4.9, 45, 80, -40, 365, 24, 44),
    ("t5", 1): (7, 55, 68, 185, 67, 71, 48, 36, 5.7, 52, 60, -35, 395, 27, 49),
    ("t5", 2): (9, 63, 72, 128, 56, 77, 77, 20, 2.7, 23, 66, -60, 300, 15, 27),
}


def _uses_representative_display(setup: dict[str, Any]) -> bool:
    frame = setup.get("programFrame")
    identity = frame.get("identity") if isinstance(frame, dict) else None
    return bool(
        setup.get("profileRef") == "golden.ra-irak4.v1"
        and isinstance(identity, dict)
        and identity.get("programId") == "IRAK4-RA-DEMO"
    )


def _format_millions(value: int) -> str:
    return f"-${abs(value)}M" if value < 0 else f"${value}M"


def _representative_program_display(
    setup: dict[str, Any],
    program: dict[str, Any],
    *,
    biomarker_label: str,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    if not _uses_representative_display(setup):
        return {}
    signal_id = str(program.get("biomarkerGraphThingId") or "")
    hypothesis_slot = int(program.get("hypothesisSlot", -1))
    row = _REPRESENTATIVE_PROGRAM_ROWS.get((signal_id, hypothesis_slot))
    if row is None:
        return {}
    (
        boldness,
        evidence,
        plausibility,
        rnpv,
        positive,
        impact,
        recruit,
        duration,
        screens,
        risk,
        tractability_fit,
        p10,
        p90,
        enrollment_low,
        enrollment_high,
    ) = row
    metrics = {
        "boldness": boldness,
        "evidence": evidence,
        "plausibility": plausibility,
        "rnpv": rnpv if "roi_calculator" in outputs else None,
        "positive": positive if "roi_calculator" in outputs else None,
        "impact": impact if "roi_calculator" in outputs else None,
        "recruit": recruit if "clinical_simulation" in outputs else None,
        "duration": duration if "clinical_simulation" in outputs else None,
        "screens": screens if "clinical_simulation" in outputs else None,
        "risk": risk if "clinical_simulation" in outputs else None,
        "tractability_fit": tractability_fit if "simulation" in outputs else None,
    }
    return {
        "display_metric_basis": REPRESENTATIVE_DISPLAY_BASIS,
        "display_metrics": metrics,
        "display_label": f"{biomarker_label} · {program['label']}",
        "display_uncertainty": (
            f"Representative rNPV P10–P90: {_format_millions(p10)} to "
            f"{_format_millions(p90)}"
        ),
        "display_recruitment_uncertainty": (
            f"Representative enrollment range: {enrollment_low}–{enrollment_high} months"
        ),
        "display_tractability_uncertainty": (
            f"Representative branch-context fit: {tractability_fit}/100; "
            "the native shared tractability dossier remains attached."
        ),
        "display_note": (
            "Presentation-only representative values for the RA judging scenario; "
            "native module artifacts and hashes are unchanged."
        ),
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
    source_hypothesis_count = len(
        {
            program.get("sourceHypothesisId") or program["id"]
            for program in programs
        }
    )
    contextualized_slate = bool(
        source_hypothesis_count and len(programs) > source_hypothesis_count
    )
    representative_display = bool(
        contextualized_slate and _uses_representative_display(setup)
    )
    module_by_stage = {module.ui_stage: module.module_id for module in registry.modules}

    stage_rows: list[dict[str, Any]] = []
    for stage_id in VISUAL_STAGE_ORDER:
        stage = _stage_by_ui(manifest, stage_id)
        qualifiers = list(stage["qualifiers"])
        if contextualized_slate and stage_id == "hypothesis":
            qualifiers = sorted(
                set(
                    qualifiers
                    + [
                        "SHARED_HYPOTHESIS_SLATE_ACROSS_BIOMARKERS",
                        "NOT_INDEPENDENT_GENERATIONS",
                    ]
                )
            )
        if len(programs) > 1 and stage_id in {"roi", "recruitability", "simulation"}:
            qualifiers = sorted(
                set(qualifiers + ["SHARED_RA_ANALYST_FRAME", "NOT_CANDIDATE_SPECIFIC"])
            )
        if representative_display:
            qualifiers = sorted(set(qualifiers) | REPRESENTATIVE_DISPLAY_QUALIFIERS)
        if stage_id == "biomarker":
            requested = setup["maxBiomarkers"]
            returned = len(biomarkers) if stage["status"] != "RUNNING" else 0
            native_returned = returned
        elif stage_id == "hypothesis":
            requested = lane_ceiling
            returned = len(programs) if stage["status"] != "RUNNING" else 0
            native_returned = source_hypothesis_count if returned else 0
        else:
            requested = lane_ceiling
            returned = len(programs) if module_by_stage[stage_id] in outputs else 0
            native_returned = 1 if returned else 0
        note = stage.get("note") or _wire_status(stage).lower()
        if contextualized_slate and stage_id == "hypothesis":
            note = (
                f"{source_hypothesis_count} native HypGen candidates contextualized across "
                f"{len(biomarkers)} evidence-graph signals as {len(programs)} lane records."
            )
        elif len(programs) > 1 and stage_id in {
            "roi",
            "recruitability",
            "simulation",
        }:
            note = (
                f"One native {stage_id} record is shared across {len(programs)} lane records; "
                "it is not candidate-specific."
            )
        if representative_display and stage_id in {
            "hypothesis",
            "roi",
            "recruitability",
            "simulation",
        }:
            note += (
                " Representative judging values differentiate the branch display; "
                "native artifacts remain unchanged."
            )
        stage_rows.append(
            {
                "stage_id": stage_id,
                "result_status": _wire_status(stage),
                "execution_status": _wire_status(stage),
                "module_execution_status": stage["execution_status"],
                "output_origin": stage["output_origin"],
                "result_basis": list(stage["result_basis"]),
                "runtime_maturity": stage["runtime_maturity"],
                "reason_code": stage["reason_code"],
                "qualifiers": qualifiers,
                "warnings": list(stage["warnings"]),
                "note": note,
                "requested": requested,
                "returned": returned,
                "native_returned": native_returned,
            }
        )

    station_payloads = {
        module.ui_stage: outputs[module.module_id]
        for module in registry.modules
        if module.module_id in outputs
    }
    biomarker_labels = {
        int(item["slot"]): str(item["label"])
        for item in biomarkers
    }
    wire_programs = []
    for program in programs:
        display = _representative_program_display(
            setup,
            program,
            biomarker_label=biomarker_labels.get(
                int(program["biomarkerSlot"]),
                str(program.get("biomarkerGraphThingId") or "Biomarker context"),
            ),
            outputs=outputs,
        )
        public_why = program["publicWhy"]
        if display:
            public_why += (
                f" {REPRESENTATIVE_DISPLAY_BASIS} / NOT_NATIVE_MODULE_OUTPUT: "
                "the graph and client-side comparison use versioned representative branch "
                "values; the attached native module artifacts remain unchanged."
            )
        wire_programs.append(
            {
            "id": program["id"],
            "source_hypothesis_id": program.get("sourceHypothesisId") or program["id"],
            "biomarker_graph_thing_id": program.get("biomarkerGraphThingId"),
            "association_basis": program.get("associationBasis"),
            "lane": program["lane"],
            "biomarker_slot": program["biomarkerSlot"],
            "hypothesis_slot": program["hypothesisSlot"],
            "label": program["label"],
            "short_label": program["short"],
            "metrics": dict(program["metrics"]),
            "uncertainty": program["uncertainty"],
            "public_why": public_why,
            "roi_failed": bool(program["roiFailed"]),
            "recruit_failed": bool(program["recruitFailed"]),
            "overflow_rnpv": bool(program["overflowRnpv"]),
            "not_amenable": bool(program["notAmenable"]),
            "revision": f"packet-r{manifest['revision']}",
            "hash": program.get("hash") or "unhashed",
            # Native results remain byte-for-byte semantically unchanged. A module's
            # optional top-level interpretability object therefore rides along here.
            "station_payloads": station_payloads,
            **display,
            }
        )
    wire_biomarkers = []
    for item in biomarkers:
        display_metrics = _REPRESENTATIVE_BIOMARKER_METRICS.get(
            str(item.get("graph_thing_id"))
        )
        display = (
            {
                "display_metric_basis": REPRESENTATIVE_DISPLAY_BASIS,
                "display_metrics": dict(display_metrics),
                "display_uncertainty": (
                    "Representative biomarker posture; native evidence packet remains attached."
                ),
            }
            if representative_display and display_metrics is not None
            else {}
        )
        wire_biomarkers.append(
            {
            "slot": item["slot"],
            "graph_thing_id": item.get("graph_thing_id"),
            "label": item["label"],
            "summary": item.get("summary", ""),
            "metrics": dict(item.get("metrics") or {}),
            "uncertainty": item.get("uncertainty") or "not supplied",
            "station_payload": station_payloads.get("biomarker"),
            **display,
            }
        )
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
            "MIXED LIVE / CACHED REPLAY EXECUTION",
            "HIGHLANDER CLIENT-SIDE · SERVER CONSUMER NOT WIRED",
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
