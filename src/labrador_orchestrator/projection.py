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
    values = []
    for link in evidence.get("links", []):
        if not isinstance(link, dict):
            continue
        confidence = link.get("confidence")
        if isinstance(confidence, dict):
            confidence = confidence.get("overall")
        values.append(_number(confidence))
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


def _hypothesis_cards(output: Any) -> list[dict[str, Any]]:
    """Normalize the official cards payload and the hermetic legacy fixture."""

    if not isinstance(output, dict):
        return []
    cards = output.get("hypotheses")
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)][:3]
    record = output.get("hypothesis")
    if not isinstance(record, dict):
        return []
    scores = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    return [
        {
            "id": record.get("id") or "program-1",
            "headline": (
                f"{record.get('subject_name', 'Candidate')} → "
                f"{record.get('object_name', 'mechanism')}"
            ),
            "statement": None,
            "trace": record.get("provenance") or "Graph-derived structural candidate",
            "motif": record.get("motif") or "legacy_fixture",
            "metrics": {
                "support": scores.get("support"),
                "novelty": scores.get("novelty"),
                "testability": scores.get("testability"),
                "rank": record.get("rank_score"),
            },
            "highlights": [],
        }
    ]


def _biomarker_signals(evidence: Any, count: int) -> list[dict[str, Any]]:
    """Select graph-grounded RA readout signals; these are not clinical biomarkers."""

    if not isinstance(evidence, dict):
        return []
    things = {
        str(thing.get("id")): thing
        for thing in evidence.get("things", [])
        if isinstance(thing, dict) and thing.get("id")
    }
    preferred = ["t2", "t3", "t5"]
    selected = [things[thing_id] for thing_id in preferred if thing_id in things]
    if not selected:
        selected = [
            thing
            for thing in things.values()
            if str(thing.get("kind", "")).casefold() in {"process", "biomarker"}
        ]
    if not selected:
        selected = [
            {"id": f"signal-{index + 1}", "name": "Target evidence signal"}
            for index in range(count)
        ]
    return selected[:count]


