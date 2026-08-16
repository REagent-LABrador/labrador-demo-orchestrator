"""RFC 8785 packets and subprocess invocation for scientific Highlander runs."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rfc8785

from .contracts import ContractError, dump_json_atomic, load_json, resolve_within

REQUEST_SCHEMA_VERSION = "highlander.packet-comparison-request.v1"
RESULT_SCHEMA_VERSION = "highlander.portfolio-result.v1"
INPUT_BINDING_SCHEMA_VERSION = "labrador.module-input-binding.v1"

MODULE_ORDER = (
    "evidence_mapper",
    "hypothesis_generator",
    "clinical_simulation",
    "roi_calculator",
    "simulation",
)

DEPENDENCIES = {
    "evidence_mapper": (),
    "hypothesis_generator": ("evidence_mapper",),
    "clinical_simulation": ("hypothesis_generator",),
    # The current ROI request is emitted by HypGen and is not overlaid with the
    # clinical result. Keep clinical visible, but do not claim false ROI lineage.
    "roi_calculator": ("hypothesis_generator",),
    # Tractability executes from the explicit program frame. It is useful even
    # when HypGen cannot complete, so it does not claim a HypGen dependency.
    "simulation": (),
}


@dataclass(frozen=True)
class HighlanderSpec:
    repository: str
    commit: str
    module_root: Path
    command: tuple[str, ...]
    timeout_seconds: float
    adapter_version: str
    producer_contracts: dict[str, dict[str, str]]
    objective_policy: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> HighlanderSpec:
        lock = load_json(root / "module-lock.json")
        raw = lock.get("portfolio_consumer") if isinstance(lock, dict) else None
        if not isinstance(raw, dict):
            raise ContractError("module-lock portfolio_consumer must be an object")
        required = {
            "repository",
            "commit",
            "module_root",
            "command",
            "timeout_seconds",
            "adapter_version",
            "producer_contracts",
            "objective_policy",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise ContractError(
                "portfolio_consumer missing fields: " + ", ".join(missing)
            )
        command = raw["command"]
        if not isinstance(command, list) or not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise ContractError("portfolio_consumer.command must be an argument array")
        timeout = raw["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ContractError("portfolio_consumer.timeout_seconds must be positive")
        if not isinstance(raw["adapter_version"], str) or not raw["adapter_version"]:
            raise ContractError("portfolio_consumer.adapter_version must be non-empty")
        contracts = raw["producer_contracts"]
        if not isinstance(contracts, dict) or set(contracts) != set(MODULE_ORDER):
            raise ContractError(
                "portfolio_consumer.producer_contracts must name all five modules"
            )
        normalized_contracts: dict[str, dict[str, str]] = {}
        for module_id, contract in contracts.items():
            if not isinstance(contract, dict):
                raise ContractError(f"producer contract {module_id} must be an object")
            fields = ("module_id", "native_schema_id", "native_schema_version")
            if any(
                not isinstance(contract.get(field), str) or not contract[field]
                for field in fields
            ):
                raise ContractError(f"producer contract {module_id} is incomplete")
            normalized_contracts[module_id] = {
                field: contract[field] for field in fields
            }
        policy = raw["objective_policy"]
        if not isinstance(policy, dict):
            raise ContractError("portfolio_consumer.objective_policy must be an object")
        module_root = resolve_within(
            root,
            root / str(raw["module_root"]),
            label="portfolio consumer module root",
        )
        return cls(
            repository=str(raw["repository"]),
            commit=str(raw["commit"]),
            module_root=module_root,
            command=tuple(command),
            timeout_seconds=float(timeout),
            adapter_version=str(raw["adapter_version"]),
            producer_contracts=normalized_contracts,
            objective_policy=copy.deepcopy(policy),
        )

    def expand_command(self, *, request_path: Path, output_path: Path) -> list[str]:
        values = {
            "module_root": str(self.module_root),
            "request": str(request_path.resolve()),
            "output": str(output_path.resolve()),
        }
        return [part.format(**values) for part in self.command]


@dataclass(frozen=True)
class PacketBuild:
    request: dict[str, Any]
    candidate_hashes: tuple[str, ...]


@dataclass(frozen=True)
class HighlanderArtifacts:
    request_ref: str
    request_raw_sha256: str
    request_canonical_sha256: str
    result_ref: str
    result_raw_sha256: str
    result_canonical_sha256: str
    execution_ref: str
    result: dict[str, Any]
    candidate_hashes: tuple[str, ...]


class HighlanderInvocationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785 JCS bytes, rejecting values outside I-JSON."""

    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, OverflowError, TypeError, ValueError) as exc:
        raise ContractError(f"value is not valid RFC 8785 JSON: {exc}") from exc


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_segment(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._~-]+", "-", value).strip("-.")
    return result[:80] or "candidate"


