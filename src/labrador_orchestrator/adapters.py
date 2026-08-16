"""Explicit adapters for profile fixtures and analyst-supplied program frames."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .contracts import ContractError, load_json

PLANNED_ENROLLMENT_MONTHS = 18


class StageAbstention(ContractError):
    """A stage has no honest input for this run and must be marked skipped."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_output(outputs: dict[str, Any], module_id: str) -> Any:
    try:
        return copy.deepcopy(outputs[module_id])
    except KeyError as exc:
        raise StageAbstention(
            "MISSING_UPSTREAM_OUTPUT", f"adapter requires missing output from {module_id}"
        ) from exc


def enrollment_delay_years(months: int | float) -> int:
    """Translate an enrollment overrun into the ROI module's launch-delay axis."""

    return max(0, round((float(months) - PLANNED_ENROLLMENT_MONTHS) / 12))


def _profile_roi_input(root: Path, run_id: str, recruitability: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(load_json(root / "fixtures/golden/roi-input-template.json"))
    value["request_id"] = f"{run_id}-roi"
    program = value["program"]
    program.update(
        {
            "program_id": "IRAK4-RA-DEMO",
            "program_name": "IRAK4 inhibition in rheumatoid arthritis (synthetic economics)",
            "molecule_identifier": "IRAK4-SMALL-MOLECULE-DEMO",
            "target": "IRAK4",
            "modality": "SMALL_MOLECULE",
            "route": "ORAL",
        }
    )
    program["assumptions"].update(
        {
            "decision_grade": "NOT_DECISION_GRADE",
            "synthetic": True,
            "note": (
                "IRAK4/RA presentation wrapper around the ROI repository's fully synthetic "
                "economics fixture. It is not a forecast or investment recommendation."
            ),
        }
    )
    indication = program["initial_indication"]
    indication.update(
        {
            "indication_id": "ra-irak4-demo",
            "name": "Rheumatoid arthritis",
            "therapeutic_area": "Immunology",
            "target_population": "Adults with active seropositive rheumatoid arthritis",
            "biomarker": "RF and/or anti-CCP positive",
            "route": "ORAL",
        }
    )
    indication.setdefault("assumptions", {})["note"] = (
        "Every commercial, epidemiologic, pricing, access, and development value remains "
        "synthetic; only the program labels and recruitment-derived delay are adapted."
    )

    point = recruitability.get("simulated_months_to_enroll")
    range_value = recruitability.get("simulated_months_range")
    if not isinstance(point, (int, float)):
        raise ContractError("recruitability output lacks simulated_months_to_enroll")
    if not (
        isinstance(range_value, list)
        and len(range_value) == 2
        and all(isinstance(item, (int, float)) for item in range_value)
    ):
        raise ContractError("recruitability output lacks simulated_months_range")
    fast, slow = sorted(float(item) for item in range_value)
    delay = value["execution"]["simulation_assumptions"]["launch_delay_years"]
    delay.update(
        {
            "low": float(enrollment_delay_years(fast)),
            "mode": float(enrollment_delay_years(float(point))),
            "high": float(enrollment_delay_years(slow)),
        }
    )
    return value


def _overlay_recruitment_delay(
    value: dict[str, Any], recruitability: dict[str, Any], planned_months: int | float
) -> None:
    point = recruitability.get("simulated_months_to_enroll")
    range_value = recruitability.get("simulated_months_range")
    if not isinstance(point, (int, float)) or isinstance(point, bool):
        return
    if not (
        isinstance(range_value, list)
        and len(range_value) == 2
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in range_value
        )
    ):
        return
    assumptions = value.get("execution")
    if not isinstance(assumptions, dict):
        return
    simulation = assumptions.get("simulation_assumptions")
    if not isinstance(simulation, dict):
        return
    delay = simulation.get("launch_delay_years")
    if not isinstance(delay, dict):
        return
    fast, slow = sorted(float(item) for item in range_value)

    def translated(months: float) -> float:
        return max(0.0, (months - float(planned_months)) / 12.0)

    delay.update(
        {
            "low": translated(fast),
            "mode": translated(float(point)),
            "high": translated(slow),
        }
    )


