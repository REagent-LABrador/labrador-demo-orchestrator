"""Project five heterogeneous module envelopes into the stable browser contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ContractError, load_json, resolve_within, sha256_file
from .registry import ModuleRegistry
from .runner import TERMINAL_STAGE_STATUSES

VISUAL_STAGE_ORDER = ["biomarker", "hypothesis", "roi", "recruitability", "simulation"]


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _percent(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    numeric = float(number)
    if not 0 <= numeric <= 100:
        return None
    return round(numeric * 100 if numeric <= 1 else numeric, 1)


def _stage_by_ui(manifest: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage in manifest["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise ContractError(f"manifest has no UI stage {stage_id}")


def load_validated_outputs(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Load only manifest-bound outputs whose exact artifact hashes still match."""

    run_dir = root / "runs" / manifest["run_id"]
    result: dict[str, Any] = {}
    for stage in manifest["stages"]:
        reference = stage.get("output_ref")
        if not reference:
            continue
        path = resolve_within(run_dir, run_dir / reference, label="run output")
        if not path.is_file():
            raise ContractError(
                f"output integrity mismatch: {stage['module_id']} artifact is missing"
            )
        expected_hash = stage.get("output_hash")
        if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            raise ContractError(f"output integrity mismatch: {stage['module_id']} hash changed")
        result[stage["module_id"]] = load_json(path)
    return result


def _stage_projection(stage: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stage["id"],
        "status": stage["status"],
        "executionStatus": stage["execution_status"],
        "outputOrigin": stage["output_origin"],
        "note": stage["note"],
        "reasonCode": stage["reason_code"],
        "resultBasis": list(stage["result_basis"]),
        "runtimeMaturity": stage["runtime_maturity"],
        "qualifiers": list(stage["qualifiers"]),
        "warnings": list(stage["warnings"]),
        "outputHash": stage["output_hash"],
    }


def _execution(stage: dict[str, Any]) -> str:
    if stage["status"] == "COMPLETE_WITH_WARNINGS":
        return "COMPLETE_WITH_WARNINGS"
    return stage["status"]


def _basis(stage: dict[str, Any]) -> str:
    return " + ".join(stage["result_basis"])


def _runtime(stage: dict[str, Any]) -> str:
    return f"{stage['runtime_maturity']} · {stage['output_origin'].replace('_', ' ')}"


def _evidence_metric(evidence: dict[str, Any] | None) -> float | None:
    if not evidence:
        return None
    values = [
        _number(link.get("confidence"))
        for link in evidence.get("links", [])
        if isinstance(link, dict)
    ]
    finite = [float(value) for value in values if value is not None]
    if not finite:
        return None
    return round(sum(finite) / len(finite) * 100, 1)


def _paper_citations(evidence: dict[str, Any] | None) -> list[str]:
    if not evidence:
        return []
    citations: list[str] = []
    for paper in evidence.get("papers", []):
        if not isinstance(paper, dict):
            continue
        identifier = paper.get("doi") or paper.get("pmid") or paper.get("url") or paper.get("id")
        if identifier:
            citations.append(str(identifier))
    return citations[:6]


