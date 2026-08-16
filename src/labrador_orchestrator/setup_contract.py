"""Versioned run-request validation and explicit program-frame resolution."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .contracts import ContractError, canonical_json, load_json, validate_json

if TYPE_CHECKING:
    from .registry import ModuleRegistry


LEGACY_FIELDS = {
    "clinicalIndication",
    "biomarkerRange",
    "maxBiomarkers",
    "maxLiteraturePapers",
    "hypothesisRange",
    "maxHypothesesPerBiomarker",
}
FRONTEND_V0_FIELDS = {
    "clinical_indication",
    "biomarker_exploration_range",
    "maximum_biomarkers",
    "maximum_literature_papers",
    "hypothesis_boldness_range",
    "maximum_hypotheses_per_biomarker",
}
GOLDEN_PROFILE = "golden.ra-irak4.v1"


def _exact_object(value: Any, *, label: str, required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    missing = sorted(required - set(value))
    extras = sorted(set(value) - required)
    if missing:
        raise ContractError(f"{label} is missing fields: {', '.join(missing)}")
    if extras:
        raise ContractError(f"{label} has unknown fields: {', '.join(extras)}")
    return value


def _interval(value: Any, *, label: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError(f"{label} must be a two-value array")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ContractError(f"{label} values must be integers")
    low, high = value
    if not 1 <= low <= high <= 10:
        raise ContractError(f"{label} must satisfy 1 <= lower <= upper <= 10")
    return [low, high]


def _ceiling(value: Any, *, label: str, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ContractError(f"{label} must be an integer from 1 through {upper}")
    return value


def _text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _same(left: Any, right: Any) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and _normalized(left) == _normalized(right)
    )


def _golden_frame(registry: ModuleRegistry | None) -> dict[str, Any]:
    golden = registry.golden_program if registry is not None else {}
    target = str(golden.get("target") or "IRAK4")
    indication = str(golden.get("indication") or "Rheumatoid arthritis")
    accession = str(golden.get("uniprot_accession") or "Q9NWZ3")
    modality = str(golden.get("modality") or "small_molecule")
    evidence: dict[str, Any] = {
        "ask": "new_question",
        "target": (
            "Can a small-molecule IRAK4 inhibitor suppress synovial fibroblast-driven "
            "inflammation in rheumatoid arthritis?"
        ),
        "depth": "deep",
    }
    clinical: dict[str, Any] | None = None
    simulation_context: dict[str, Any] = {
        "interactionToDisrupt": (
            "IRAK4 catalytic function - ATP-competitive occupancy of the kinase domain"
        )
    }
    if registry is not None:
        root = registry.root
        evidence = copy.deepcopy(load_json(root / "fixtures/golden/evidence.request.json"))
        clinical = copy.deepcopy(load_json(root / "fixtures/golden/clinical-input.json"))
        simulation_input = copy.deepcopy(load_json(root / "fixtures/golden/simulation-input.json"))
        simulation_context = {
            "interactionToDisrupt": simulation_input.get("interaction_to_disrupt"),
            "mechanismHypothesis": simulation_input.get("mechanism_hypothesis"),
            "asOfDate": simulation_input.get("as_of_date"),
        }
    return {
        "schemaVersion": "labrador.program-frame.v1",
        "frameId": GOLDEN_PROFILE,
        "basis": "PROFILE_FIXTURE",
        "identity": {
            "programId": "IRAK4-RA-DEMO",
            "displayName": f"{target} inhibition in {indication}",
            "indication": indication,
            "targetSymbol": target,
            "uniprotAccession": accession,
            "modality": modality,
        },
        "evidenceRequest": evidence,
        "clinicalThesis": clinical,
        "plannedEnrollmentMonths": 18,
        "simulationContext": simulation_context,
        "roiRequest": None,
        "notes": [
            "Frozen presentation profile; scientific and economic fixture values are not defaults."
        ],
    }


def _resolved(
    *,
    submitted_indication: str,
    indication: str,
    biomarker_range: list[int],
    max_biomarkers: int,
    max_papers: int,
    hypothesis_range: list[int],
    max_hypotheses: int,
    request_schema: str,
    profile_ref: str | None,
    frame: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "labrador.resolved-run-setup.v2",
        "requestSchemaVersion": request_schema,
        "clinicalIndication": submitted_indication,
        "validatedIndication": indication,
        "normalizedIndication": indication,
        "biomarkerRange": biomarker_range,
        "maxBiomarkers": max_biomarkers,
        "maxLiteraturePapers": max_papers,
        "hypothesisRange": hypothesis_range,
        "maxHypothesesPerBiomarker": max_hypotheses,
        "profileRef": profile_ref,
        "fallbackPolicy": "PROFILE_MATCH_ONLY" if profile_ref else "DISABLED",
        "programFrame": frame,
    }


def _legacy(raw: dict[str, Any], registry: ModuleRegistry | None) -> dict[str, Any]:
    extras = sorted(set(raw) - LEGACY_FIELDS)
    missing = sorted(LEGACY_FIELDS - set(raw))
    if extras:
        raise ContractError(f"unknown setup fields: {', '.join(extras)}")
    if missing:
        raise ContractError(f"missing setup fields: {', '.join(missing)}")
    submitted = raw["clinicalIndication"]
    if not isinstance(submitted, str):
        raise ContractError("clinicalIndication must be a string")
    trimmed = submitted.strip()
    if not trimmed:
        raise ContractError("clinicalIndication is required")
    normalized = _normalized(trimmed)
    if normalized == "ra":
        normalized = "rheumatoid arthritis"
    if normalized != "rheumatoid arthritis":
        raise ContractError(
            "this legacy demo request supports only the explicit Rheumatoid arthritis profile; "
            "use labrador.run-setup.v2 with an analyst frame for another program"
        )
    frame = _golden_frame(registry)
    result = _resolved(
        submitted_indication=submitted,
        indication=trimmed,
        biomarker_range=_interval(raw["biomarkerRange"], label="biomarkerRange"),
        max_biomarkers=_ceiling(raw["maxBiomarkers"], label="maxBiomarkers", upper=5),
        max_papers=_ceiling(
            raw["maxLiteraturePapers"], label="maxLiteraturePapers", upper=100
        ),
        hypothesis_range=_interval(raw["hypothesisRange"], label="hypothesisRange"),
        max_hypotheses=_ceiling(
            raw["maxHypothesesPerBiomarker"],
            label="maxHypothesesPerBiomarker",
            upper=5,
        ),
        request_schema="labrador.run-setup.v1",
        profile_ref=GOLDEN_PROFILE,
        frame=frame,
    )
    result["normalizedIndication"] = "Rheumatoid arthritis"
    return result


def is_frontend_v0_request(raw: Any) -> bool:
    """Return whether a payload is using the frontend's snake-case v0 dialect."""

    return isinstance(raw, dict) and bool(set(raw) & FRONTEND_V0_FIELDS)