def canonical_hypothesis_id(branch: dict[str, Any]) -> str:
    node = branch.get("nodes", {}).get("hypothesis_generator", {})
    output = node.get("output") if isinstance(node, dict) else None
    document = output.get("hypothesis") if isinstance(output, dict) else None
    hypothesis = document.get("hypothesis") if isinstance(document, dict) else None
    for value in (
        hypothesis.get("id") if isinstance(hypothesis, dict) else None,
        document.get("id") if isinstance(document, dict) else None,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    branch_id = branch.get("branch_id")
    if isinstance(branch_id, str) and branch_id.strip():
        return branch_id.strip()
    raise ContractError("scientific branch has no stable hypothesis identity")


def _artifact_path(root: Path, ref: Any, *, label: str) -> Path:
    if not isinstance(ref, str) or not ref:
        raise ContractError(f"{label} is missing")
    path = resolve_within(root / "runs", root / "runs" / ref, label=label)
    if not path.is_file():
        raise ContractError(f"{label} does not exist at {path}")
    return path


def _strict_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    canonical_json_bytes(value)
    return value


def _evidence_node(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    stage = next(
        (
            item
            for item in manifest.get("stages", [])
            if item.get("module_id") == "evidence_mapper"
        ),
        None,
    )
    if not isinstance(stage, dict):
        raise ContractError("scientific manifest has no evidence mapper stage")
    stage_ref = stage.get("output_ref")
    if not isinstance(stage_ref, str) or not stage_ref:
        raise ContractError("evidence index is missing")
    index_path = _artifact_path(
        root,
        f"{manifest['run_id']}/{stage_ref}",
        label="evidence index",
    )
    index = load_json(index_path)
    nodes = index.get("nodes") if isinstance(index, dict) else None
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
        raise ContractError("evidence branch index must contain exactly one node")
    return nodes[0]


def _basis(node: dict[str, Any], module_id: str, *, partial: bool) -> str:
    if node.get("status") != "COMPLETE" and not partial:
        return "NOT_RUN"
    return {
        "evidence_mapper": "OBSERVED",
        "hypothesis_generator": "INFERRED",
        "clinical_simulation": "MODELED",
        "roi_calculator": "MODELED",
        "simulation": "MODELED",
    }[module_id]


def _qualifiers(node: dict[str, Any]) -> list[str]:
    origin = node.get("output_origin")
    output = node.get("output") if isinstance(node.get("output"), dict) else {}
    if origin in {None, "NOT_RUN"} and isinstance(output.get("output_origin"), str):
        origin = output["output_origin"]
    values = [str(origin or "UNKNOWN").upper()]
    if node.get("status") != "COMPLETE":
        values.append("CANNOT_COMPLETE")
    reason_code = node.get("reason_code")
    if isinstance(reason_code, str) and reason_code:
        values.append(reason_code.upper())
    return list(dict.fromkeys(values))


def _native_input_identity(
    module_id: str,
    native_input: dict[str, Any],
    hypothesis_id: str,
    dependencies: list[dict[str, Any]],
) -> dict[str, Any]:
    identity: dict[str, Any] = {"hypothesisId": hypothesis_id}
    if module_id == "clinical_simulation":
        thesis = native_input.get("thesis") if "thesis" in native_input else native_input
        thesis_id = thesis.get("id") if isinstance(thesis, dict) else None
        identity["thesisId"] = thesis_id
    elif module_id == "roi_calculator":
        program = native_input.get("program")
        if not isinstance(program, dict):
            program = native_input.get("cashflow_inputs")
        identity["programId"] = program.get("program_id") if isinstance(program, dict) else None
        recruitment = next(
            (
                item
                for item in dependencies
                if item["moduleId"] == "trial-recruitment-forecaster"
            ),
            None,
        )
        if recruitment is not None:
            identity["recruitmentOutputCanonicalSha256"] = recruitment[
                "outputCanonicalSha256"
            ]
    elif module_id == "simulation":
        identity["uniprotAccession"] = native_input.get("uniprot_accession")
    return identity


def _subject(
    manifest: dict[str, Any],
    branch: dict[str, Any],
    module_id: str,
    output: dict[str, Any] | None,
    evidence_subject: dict[str, Any],
) -> dict[str, Any]:
    frame = manifest["setup"]["programFrame"]["scientificFrame"]
    graph = output if module_id == "evidence_mapper" else None
    if graph is None:
        graph_id = evidence_subject.get("graphId")
        graph_round = evidence_subject.get("graphRound")
    else:
        graph_id = graph.get("graph_id")
        graph_round = graph.get("round")
    result = {
        "graphId": graph_id,
        "graphRound": graph_round,
        "thingId": branch.get("focus", {}).get("thing_id"),
        "targetSymbol": frame["target"].get("symbol"),
        "uniprotAccession": frame["target"].get("uniprotAccession"),
        "mechanismHypothesis": frame["simulationContext"].get("mechanismHypothesis"),
        "asOf": frame["simulationContext"].get("asOfDate"),
        "modality": frame["asset"].get("modality"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _module_packet(
    *,
    root: Path,
    manifest: dict[str, Any],
    branch: dict[str, Any],
    hypothesis_id: str,
    internal_module_id: str,
    node: dict[str, Any],
    contract: dict[str, str],
    dependencies: list[dict[str, Any]],
    artifact_payloads: dict[str, str],
    evidence_subject: dict[str, Any],
    adapter_version: str,
) -> dict[str, Any]:
    branch_segment = _safe_segment(str(branch["branch_id"]))
    external_module_id = contract["module_id"]
    attempt_id = "attempt-1"
    prefix = (
        f"artifact://{_safe_segment(manifest['run_id'])}/{branch_segment}/"
        f"{_safe_segment(external_module_id)}/{attempt_id}"
    )

    input_path = _artifact_path(root, node.get("input_ref"), label=f"{internal_module_id} input")
    native_input_raw = input_path.read_bytes()
    native_input = _strict_object(native_input_raw, label=f"{internal_module_id} input")
    binding = {
        "schemaVersion": INPUT_BINDING_SCHEMA_VERSION,
        "runId": manifest["run_id"],
        "hypothesisId": hypothesis_id,
        "moduleId": external_module_id,
        "attemptId": attempt_id,
        "dependsOn": dependencies,
        "inputIdentity": _native_input_identity(
            internal_module_id, native_input, hypothesis_id, dependencies
        ),
        "nativeInput": native_input,
    }
    input_raw = canonical_json_bytes(binding)
    input_ref = prefix + "/input"
    artifact_payloads[input_ref] = base64.b64encode(input_raw).decode("ascii")

    output_path = _artifact_path(
        root, node.get("output_ref"), label=f"{internal_module_id} output"
    )
    output_raw = output_path.read_bytes()
    payload = _strict_object(output_raw, label=f"{internal_module_id} output")

    complete = node.get("status") == "COMPLETE"
    partial = False
    if internal_module_id == "hypothesis_generator" and not complete:
        document = payload.get("hypothesis")
        hypothesis = document.get("hypothesis") if isinstance(document, dict) else None
        partial = isinstance(hypothesis, dict) and isinstance(hypothesis.get("id"), str)
    reason_code = node.get("reason_code")
    message = node.get("message")
    execution_reason = None
    if not complete:
        parts = [str(value) for value in (reason_code, message) if isinstance(value, str) and value]
        detail = ": ".join(parts) or "module returned CANNOT_COMPLETE"
        execution_reason = f"CANNOT_COMPLETE: {detail}"

    output_ref = prefix + "/output" if complete or partial else None
    execution_ref = prefix + "/execution" if not complete and not partial else None
    if output_ref is not None:
        artifact_payloads[output_ref] = base64.b64encode(output_raw).decode("ascii")
    if execution_ref is not None:
        artifact_payloads[execution_ref] = base64.b64encode(output_raw).decode("ascii")
    packet_payload = payload if complete or partial else None

    producer = node.get("producer") if isinstance(node.get("producer"), dict) else {}
    packet = {
        "runId": manifest["run_id"],
        "hypothesisId": hypothesis_id,
        "moduleId": external_module_id,
        "attemptId": attempt_id,
        "nativeSchemaId": contract["native_schema_id"],
        "nativeSchemaVersion": contract["native_schema_version"],
        "producerCodeVersion": str(producer.get("git_sha") or "unknown"),
        "adapterVersion": adapter_version,
        "executionStatus": "COMPLETE" if complete else "PARTIAL" if partial else "FAILED",
        "executionReason": execution_reason,
        "evidenceBasis": _basis(node, internal_module_id, partial=partial),
        "inputRawSha256": raw_sha256(input_raw),
        "outputRawSha256": raw_sha256(output_raw) if output_ref is not None else None,
        "outputCanonicalSha256": (
            canonical_json_sha256(payload) if output_ref is not None else None
        ),
        "inputArtifactRef": input_ref,
        "outputArtifactRef": output_ref,
        "executionArtifactRef": execution_ref,
        "executionArtifactRawSha256": (
            raw_sha256(output_raw) if execution_ref is not None else None
        ),
        "dependsOn": dependencies,
        "subject": _subject(
            manifest,
            branch,
            internal_module_id,
            payload,
            evidence_subject,
        ),
        "qualifiers": _qualifiers(node),
        "payload": packet_payload,
    }
    packet["envelopeCanonicalSha256"] = canonical_json_sha256(packet)
    return packet


def _dependency(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "moduleId": packet["moduleId"],
        "outputCanonicalSha256": packet["outputCanonicalSha256"],
        "envelopeCanonicalSha256": packet["envelopeCanonicalSha256"],
    }


def build_scientific_comparison_request(
    root: Path,
    manifest: dict[str, Any],
    spec: HighlanderSpec,
    *,
    created_at: str,
) -> PacketBuild:
    """Build one immutable candidate packet per scientific focus branch."""

    if not manifest.get("scientific", {}).get("enabled"):
        raise ContractError("scientific Highlander packets require a v3 run")
    branches = manifest.get("scientific", {}).get("branches")
    if not isinstance(branches, list) or not branches:
        raise ContractError("scientific run has no focus branches")
    evidence_node = _evidence_node(root, manifest)
    evidence_output_path = _artifact_path(
        root, evidence_node.get("output_ref"), label="evidence output"
    )
    evidence_output = _strict_object(evidence_output_path.read_bytes(), label="evidence output")
    evidence_subject = {
        "graphId": evidence_output.get("graph_id"),
        "graphRound": evidence_output.get("round"),
    }
    evidence_subject = {
        key: value for key, value in evidence_subject.items() if value is not None
    }
    artifact_payloads: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    candidate_hashes: list[str] = []
    hypothesis_branches: dict[str, str] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            raise ContractError("scientific branch must be an object")
        hypothesis_id = canonical_hypothesis_id(branch)
        previous_branch = hypothesis_branches.get(hypothesis_id)
        if previous_branch is not None:
            raise ContractError(
                f"duplicate canonical hypothesis id {hypothesis_id!r} from branches "
                f"{previous_branch!r} and {branch.get('branch_id')!r}; "
                "the producer must emit one focus-unique canonical id per invocation"
            )
        hypothesis_branches[hypothesis_id] = str(branch.get("branch_id"))
        selected: dict[str, dict[str, Any]] = {}
        module_packets: list[dict[str, Any]] = []
        nodes = branch.get("nodes")
        if not isinstance(nodes, dict):
            raise ContractError(f"branch {branch.get('branch_id')} has no nodes")
        for internal_module_id in MODULE_ORDER:
            node = evidence_node if internal_module_id == "evidence_mapper" else nodes.get(
                internal_module_id
            )
            if not isinstance(node, dict):
                raise ContractError(
                    f"branch {branch.get('branch_id')} lacks terminal {internal_module_id}"
                )
            dependencies = [
                _dependency(selected[parent])
                for parent in DEPENDENCIES[internal_module_id]
            ]
            packet = _module_packet(
                root=root,
                manifest=manifest,
                branch=branch,
                hypothesis_id=hypothesis_id,
                internal_module_id=internal_module_id,
                node=node,
                contract=spec.producer_contracts[internal_module_id],
                dependencies=dependencies,
                artifact_payloads=artifact_payloads,
                evidence_subject=evidence_subject,
                adapter_version=spec.adapter_version,
            )
            selected[internal_module_id] = packet
            module_packets.append(packet)

        exclusion_reasons = sorted(
            {
                f"{node['module_id']}:{node.get('reason_code') or 'CANNOT_COMPLETE'}"
                for node in nodes.values()
                if isinstance(node, dict) and node.get("status") != "COMPLETE"
            }
        )
        packet_revision_id = (
            f"{manifest['run_id']}-{_safe_segment(str(branch['branch_id']))}-"
            f"terminal-r{manifest['revision']}"
        )
        body = {
            "packetRevisionId": packet_revision_id,
            "runId": manifest["run_id"],
            "hypothesisId": hypothesis_id,
            "modulePackets": sorted(module_packets, key=lambda item: item["moduleId"]),
            "exclusionReasons": exclusion_reasons,
        }
        packet_hash = canonical_json_sha256(body)
        candidates.append({"packetHash": packet_hash, **body})
        candidate_hashes.append(packet_hash)
    request = {
        "schemaVersion": REQUEST_SCHEMA_VERSION,
        "snapshotId": f"{manifest['run_id']}-terminal-r{manifest['revision']}",
        "createdAt": created_at,
        "objectivePolicy": copy.deepcopy(spec.objective_policy),
        "candidatePackets": candidates,
        "artifactPayloads": {
            ref: artifact_payloads[ref] for ref in sorted(artifact_payloads)
        },
    }
    canonical_json_bytes(request)
    return PacketBuild(request=request, candidate_hashes=tuple(candidate_hashes))


def invoke_highlander(
    root: Path,
    run_id: str,
    manifest: dict[str, Any],
    spec: HighlanderSpec,
    *,
    created_at: str,
) -> HighlanderArtifacts:
    """Persist, invoke, and validate the pinned server-side Highlander CLI."""

    built = build_scientific_comparison_request(
        root, manifest, spec, created_at=created_at
    )
    run_dir = resolve_within(root / "runs", root / "runs" / run_id, label="run directory")
    highlander_dir = run_dir / "highlander"
    request_path = highlander_dir / "request.json"
    result_path = highlander_dir / "result.json"
    execution_path = highlander_dir / "execution.json"
    dump_json_atomic(request_path, built.request)
    command = spec.expand_command(request_path=request_path, output_path=result_path)
    executable = command[0]
    available = Path(executable).is_file() if "/" in executable else shutil.which(executable)
    if not available:
        raise HighlanderInvocationError(
            "HIGHLANDER_RUNTIME_UNAVAILABLE",
            f"Highlander runtime {Path(executable).name} is unavailable",
        )
    try:
        completed = subprocess.run(
            command,
            cwd=spec.module_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=spec.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        dump_json_atomic(
            execution_path,
            {
                "command": command,
                "exit_code": None,
                "timed_out": True,
                "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
                "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
            },
        )
        raise HighlanderInvocationError(
            "HIGHLANDER_TIMEOUT",
            f"Highlander exceeded {spec.timeout_seconds:g} seconds",
        ) from exc
    dump_json_atomic(
        execution_path,
        {
            "command": command,
            "exit_code": completed.returncode,
            "timed_out": False,
            "stdout": completed.stdout[-20_000:],
            "stderr": completed.stderr[-20_000:],
        },
    )
    if completed.returncode != 0:
        reason = completed.stderr.strip() or f"Highlander exited {completed.returncode}"
        raise HighlanderInvocationError("HIGHLANDER_FAILED", reason)
    if not result_path.is_file():
        raise HighlanderInvocationError(
            "HIGHLANDER_OUTPUT_MISSING", "Highlander did not write its result artifact"
        )
    result_raw = result_path.read_bytes()
    try:
        result = _strict_object(result_raw, label="Highlander result")
    except ContractError as exc:
        raise HighlanderInvocationError("HIGHLANDER_OUTPUT_INVALID", str(exc)) from exc
    if result.get("schemaVersion") != RESULT_SCHEMA_VERSION:
        raise HighlanderInvocationError(
            "HIGHLANDER_OUTPUT_INVALID",
            f"Highlander result must use {RESULT_SCHEMA_VERSION}",
        )
    if "nextEvidenceAction" not in result:
        raise HighlanderInvocationError(
            "HIGHLANDER_OUTPUT_INVALID",
            "Highlander result must contain nextEvidenceAction",
        )
    request_raw = request_path.read_bytes()
    return HighlanderArtifacts(
        request_ref=str(request_path.relative_to(run_dir)),
        request_raw_sha256=raw_sha256(request_raw),
        request_canonical_sha256=canonical_json_sha256(built.request),
        result_ref=str(result_path.relative_to(run_dir)),
        result_raw_sha256=raw_sha256(result_raw),
        result_canonical_sha256=canonical_json_sha256(result),
        execution_ref=str(execution_path.relative_to(run_dir)),
        result=result,
        candidate_hashes=built.candidate_hashes,
    )