def _actual_program(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
) -> dict[str, Any] | None:
    hypothesis = outputs.get("hypothesis_generator")
    if not isinstance(hypothesis, dict) or not isinstance(hypothesis.get("hypothesis"), dict):
        return None
    record = hypothesis["hypothesis"]
    scores = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    roi = outputs.get("roi_calculator") if isinstance(outputs.get("roi_calculator"), dict) else {}
    roi_payload = roi.get("payload") if isinstance(roi.get("payload"), dict) else {}
    summary = roi_payload.get("summary") if isinstance(roi_payload.get("summary"), dict) else {}
    clinical = (
        outputs.get("clinical_simulation")
        if isinstance(outputs.get("clinical_simulation"), dict)
        else {}
    )
    simulation = outputs.get("simulation") if isinstance(outputs.get("simulation"), dict) else {}
    low, high = setup["hypothesisRange"]
    frame = setup.get("programFrame") if isinstance(setup.get("programFrame"), dict) else {}
    identity = frame.get("identity") if isinstance(frame.get("identity"), dict) else {}
    target = str(identity.get("targetSymbol") or "Target not reported")
    indication = str(identity.get("indication") or setup.get("validatedIndication") or "Indication")
    display_name = str(identity.get("displayName") or f"{target} in {indication}")
    hypothesis_label = (
        f"{record.get('subject_name', target)} → "
        f"{record.get('object_name', indication + ' mechanism')}"
    )
    p10 = _number(summary.get("p10_rnpv"))
    p90 = _number(summary.get("p90_rnpv"))
    decision_grade = roi_payload.get("decision_grade") or summary.get("recommendation")
    qualifier = str(decision_grade or "NOT_DECISION_GRADE")
    simulation_stage = stages["simulation"]
    citations = _paper_citations(outputs.get("evidence_mapper"))
    metrics = {
        "boldness": round((low + high) / 2, 1),
        "evidence": _percent(scores.get("support")),
        "plausibility": _percent(record.get("rank_score")),
        "rnpv": (
            round(float(summary["p50_rnpv"]) / 1_000_000, 1)
            if _number(summary.get("p50_rnpv")) is not None
            else None
        ),
        "positive": _percent(summary.get("probability_positive_rnpv")),
        "impact": None,
        "recruit": _percent(clinical.get("score")),
        "duration": _number(clinical.get("simulated_months_to_enroll")),
        "screens": _number(clinical.get("screens_per_enrollee")),
        "risk": (
            round(100 - float(clinical["score"]) * 100, 1)
            if _number(clinical.get("score")) is not None
            else None
        ),
        # Small-molecule tractability is a categorical verdict over two evidence axes
        # reported separately; no scalar tractability score is imputed for these slots.
        "verdict": None,
        "precedent": None,
        "computed": None,
    }
    return {
        "id": str(identity.get("programId") or "program-1"),
        "lane": 0,
        "biomarkerSlot": 0,
        "hypothesisSlot": 0,
        "hypothesisNodeId": "hyp-slot-0",
        "roiNodeId": "roi-slot-0",
        "recruitNodeId": "recruitability-slot-0",
        "simulationNodeId": "simulation-slot-0",
        "label": hypothesis_label,
        "short": f"{target} · {indication}",
        "metrics": metrics,
        "uncertainty": (
            f"rNPV P10–P90: ${float(p10) / 1_000_000:.1f}M to "
            f"${float(p90) / 1_000_000:.1f}M · {qualifier}"
            if p10 is not None and p90 is not None
            else f"Economics unavailable · {qualifier}"
        ),
        "publicWhy": (
            f"Integrated candidate for {display_name}. Each stage retains its own execution, "
            "origin, evidence basis, and decision-grade qualifiers."
        ),
        "roiFailed": "roi_calculator" not in outputs,
        "recruitFailed": "clinical_simulation" not in outputs,
        "overflowRnpv": False,
        "notAmenable": simulation.get("verdict") == "not_tractable",
        "revision": "packet-r1",
        "hash": stages["roi"].get("output_hash") or stages["hypothesis"].get("output_hash"),
        "paretoStatus": "non-dominated" if metrics["rnpv"] is not None else "incomparable",
        "sourceType": "INTEGRATED_RUN",
        "qualifiers": sorted(
            set(
                stages["biomarker"]["qualifiers"]
                + stages["hypothesis"]["qualifiers"]
                + stages["roi"]["qualifiers"]
                + stages["recruitability"]["qualifiers"]
                + stages["simulation"]["qualifiers"]
            )
        ),
        "citations": citations,
        "economicsBasis": qualifier,
        "simulationBasis": (
            f"{simulation_stage['output_origin'].replace('_', ' ')} SMALL-MOLECULE "
            f"TRACTABILITY · verdict {simulation.get('verdict') or 'insufficient_evidence'} "
            f"({simulation.get('verdict_basis') or 'basis not reported'}) · "
            "two axes reported separately, never combined"
        ),
        "gaps": [
            (
                "Recruitability uses an explicit analyst clinical thesis, "
                "not inferred hypothesis prose."
            ),
            f"ROI basis: {qualifier}.",
            (
                "Tractability is a categorical two-axis verdict (retrieved precedent, "
                "computed tractability); the axes are reported separately, never "
                "combined into a scalar and never a molecular-dynamics score."
            ),
        ],
        "failureHistory": [
            {
                "stage": item["id"],
                "executionStatus": item["execution_status"],
                "reasonCode": item["reason_code"],
            }
            for item in stages.values()
            if item["execution_status"] in {"FAILED", "TIMED_OUT"}
        ],
    }