def _frontend_v0(raw: dict[str, Any], registry: ModuleRegistry | None) -> dict[str, Any]:
    """Translate the six-field frontend contract into the immutable internal setup."""

    _exact_object(raw, label="frontend run setup", required=FRONTEND_V0_FIELDS)
    indication = _exact_object(
        raw["clinical_indication"],
        label="clinical_indication",
        required={"submitted_text"},
    )
    biomarker = _exact_object(
        raw["biomarker_exploration_range"],
        label="biomarker_exploration_range",
        required={"lower", "upper"},
    )
    hypothesis = _exact_object(
        raw["hypothesis_boldness_range"],
        label="hypothesis_boldness_range",
        required={"lower", "upper"},
    )
    translated = {
        "clinicalIndication": indication["submitted_text"],
        "biomarkerRange": [biomarker["lower"], biomarker["upper"]],
        "maxBiomarkers": raw["maximum_biomarkers"],
        "maxLiteraturePapers": raw["maximum_literature_papers"],
        "hypothesisRange": [hypothesis["lower"], hypothesis["upper"]],
        "maxHypothesesPerBiomarker": raw["maximum_hypotheses_per_biomarker"],
    }
    result = _legacy(translated, registry)
    result["requestSchemaVersion"] = "labrador.frontend-run-setup.v0"
    return result