def _program_from_card(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
    card: dict[str, Any],
    signal: dict[str, Any],
    biomarker_slot: int,
    hypothesis_slot: int,
) -> dict[str, Any] | None:
    card_metrics = card.get("metrics") if isinstance(card.get("metrics"), dict) else {}
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
    hypothesis_label = str(card.get("headline") or card.get("trace") or f"{target} mechanism")
    signal_name = str(signal.get("name") or f"Signal {biomarker_slot + 1}")
    trace = str(card.get("trace") or "")
    association_basis = (
        "SOURCE_PATH" if signal_name.casefold() in trace.casefold() else "CONTEXT_ONLY"
    )
    association_qualifier = (
        "PARENT_SIGNAL_ON_SOURCE_PATH"
        if association_basis == "SOURCE_PATH"
        else "PARENT_SIGNAL_CONTEXT_ONLY"
    )
    p10 = _number(summary.get("p10_rnpv"))
    p90 = _number(summary.get("p90_rnpv"))
    decision_grade = roi_payload.get("decision_grade") or summary.get("recommendation")
    qualifier = str(decision_grade or "NOT_DECISION_GRADE")
    simulation_stage = stages["simulation"]
    citations = _paper_citations(outputs.get("evidence_mapper"))
    metrics = {
        "boldness": round((low + high) / 2, 1),
        "evidence": _percent(card_metrics.get("support")),
        "plausibility": _percent(card_metrics.get("rank")),
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
        # This repository supplies a tractability dossier, not atomistic simulation.
        "support": None,
        "occupancy": None,
        "convergence": None,
    }
    lane = (
        biomarker_slot * int(setup["maxHypothesesPerBiomarker"])
        + hypothesis_slot
    )
    source_candidate_id = str(card.get("id") or f"candidate-{hypothesis_slot + 1}")
    signal_id = str(signal.get("id") or f"signal-{biomarker_slot + 1}")
    candidate_id = f"ctx-{signal_id}--{source_candidate_id}"
    return {
        "id": candidate_id,
        "sourceHypothesisId": source_candidate_id,
        "biomarkerGraphThingId": signal_id,
        "associationBasis": association_basis,
        "lane": lane,
        "biomarkerSlot": biomarker_slot,
        "hypothesisSlot": hypothesis_slot,
        "hypothesisNodeId": f"hyp-slot-{lane}",
        "roiNodeId": f"roi-slot-{lane}",
        "recruitNodeId": f"recruitability-slot-{lane}",
        "simulationNodeId": f"simulation-slot-{lane}",
        "label": f"{source_candidate_id} · {hypothesis_label}",
        "short": f"{signal_id} · {source_candidate_id} · {target}/{indication}",
        "metrics": metrics,
        "uncertainty": (
            f"rNPV P10–P90: ${float(p10) / 1_000_000:.1f}M to "
            f"${float(p90) / 1_000_000:.1f}M · {qualifier}"
            if p10 is not None and p90 is not None
            else f"Economics unavailable · {qualifier}"
        ),
        "publicWhy": (
            "SHARED_HYPOTHESIS_SLATE_ACROSS_BIOMARKERS / "
            "NOT_INDEPENDENT_GENERATIONS: "
            f"HypGen candidate {source_candidate_id} is contextualized beneath evidence-graph "
            f"signal {signal_id}. This pairing is {association_basis.replace('_', ' ').lower()}; "
            "the same native slate is shown beneath each requested signal. "
            "SHARED_RA_ANALYST_FRAME / NOT_CANDIDATE_SPECIFIC: "
            f"structural candidate {source_candidate_id} for {display_name}. Recruitment, ROI, and "
            "tractability are the same explicit RA analyst-frame records on every candidate; "
            "only the hypothesis metrics differ."
        ),
        "roiFailed": "roi_calculator" not in outputs,
        "recruitFailed": "clinical_simulation" not in outputs,
        "overflowRnpv": False,
        "notAmenable": simulation.get("verdict") == "not_tractable",
        "revision": "packet-r1",
        "hash": stages["roi"].get("output_hash") or stages["hypothesis"].get("output_hash"),
        "paretoStatus": "non-dominated" if metrics["rnpv"] is not None else "incomparable",
        "sourceType": "HYPGEN_CARD_CONTEXT_BRANCH_WITH_SHARED_DOWNSTREAM",
        "qualifiers": sorted(
            set(
                stages["biomarker"]["qualifiers"]
                + stages["hypothesis"]["qualifiers"]
                + stages["roi"]["qualifiers"]
                + stages["recruitability"]["qualifiers"]
                + stages["simulation"]["qualifiers"]
                + [
                    "SHARED_HYPOTHESIS_SLATE_ACROSS_BIOMARKERS",
                    "NOT_INDEPENDENT_GENERATIONS",
                    association_qualifier,
                    "SHARED_RA_ANALYST_FRAME",
                    "NOT_CANDIDATE_SPECIFIC",
                ]
            )
        ),
        "citations": citations,
        "economicsBasis": qualifier,
        "simulationBasis": (
            f"{simulation_stage['output_origin'].replace('_', ' ')} TRACTABILITY · "
            "NOT ATOMISTIC"
        ),
        "gaps": [
            (
                "The native HypGen slate is contextualized across graph signals; the lane "
                "records are not independent generations."
            ),
            "Downstream records are shared RA analyst-frame records, not candidate-specific.",
            (
                "Recruitability uses an explicit analyst clinical thesis, "
                "not inferred hypothesis prose."
            ),
            f"ROI basis: {qualifier}.",
            "Tractability evidence is not an atomistic simulation result.",
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


def _actual_programs(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
) -> list[dict[str, Any]]:
    hypothesis_output = outputs.get("hypothesis_generator")
    cards = _hypothesis_cards(hypothesis_output)
    signals = _biomarker_signals(
        outputs.get("evidence_mapper"), int(setup["maxBiomarkers"])
    )
    biomarker_limit = min(len(signals), int(setup["maxBiomarkers"]))
    hypothesis_limit = min(len(cards), int(setup["maxHypothesesPerBiomarker"]))
    official_slate = isinstance(hypothesis_output, dict) and isinstance(
        hypothesis_output.get("hypotheses"), list
    )
    if not official_slate:
        # Preserve the legacy single-document behavior used by custom and hermetic flows.
        limit = min(biomarker_limit, hypothesis_limit)
        return [
            program
            for index in range(limit)
            if (
                program := _program_from_card(
                    setup,
                    stages,
                    outputs,
                    cards[index],
                    signals[index],
                    index,
                    0,
                )
            )
            is not None
        ]
    return [
        program
        for biomarker_slot, signal in enumerate(signals[:biomarker_limit])
        for hypothesis_slot, card in enumerate(cards[:hypothesis_limit])
        if (
            program := _program_from_card(
                setup,
                stages,
                outputs,
                card,
                signal,
                biomarker_slot,
                hypothesis_slot,
            )
        )
        is not None
    ]


def _nodes_for_program(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
    program: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence_value = outputs.get("evidence_mapper")
    evidence = evidence_value if isinstance(evidence_value, dict) else {}
    hypothesis_value = outputs.get("hypothesis_generator")
    cards = _hypothesis_cards(hypothesis_value)
    source_hypothesis_id = str(program.get("sourceHypothesisId") or program["id"])
    hypothesis_record = next(
        (card for card in cards if str(card.get("id")) == source_hypothesis_id),
        {},
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
    lane = int(program["lane"])
    biomarker_slot = int(program["biomarkerSlot"])
    signals = _biomarker_signals(evidence, int(setup["maxBiomarkers"]))
    signal = signals[biomarker_slot] if biomarker_slot < len(signals) else {}
    signal_id = str(signal.get("id") or f"signal-{biomarker_slot + 1}")
    signal_name = str(signal.get("name") or target)
    bio_node_id = f"bio-slot-{biomarker_slot}"
    hypothesis_node_id = f"hyp-slot-{lane}"
    roi_node_id = f"roi-slot-{lane}"
    recruit_node_id = f"recruitability-slot-{lane}"
    common = {"lane": lane, "kind": "real"}

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
    nodes = [
        node(
            "biomarker",
            bio_node_id,
            "indication-root",
            signal_name,
            {
                "exploration": round((low + high) / 2, 1),
                "evidence": _evidence_metric(evidence),
                "pursuit": None,
            },
            evidence_uncertainty,
            {
                "slot": biomarker_slot,
                "graphThingId": signal_id,
                "summary": (
                    f"Candidate mechanistic/PD readout for {target} in {indication}; "
                    "not a validated clinical biomarker"
                ),
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
            hypothesis_node_id,
            bio_node_id,
            program["label"],
            {
                "boldness": program["metrics"]["boldness"],
                "evidence": program["metrics"]["evidence"],
                "plausibility": program["metrics"]["plausibility"],
            },
            hypothesis_uncertainty,
            {
                "slot": program["hypothesisSlot"],
                "biomarkerSlot": biomarker_slot,
                "sourceHypothesisId": source_hypothesis_id,
                "biomarkerGraphThingId": signal_id,
                "summary": hypothesis_record.get("trace", "Hypothesis generated from graph"),
                "evidenceSummary": (
                    f"Support score {hypothesis_record.get('metrics', {}).get('support')}"
                ),
                "counterevidenceSummary": "; ".join(
                    highlight.get("text", "")
                    for highlight in hypothesis_record.get("highlights", [])
                    if isinstance(highlight, dict)
                    and highlight.get("kind") == "contradiction"
                ),
                "limitations": [
                    highlight.get("text")
                    for highlight in hypothesis_record.get("highlights", [])
                    if isinstance(highlight, dict)
                    and highlight.get("kind") in {"caution", "failure"}
                ],
                "citations": program["citations"],
            },
        ),
        node(
            "roi",
            roi_node_id,
            hypothesis_node_id,
            f"rNPV · {display_name}",
            {
                "rnpv": program["metrics"]["rnpv"],
                "positive": program["metrics"]["positive"],
                "impact": None,
            },
            program["uncertainty"],
            {
                "slot": program["hypothesisSlot"],
                "biomarkerSlot": biomarker_slot,
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
            recruit_node_id,
            roi_node_id,
            f"{indication} enrollment feasibility",
            {
                "recruit": program["metrics"]["recruit"],
                "duration": program["metrics"]["duration"],
                "screens": program["metrics"]["screens"],
                "risk": program["metrics"]["risk"],
            },
            f"Modeled enrollment range: {clinical.get('simulated_months_range', 'missing')}",
            {
                "slot": program["hypothesisSlot"],
                "biomarkerSlot": biomarker_slot,
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
            f"simulation-slot-{lane}",
            recruit_node_id,
            f"{target} tractability dossier",
            {"support": None, "occupancy": None, "convergence": None},
            "Two evidence axes remain separate; no atomistic score was produced.",
            {
                "slot": program["hypothesisSlot"],
                "biomarkerSlot": biomarker_slot,
                "summary": (
                    f"Verdict: {simulation.get('verdict', 'missing')} · basis: "
                    f"{simulation.get('verdict_basis', 'missing')}"
                ),
                "evidenceSummary": f"{simulation_origin} tractability output",
                "counterevidenceSummary": (
                    simulation.get("axis_conflict") or "No axis conflict recorded"
                ),
                "limitations": [
                    "Tractability output is not atomistic simulation",
                    "No scalar is imputed from separate dossier axes",
                ]
                + list(stages["simulation"].get("warnings", [])),
                "verdict": simulation.get("verdict"),
                "verdictBasis": simulation.get("verdict_basis"),
            },
        ),
    ]
    return nodes


def _nodes(
    setup: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any],
    programs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for program in programs:
        for node in _nodes_for_program(setup, stages, outputs, program):
            if node["id"] in seen_ids:
                continue
            seen_ids.add(node["id"])
            nodes.append(node)
    return nodes


def project_ui_state(
    root: Path,
    registry: ModuleRegistry,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if manifest.get("scientific", {}).get("enabled"):
        from .scientific_projection import project_scientific_ui_state

        return project_scientific_ui_state(root, registry, manifest)
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
    if "DEMO_FALLBACK" in origins and "LIVE" in origins:
        data_basis = "LIVE + LABELED FALLBACKS"
    elif "LIVE" in origins and origins - {"LIVE"}:
        data_basis = "LIVE + LABELED REPLAYS / CACHED OUTPUTS"
    elif origins == {"LIVE"}:
        data_basis = "LIVE MODULE OUTPUTS"
    elif origins:
        data_basis = "LABELED REPLAYS / CACHED OUTPUTS"
    else:
        data_basis = "NO MODULE OUTPUTS YET"
    truth = {
        "designStatus": "PROPOSED TARGET",
        "dataBasis": data_basis,
        "runtimeStatus": f"{terminal_count} OF 5 MODULES TERMINAL",
    }
    programs = _actual_programs(setup, stages, outputs)
    nodes = _nodes(setup, stages, outputs, programs)
    signals = _biomarker_signals(
        outputs.get("evidence_mapper"), int(setup["maxBiomarkers"])
    )
    biomarker_nodes = {
        int(node["metadata"]["slot"]): node
        for node in nodes
        if node["stage"] == "biomarker"
    }
    biomarkers = []
    for slot, signal in enumerate(signals[: int(setup["maxBiomarkers"])]):
        node = biomarker_nodes.get(slot)
        if node is None:
            continue
        biomarkers.append(
            {
                "slot": slot,
                "id": f"bio-slot-{slot}",
                "graph_thing_id": str(signal.get("id")),
                "label": str(signal.get("name") or f"RA readout {slot + 1}"),
                "summary": (
                    "Evidence-graph mechanistic/PD readout candidate; not a validated "
                    "clinical biomarker."
                ),
                "metrics": node["metrics"],
                "uncertainty": (
                    "Recorded search was truncated and quotes remain unverified; inspect the "
                    "evidence packet."
                ),
            }
        )
    nonterminal = 5 - terminal_count
    packet_count = len(programs)
    source_hypothesis_count = len(
        {
            program.get("sourceHypothesisId") or program["id"]
            for program in programs
        }
    )
    counts = {
        "complete": 0,
        "partial": packet_count,
        "blocked": 0 if programs else 1,
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
                "biomarkers": biomarkers,
                "programs": programs,
                "requestedLanes": setup["maxBiomarkers"]
                * setup["maxHypothesesPerBiomarker"],
                "biomarkerShortfall": max(0, setup["maxBiomarkers"] - len(biomarkers)),
                "hypothesisShortfall": max(
                    0,
                    setup["maxBiomarkers"] * setup["maxHypothesesPerBiomarker"]
                    - len(programs),
                ),
            },
            "nodes": nodes,
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
            "programs": programs,
            "comparisonBasis": (
                f"{source_hypothesis_count} real HypGen structural candidates are "
                f"contextualized beneath {len(biomarkers)} evidence-graph signals as "
                f"{len(programs)} lane records. These are not independent generations, and "
                "all downstream RA records are shared. The frontend performs the current "
                "client-side Pareto comparison; the Highlander server consumer is not wired."
            ),
        },
        "modules": modules,
        "warnings": list(manifest["warnings"]),
        "errors": list(manifest["errors"]),
    }