def _proxy_programs() -> list[dict[str, Any]]:
    """Two explicit comparators retained for a flashy comparison without fake module claims."""

    return [
        {
            "id": "proxy-jak1",
            "label": "JAK1 inhibition comparator",
            "short": "JAK1 · illustrative proxy",
            "sourceType": "ILLUSTRATIVE_PROXY",
            "paretoStatus": "incomparable",
            "metrics": {
                "boldness": 3,
                "evidence": None,
                "plausibility": None,
                "rnpv": None,
                "positive": None,
                "impact": None,
                "recruit": None,
                "duration": None,
                "screens": None,
                "risk": None,
                "verdict": None,
                "precedent": None,
                "computed": None,
            },
            "publicWhy": "Comparison shell only; no module packet was produced for this program.",
            "uncertainty": "All objectives missing",
            "qualifiers": ["ILLUSTRATIVE_PROXY", "NO_MODULE_PACKET"],
            "citations": [],
            "gaps": ["No integrated module packet."],
            "economicsBasis": "MISSING",
            "simulationBasis": "MISSING",
        },
        {
            "id": "proxy-tnf",
            "label": "TNF inhibition comparator",
            "short": "TNF · illustrative proxy",
            "sourceType": "ILLUSTRATIVE_PROXY",
            "paretoStatus": "incomparable",
            "metrics": {
                "boldness": 1,
                "evidence": None,
                "plausibility": None,
                "rnpv": None,
                "positive": None,
                "impact": None,
                "recruit": None,
                "duration": None,
                "screens": None,
                "risk": None,
                "verdict": None,
                "precedent": None,
                "computed": None,
            },
            "publicWhy": "Comparison shell only; no module packet was produced for this program.",
            "uncertainty": "All objectives missing",
            "qualifiers": ["ILLUSTRATIVE_PROXY", "NO_MODULE_PACKET"],
            "citations": [],
            "gaps": ["No integrated module packet."],
            "economicsBasis": "MISSING",
            "simulationBasis": "MISSING",
        },
    ]