def _identity_check(frame: dict[str, Any]) -> None:
    identity = frame["identity"]
    clinical = frame["clinicalThesis"]
    if isinstance(clinical, dict):
        checks = (
            (identity["targetSymbol"], clinical.get("target", {}).get("symbol"), "target"),
            (identity["indication"], clinical.get("disease", {}).get("name"), "indication"),
            (identity["modality"], clinical.get("asset", {}).get("modality"), "modality"),
        )
        accession = clinical.get("target", {}).get("uniprot_accession")
        if accession is not None and identity["uniprotAccession"] is not None:
            checks += ((identity["uniprotAccession"], accession, "UniProt accession"),)
        for expected, actual, label in checks:
            if not _same(expected, actual):
                raise ContractError(
                    f"FRAME_IDENTITY_MISMATCH: clinical {label} {actual!r} "
                    f"does not match frame {expected!r}"
                )
    roi = frame["roiRequest"]
    if isinstance(roi, dict):
        program = roi.get("program") if isinstance(roi.get("program"), dict) else {}
        checks = (
            (identity["programId"], program.get("program_id"), "program id"),
            (identity["targetSymbol"], program.get("target"), "target"),
            (identity["modality"], program.get("modality"), "modality"),
        )
        indication = program.get("initial_indication")
        if isinstance(indication, dict) and indication.get("name") is not None:
            checks += ((identity["indication"], indication.get("name"), "indication"),)
        for expected, actual, label in checks:
            if not _same(expected, actual):
                raise ContractError(
                    f"FRAME_IDENTITY_MISMATCH: ROI {label} {actual!r} "
                    f"does not match frame {expected!r}"
                )