def build_module_input(
    root: Path,
    module_id: str,
    *,
    run_id: str,
    setup: dict[str, Any],
    outputs: dict[str, Any],
) -> Any:
    """Build one module input without guessing scientific fields absent upstream."""

    frame = setup.get("programFrame")
    if not isinstance(frame, dict):
        raise ContractError("resolved setup is missing programFrame")
    profile_ref = setup.get("profileRef")
    if module_id == "evidence_mapper":
        value = frame.get("evidenceRequest")
        if not isinstance(value, dict):
            raise StageAbstention("MISSING_EVIDENCE_REQUEST", "no evidence request was supplied")
        return copy.deepcopy(value)
    if module_id == "hypothesis_generator":
        return _require_output(outputs, "evidence_mapper")
    if module_id == "clinical_simulation":
        value = frame.get("clinicalThesis")
        if value is None and profile_ref:
            value = load_json(root / "fixtures/golden/clinical-input.json")
        if not isinstance(value, dict):
            raise StageAbstention(
                "MISSING_CLINICAL_THESIS",
                "recruitability requires an explicit analyst clinical thesis",
            )
        return copy.deepcopy(value)
    if module_id == "roi_calculator":
        recruitability = outputs.get("clinical_simulation")
        if profile_ref:
            if not isinstance(recruitability, dict):
                raise StageAbstention(
                    "MISSING_RECRUITABILITY_OUTPUT",
                    "the golden ROI profile requires recruitability output",
                )
            return _profile_roi_input(root, run_id, recruitability)
        value = frame.get("roiRequest")
        if not isinstance(value, dict):
            raise StageAbstention(
                "MISSING_ROI_REQUEST", "ROI requires an explicit analyst economic request"
            )
        result = copy.deepcopy(value)
        planned = frame.get("plannedEnrollmentMonths")
        if isinstance(recruitability, dict) and isinstance(planned, (int, float)):
            _overlay_recruitment_delay(result, recruitability, planned)
        return result
    if module_id == "simulation":
        identity = frame.get("identity") if isinstance(frame.get("identity"), dict) else {}
        accession = identity.get("uniprotAccession")
        if not isinstance(accession, str) or not accession.strip():
            raise StageAbstention(
                "MISSING_UNIPROT_ACCESSION",
                "tractability requires an explicitly resolved UniProt accession",
            )
        clinical = frame.get("clinicalThesis")
        clinical = clinical if isinstance(clinical, dict) else {}
        disease = clinical.get("disease") if isinstance(clinical.get("disease"), dict) else {}
        context = (
            frame.get("simulationContext")
            if isinstance(frame.get("simulationContext"), dict)
            else {}
        )
        return {
            "uniprot_accession": accession,
            "as_of_date": context.get("asOfDate"),
            "disease_context": disease.get("name") or identity.get("indication"),
            "interaction_to_disrupt": context.get("interactionToDisrupt"),
            "mechanism_hypothesis": context.get("mechanismHypothesis"),
        }
    raise ContractError(f"no adapter is registered for module {module_id}")


def input_source(module_id: str, setup: dict[str, Any]) -> str:
    if module_id == "hypothesis_generator":
        return "UPSTREAM_DERIVED"
    return "PROFILE_FIXTURE" if setup.get("profileRef") else "ANALYST_FRAME"


def _matches(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return True
    def normalize(value: str) -> str:
        return " ".join(value.casefold().replace("-", " ").split())

    return normalize(left) == normalize(right)


def validate_semantics(
    module_id: str,
    output: Any,
    *,
    module_input: Any | None = None,
    setup: dict[str, Any] | None = None,
) -> None:
    """Keep transport/execution success distinct from scientific result semantics."""

    if not isinstance(output, dict):
        raise ContractError(f"{module_id} output must be a JSON object")
    if module_id == "evidence_mapper" and output.get("status") not in {"ok", "partial", "empty"}:
        raise ContractError(f"evidence mapper payload status is {output.get('status')!r}")
    if module_id == "roi_calculator" and output.get("status") != "ok":
        raise ContractError(f"ROI transport status is {output.get('status')!r}")
    frame = setup.get("programFrame") if isinstance(setup, dict) else None
    identity = frame.get("identity") if isinstance(frame, dict) else None
    if not isinstance(identity, dict) or not isinstance(module_input, dict):
        return
    if module_id == "hypothesis_generator":
        provenance = output.get("provenance")
        graph_id = provenance.get("graph_id") if isinstance(provenance, dict) else None
        if graph_id is not None and not _matches(graph_id, module_input.get("graph_id")):
            raise ContractError(
                "OUTPUT_IDENTITY_MISMATCH: hypothesis graph_id does not match input"
            )
    elif module_id == "clinical_simulation":
        echoed = output.get("input")
        if isinstance(echoed, dict):
            for path in (("target", "symbol"), ("disease", "name")):
                expected = module_input.get(path[0], {}).get(path[1])
                actual = echoed.get(path[0], {}).get(path[1])
                if not _matches(expected, actual):
                    raise ContractError(
                        f"OUTPUT_IDENTITY_MISMATCH: clinical echo {'.'.join(path)} differs"
                    )
    elif module_id == "roi_calculator":
        request_id = output.get("request_id")
        if request_id is not None and not _matches(request_id, module_input.get("request_id")):
            raise ContractError("OUTPUT_IDENTITY_MISMATCH: ROI request_id does not match input")
        summary = output.get("payload", {}).get("summary", {})
        actual = summary.get("program_id") if isinstance(summary, dict) else None
        expected = module_input.get("program", {}).get("program_id")
        if actual is not None and not _matches(actual, expected):
            raise ContractError("OUTPUT_IDENTITY_MISMATCH: ROI program_id does not match input")
    elif module_id == "simulation":
        echoed = output.get("input") if isinstance(output.get("input"), dict) else {}
        target = output.get("target") if isinstance(output.get("target"), dict) else {}
        actual = target.get("uniprot_accession") or echoed.get("uniprot_accession")
        if actual is not None and not _matches(actual, module_input.get("uniprot_accession")):
            raise ContractError(
                "OUTPUT_IDENTITY_MISMATCH: simulation accession does not match input"
            )


def hypothesis_craziness(setup: dict[str, Any]) -> float:
    low, high = setup["hypothesisRange"]
    midpoint = (low + high) / 2
    return (midpoint - 1) / 9