def _nodes(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
    program: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if program is None:
        return []
    evidence_value = outputs.get("evidence_mapper")
    evidence = evidence_value if isinstance(evidence_value, dict) else {}
    hypothesis_value = outputs.get("hypothesis_generator")
    hypothesis = hypothesis_value if isinstance(hypothesis_value, dict) else {}
    hypothesis_record = (
        hypothesis.get("hypothesis") if isinstance(hypothesis.get("hypothesis"), dict) else {}
    )
    roi = outputs.get("roi_calculator") if isinstance(outputs.get("roi_calculator"), dict) else {}
    roi_payload = roi.get("payload") if isinstance(roi.get("payload"), dict) else {}
    clinical_value = outputs.get("clinical_simulation")
    clinical = clinical_value if isinstance(clinical_value, dict) else {}
    simulation = outputs.get("simulation") if isinstance(outputs.get("simulation"), dict) else {}
    frame = setup.get("programFrame") if isinstance(setup.get("programFrame"), dict) else {}
    identity = frame.get("identity") if isinstance(frame.get("identity"), dict) else {}
    target = str(identity.get("targetSymbol") or "Target not reported")
    indication = str(identity.get("indication") or setup.get("validatedIndication") or "Indication")
    display_name = str(identity.get("displayName") or f"{target} in {indication}")
    low, high = setup["biomarkerRange"]
    common = {
        "lane": 0,
        "kind": "real",
    }

    def node(
        stage_id: str,
        node_id: str,
        parent_id: str,
        label: str,
        metrics: dict[str, Any],
        uncertainty: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        stage = stages[stage_id]
        return {
            **common,
            "id": node_id,
            "stage": stage_id,
            "parentId": parent_id,
            "label": label,
            "execution": _execution(stage),
            "resultBasis": _basis(stage),
            "runtime": _runtime(stage),
            "metrics": metrics,
            "uncertainty": uncertainty,
            "reason": stage.get("note"),
            "metadata": {
                **metadata,
                "moduleName": stage["module"]["name"],
                "repository": stage["module"]["repository"],
                "gitSha": stage["module"]["git_sha"],
                "attempts": len(stage["attempts"]),
                "outputHash": stage["output_hash"],
                "outputOrigin": stage["output_origin"],
                "qualifiers": stage["qualifiers"],
                "warnings": stage["warnings"],
            },
        }

    summary = roi_payload.get("summary") if isinstance(roi_payload.get("summary"), dict) else {}
    coverage = evidence.get("coverage") if isinstance(evidence.get("coverage"), dict) else {}
    read_count = coverage.get("read")
    found_count = coverage.get("found")
    evidence_uncertainty = (
        f"Evidence coverage read {read_count} of {found_count} search results."
        if isinstance(read_count, int) and isinstance(found_count, int)
        else "Evidence coverage counts were not reported by this module output."
    )
    evidence_limitations = list(stages["biomarker"].get("warnings", []))
    if "TRUNCATED_SEARCH" in stages["biomarker"].get("qualifiers", []):
        evidence_limitations.append("Search coverage was reported as truncated.")
    hypothesis_uncertainty = (
        "Deterministic dry-run hypothesis; inspect its recorded caveats and provenance."
        if "DETERMINISTIC_DRY_RUN" in stages["hypothesis"].get("qualifiers", [])
        else "Hypothesis uncertainty is carried in the module caveats and provenance."
    )
    roi_qualifiers = set(stages["roi"].get("qualifiers", []))
    if "SYNTHETIC" in roi_qualifiers:
        roi_counterevidence = "Economics inputs are explicitly synthetic or unsupported."
    elif "NOT_DECISION_GRADE" in roi_qualifiers:
        roi_counterevidence = "Decision-grade evidence gaps remain; inspect module warnings."
    else:
        roi_counterevidence = None
    simulation_origin = stages["simulation"].get("output_origin", "NOT_RUN").replace("_", " ")
    simulation_target_precedent = (
        simulation.get("target_precedent")
        if isinstance(simulation.get("target_precedent"), dict)
        else {}
    )
    simulation_tractability = (
        simulation.get("tractability")
        if isinstance(simulation.get("tractability"), dict)
        else {}
    )
    simulation_verdict = simulation.get("verdict") or "insufficient_evidence"
    simulation_verdict_basis = simulation.get("verdict_basis")
    simulation_axis_conflict = simulation.get("axis_conflict")

    precedent_bits: list[str] = []
    best_potency_nm = _number(simulation_target_precedent.get("best_potency_nm"))
    if best_potency_nm is not None:
        precedent_bits.append(f"best measured potency {best_potency_nm} nM")
    approved_count = _number(simulation_target_precedent.get("approved_small_molecules_count"))
    if approved_count is not None:
        precedent_bits.append(f"{int(approved_count)} approved small molecules")
    clinical_molecules = simulation_target_precedent.get("clinical_stage_small_molecules")
    if isinstance(clinical_molecules, list):
        precedent_bits.append(f"{len(clinical_molecules)} clinical-stage small molecules")
    retrieved_precedent_axis = (
        "Retrieved precedent — " + "; ".join(precedent_bits)
        if precedent_bits
        else "Retrieved precedent — no measured small-molecule precedent reported"
    )

    computed_bits: list[str] = []
    site_pocket_rank = (
        simulation_tractability.get("site_pocket_rank")
        if isinstance(simulation_tractability.get("site_pocket_rank"), dict)
        else {}
    )
    fpocket_rank = _number(site_pocket_rank.get("fpocket"))
    n_pockets = _number(site_pocket_rank.get("n_pockets"))
    if fpocket_rank is not None and n_pockets is not None:
        computed_bits.append(
            f"within-structure druggability rank {int(fpocket_rank)} of {int(n_pockets)}"
        )
    cryptic_risk = simulation_tractability.get("cryptic_pocket_risk")
    if isinstance(cryptic_risk, str) and cryptic_risk:
        computed_bits.append(f"cryptic-pocket risk {cryptic_risk}")
    computed_tractability_axis = (
        "Computed tractability — " + "; ".join(computed_bits)
        if computed_bits
        else "Computed tractability — not reported for this run"
    )
    nodes = [
        node(
            "biomarker",
            "bio-slot-0",
            "indication-root",
            target,
            {
                "exploration": round((low + high) / 2, 1),
                "evidence": _evidence_metric(evidence),
                "pursuit": None,
            },
            evidence_uncertainty,
            {
                "slot": 0,
                "summary": f"Evidence request for {target} in {indication}",
                "evidenceSummary": f"{len(evidence.get('findings', []))} quoted findings",
                "counterevidenceSummary": (
                    "Condition-dependent or no-effect findings remain available for inspection."
                    if evidence.get("findings")
                    else None
                ),
                "limitations": evidence_limitations,
                "citations": _paper_citations(evidence),
            },
        ),
        node(
            "hypothesis",
            "hyp-slot-0",
            "bio-slot-0",
            program["label"],
            {
                "boldness": program["metrics"]["boldness"],
                "evidence": program["metrics"]["evidence"],
                "plausibility": program["metrics"]["plausibility"],
            },
            hypothesis_uncertainty,
            {
                "slot": 0,
                "biomarkerSlot": 0,
                "summary": hypothesis_record.get("provenance", "Hypothesis generated from graph"),
                "evidenceSummary": (
                    f"Support score {hypothesis_record.get('scores', {}).get('support')}"
                ),
                "counterevidenceSummary": "; ".join(hypothesis_record.get("caveats", [])[:2]),
                "limitations": hypothesis_record.get("caveats", []),
                "citations": program["citations"],
            },
        ),
        node(
            "roi",
            "roi-slot-0",
            "hyp-slot-0",
            f"rNPV · {display_name}",
            {
                "rnpv": program["metrics"]["rnpv"],
                "positive": program["metrics"]["positive"],
                "impact": None,
            },
            program["uncertainty"],
            {
                "slot": 0,
                "biomarkerSlot": 0,
                "summary": f"P50 rNPV from {roi_payload.get('simulations', 'unknown')} simulations",
                "evidenceSummary": str(roi_payload.get("decision_grade", "NOT_DECISION_GRADE")),
                "counterevidenceSummary": roi_counterevidence,
                "limitations": [warning.get("message") for warning in roi.get("warnings", [])[:5]],
                "p10Rnpv": _number(summary.get("p10_rnpv")),
                "p90Rnpv": _number(summary.get("p90_rnpv")),
                "currency": roi_payload.get("currency"),
                "decisionGrade": roi_payload.get("decision_grade"),
            },
        ),
        node(
            "recruitability",
            "recruitability-slot-0",
            "roi-slot-0",
            f"{indication} enrollment feasibility",
            {
                "recruit": program["metrics"]["recruit"],
                "duration": program["metrics"]["duration"],
                "screens": program["metrics"]["screens"],
                "risk": program["metrics"]["risk"],
            },
            f"Simulated month range: {clinical.get('simulated_months_range', 'missing')}",
            {
                "slot": 0,
                "biomarkerSlot": 0,
                "summary": clinical.get("why", "Recruitability output unavailable"),
                "evidenceSummary": (
                    f"{clinical.get('evidence', {}).get('competing_trials', 'unknown')} "
                    "competing trials"
                ),
                "counterevidenceSummary": (
                    clinical.get("counterfactual", {}).get("change")
                    if isinstance(clinical.get("counterfactual"), dict)
                    else None
                ),
                "limitations": [
                    f"Input source: {stages['recruitability'].get('input_source', 'not reported')}",
                    "Recruitability score is not probability of approval",
                ],
                "sites": clinical.get("sites"),
                "requiredN": clinical.get("required_n"),
            },
        ),
        node(
            "simulation",
            "simulation-slot-0",
            "recruitability-slot-0",
            f"{target} small-molecule tractability",
            {"verdict": None, "precedent": None, "computed": None},
            (
                "Small-molecule tractability is a categorical verdict over two evidence "
                "axes reported separately (retrieved precedent, computed tractability); "
                "the axes are never averaged into a scalar."
            ),
            {
                "slot": 0,
                "biomarkerSlot": 0,
                "summary": (
                    f"Verdict: {simulation_verdict} · basis: "
                    f"{simulation_verdict_basis or 'not reported'}"
                ),
                "evidenceSummary": (
                    f"{simulation_origin} small-molecule tractability dossier · "
                    f"verdict {simulation_verdict}"
                ),
                "counterevidenceSummary": (
                    f"Axis conflict: {simulation_axis_conflict}"
                    if simulation_axis_conflict
                    else "No axis conflict recorded between the two evidence axes"
                ),
                "limitations": [
                    "Small-molecule tractability dossier, not a molecular-dynamics score",
                    "Two axes reported separately; no scalar is imputed from them",
                ]
                + list(stages["simulation"].get("warnings", [])),
                "verdict": simulation_verdict,
                "verdictBasis": simulation_verdict_basis,
                "axisConflict": simulation_axis_conflict,
                "retrievedPrecedent": retrieved_precedent_axis,
                "computedTractability": computed_tractability_axis,
            },
        ),
    ]
    return nodes


def project_ui_state(
    root: Path,
    registry: ModuleRegistry,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    root = root.resolve()
    outputs = load_validated_outputs(root, manifest)
    setup = manifest["setup"]
    stages = {stage["id"]: stage for stage in manifest["stages"]}
    terminal_count = sum(stage["status"] in TERMINAL_STAGE_STATUSES for stage in manifest["stages"])
    origins = {
        stage["output_origin"]
        for stage in manifest["stages"]
        if stage["output_origin"] != "NOT_RUN"
    }
    if "LIVE" in origins and origins - {"LIVE"}:
        data_basis = "LIVE + LABELED FALLBACKS"
    elif origins == {"LIVE"}:
        data_basis = "LIVE MODULE OUTPUTS"
    elif origins:
        data_basis = "CACHED / LABELED FALLBACKS"
    else:
        data_basis = "NO MODULE OUTPUTS YET"
    truth = {
        "designStatus": "PROPOSED TARGET",
        "dataBasis": data_basis,
        "runtimeStatus": f"{terminal_count} OF 5 MODULES TERMINAL",
    }
    program = _actual_program(setup, stages, outputs)
    programs = [program] if program else []
    highlander_programs = programs + (_proxy_programs() if program else [])
    nonterminal = 5 - terminal_count
    packet_count = 1 if program else 0
    counts = {
        "complete": 0,
        "partial": packet_count,
        "blocked": 0 if program else 1,
        "nonterminal": nonterminal,
    }
    stage_projection = [_stage_projection(stages[stage_id]) for stage_id in VISUAL_STAGE_ORDER]
    modules = []
    for module in registry.modules:
        stage = _stage_by_ui(manifest, module.ui_stage)
        modules.append(
            {
                "id": module.module_id,
                "stage": module.ui_stage,
                "repository": module.repository,
                "gitSha": module.commit,
                "configuredMode": module.mode.upper(),
                "runtimeMaturity": module.runtime_maturity,
                "stageStatus": stage["status"],
                "executionStatus": stage["execution_status"],
                "outputOrigin": stage["output_origin"],
                "reasonCode": stage["reason_code"],
                "qualifiers": list(stage["qualifiers"]),
            }
        )
    return {
        "schemaVersion": "labrador.ui-run-state.v1",
        "revision": manifest["revision"],
        "runId": manifest["run_id"],
        "runStatus": manifest["run_status"],
        "updatedAt": manifest["updated_at"],
        "setupSnapshot": {
            "indication": setup["validatedIndication"],
            "submittedIndication": setup["clinicalIndication"],
            "biomarkers": setup["maxBiomarkers"],
            "papers": setup["maxLiteraturePapers"],
            "hypotheses": setup["maxHypothesesPerBiomarker"],
            "biomarkerRange": list(setup["biomarkerRange"]),
            "hypothesisRange": list(setup["hypothesisRange"]),
            "profileRef": setup.get("profileRef"),
            "programIdentity": dict(
                setup.get("programFrame", {}).get("identity", {})
                if isinstance(setup.get("programFrame"), dict)
                else {}
            ),
        },
        "truth": truth,
        "stages": stage_projection,
        "uiProjection": {
            "runData": {
                "biomarkers": (
                    [
                        {
                            "slot": 0,
                            "id": "bio-slot-0",
                            "label": str(
                                setup.get("programFrame", {})
                                .get("identity", {})
                                .get("targetSymbol", "Target")
                            ),
                            "summary": str(
                                setup.get("programFrame", {})
                                .get("identity", {})
                                .get("displayName", setup["validatedIndication"])
                            ),
                            "metrics": _nodes(setup, stages, outputs, program)[0]["metrics"],
                            "uncertainty": (
                                "Search coverage was reported as truncated."
                                if "TRUNCATED_SEARCH"
                                in stages["biomarker"].get("qualifiers", [])
                                else "See the evidence module coverage and warnings."
                            ),
                        }
                    ]
                    if program
                    else []
                ),
                "programs": programs,
                "requestedLanes": setup["maxBiomarkers"]
                * setup["maxHypothesesPerBiomarker"],
                "biomarkerShortfall": max(0, setup["maxBiomarkers"] - (1 if program else 0)),
                "hypothesisShortfall": max(
                    0,
                    setup["maxBiomarkers"] * setup["maxHypothesesPerBiomarker"]
                    - len(programs),
                ),
            },
            "nodes": _nodes(setup, stages, outputs, program),
        },
        "highlander": {
            "ready": bool(manifest["highlander"]["ready"]),
            "launched": bool(manifest["highlander"]["launched"]),
            "jobId": manifest["highlander"]["job_id"],
            "packetSnapshot": manifest["highlander"]["packet_snapshot"],
            "requiresGapAcknowledgement": bool(
                manifest["highlander"]["requires_gap_acknowledgement"]
            ),
            "counts": counts,
            "programs": highlander_programs,
            "comparisonBasis": (
                "One integrated candidate plus two clearly labeled illustrative proxy shells; "
                "no Highlander module repository is wired."
            ),
        },
        "modules": modules,
        "warnings": list(manifest["warnings"]),
        "errors": list(manifest["errors"]),
    }