def _v2(raw: dict[str, Any], registry: ModuleRegistry | None) -> dict[str, Any]:
    if registry is None:
        raise ContractError("labrador.run-setup.v2 validation requires a module registry")
    _exact_object(
        raw,
        label="labrador.run-setup.v2",
        required={"schemaVersion", "exploration", "program"},
    )
    exploration = _exact_object(
        raw["exploration"],
        label="exploration",
        required={"evidenceRequest", "hypothesis", "presentation"},
    )
    hypothesis = _exact_object(
        exploration["hypothesis"],
        label="exploration.hypothesis",
        required={"boldnessRange"},
    )
    presentation = _exact_object(
        exploration["presentation"],
        label="exploration.presentation",
        required={
            "biomarkerRange",
            "maxBiomarkers",
            "maxLiteraturePapers",
            "maxHypothesesPerBiomarker",
        },
    )
    program = raw["program"]
    if not isinstance(program, dict) or set(program) not in ({"frame"}, {"profileRef"}):
        raise ContractError("program must contain exactly one of frame or profileRef")
    if "profileRef" in program:
        if program["profileRef"] != GOLDEN_PROFILE:
            raise ContractError(f"unknown program profile {program['profileRef']!r}")
        frame = _golden_frame(registry)
        validate_json(
            load_json(registry.by_id("evidence_mapper").input_schema),
            exploration["evidenceRequest"],
            label="evidence_mapper profile request",
        )
        if canonical_json(exploration["evidenceRequest"]) != canonical_json(
            frame["evidenceRequest"]
        ):
            raise ContractError(
                "PROFILE_REQUEST_MISMATCH: evidenceRequest does not match "
                "golden.ra-irak4.v1"
            )
        profile_ref: str | None = GOLDEN_PROFILE
    else:
        frame = copy.deepcopy(
            _exact_object(
                program["frame"],
                label="program.frame",
                required={
                    "schemaVersion",
                    "frameId",
                    "basis",
                    "identity",
                    "clinicalThesis",
                    "plannedEnrollmentMonths",
                    "simulationContext",
                    "roiRequest",
                    "notes",
                },
            )
        )
        if frame["schemaVersion"] != "labrador.program-frame.v1":
            raise ContractError("program.frame must use labrador.program-frame.v1")
        if frame["basis"] != "ANALYST_SUPPLIED":
            raise ContractError("inline program.frame basis must be ANALYST_SUPPLIED")
        _text(frame["frameId"], label="program.frame.frameId")
        identity = _exact_object(
            frame["identity"],
            label="program.frame.identity",
            required={
                "programId",
                "displayName",
                "indication",
                "targetSymbol",
                "uniprotAccession",
                "modality",
            },
        )
        for field in ("programId", "displayName", "indication", "targetSymbol", "modality"):
            _text(identity[field], label=f"program.frame.identity.{field}")
        if identity["uniprotAccession"] is not None:
            _text(
                identity["uniprotAccession"],
                label="program.frame.identity.uniprotAccession",
            )
        if not isinstance(frame["notes"], list) or not all(
            isinstance(note, str) for note in frame["notes"]
        ):
            raise ContractError("program.frame.notes must be an array of strings")
        planned = frame["plannedEnrollmentMonths"]
        if planned is not None and (
            isinstance(planned, bool) or not isinstance(planned, (int, float)) or planned <= 0
        ):
            raise ContractError("plannedEnrollmentMonths must be a positive number or null")
        simulation_context = _exact_object(
            frame["simulationContext"],
            label="program.frame.simulationContext",
            required={"interactionToDisrupt"},
        )
        interaction = simulation_context["interactionToDisrupt"]
        if interaction is not None and not isinstance(interaction, str):
            raise ContractError("interactionToDisrupt must be a string or null")
        frame["evidenceRequest"] = copy.deepcopy(exploration["evidenceRequest"])
        validate_json(
            load_json(registry.by_id("evidence_mapper").input_schema),
            frame["evidenceRequest"],
            label="evidence_mapper analyst input",
        )
        if frame["clinicalThesis"] is not None:
            validate_json(
                load_json(registry.by_id("clinical_simulation").input_schema),
                frame["clinicalThesis"],
                label="clinical_simulation analyst input",
            )
        if frame["roiRequest"] is not None:
            validate_json(
                load_json(registry.by_id("roi_calculator").input_schema),
                frame["roiRequest"],
                label="roi_calculator analyst input",
            )
        _identity_check(frame)
        profile_ref = None
    identity = frame["identity"]
    return _resolved(
        submitted_indication=identity["indication"],
        indication=identity["indication"],
        biomarker_range=_interval(
            presentation["biomarkerRange"], label="presentation.biomarkerRange"
        ),
        max_biomarkers=_ceiling(
            presentation["maxBiomarkers"], label="presentation.maxBiomarkers", upper=5
        ),
        max_papers=_ceiling(
            presentation["maxLiteraturePapers"],
            label="presentation.maxLiteraturePapers",
            upper=100,
        ),
        hypothesis_range=_interval(
            hypothesis["boldnessRange"], label="hypothesis.boldnessRange"
        ),
        max_hypotheses=_ceiling(
            presentation["maxHypothesesPerBiomarker"],
            label="presentation.maxHypothesesPerBiomarker",
            upper=5,
        ),
        request_schema="labrador.run-setup.v2",
        profile_ref=profile_ref,
        frame=frame,
    )


def validate_setup(raw: Any, *, registry: ModuleRegistry | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("request body must be a JSON object")
    if raw.get("schemaVersion") == "labrador.run-setup.v2":
        return _v2(raw, registry)
    if is_frontend_v0_request(raw):
        return _frontend_v0(raw, registry)
    return _legacy(raw, registry)
