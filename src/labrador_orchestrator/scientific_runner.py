"""Explicit multi-branch scientific execution with no live-to-fixture fallback."""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .contracts import (
    ContractError,
    dump_json_atomic,
    load_json,
    sha256_file,
    validate_json,
)
from .registry import ModuleRegistry, ModuleSpec
from .store import RunStore, utc_now

TERMINAL_NODE_STATUSES = {"COMPLETE", "CANNOT_COMPLETE"}


def _top_stage(manifest: dict[str, Any], module_id: str) -> dict[str, Any]:
    for stage in manifest["stages"]:
        if stage["module_id"] == module_id:
            return stage
    raise ContractError(f"manifest has no stage for {module_id}")


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned[:60] or "focus"


def _matches_focus(record: dict[str, Any], focus_id: str) -> bool:
    return record.get("from") == focus_id or record.get("to") == focus_id


def select_focus_nodes(graph: dict[str, Any], *, maximum: int) -> list[dict[str, Any]]:
    """Select real graph biomarker nodes, then evidence-supported process nodes."""

    things = graph.get("things")
    findings = graph.get("findings")
    links = graph.get("links")
    if not all(isinstance(value, list) for value in (things, findings, links)):
        raise ContractError("evidence graph must contain things, findings, and links arrays")

    result: list[dict[str, Any]] = []
    for kind in ("biomarker", "process"):
        candidates: list[dict[str, Any]] = []
        for thing in things:
            if not isinstance(thing, dict) or thing.get("kind") != kind:
                continue
            thing_id = thing.get("id")
            name = thing.get("name")
            if not isinstance(thing_id, str) or not isinstance(name, str) or not name.strip():
                continue
            incident_findings = [
                item
                for item in findings
                if isinstance(item, dict) and _matches_focus(item, thing_id)
            ]
            supporting = [item for item in incident_findings if item.get("says") == "yes"]
            incident_links = [
                item
                for item in links
                if isinstance(item, dict) and _matches_focus(item, thing_id)
            ]
            # A process becomes a branch only when the mapper supplied positive evidence.
            if kind == "process" and not supporting:
                continue
            candidates.append(
                {
                    "thing_id": thing_id,
                    "name": name.strip(),
                    "kind": kind,
                    "display_label": (
                        name.strip()
                        if kind == "biomarker"
                        else f"Mechanistic/PD readout: {name.strip()}"
                    ),
                    "finding_ids": sorted(
                        item["id"]
                        for item in incident_findings
                        if isinstance(item.get("id"), str)
                    ),
                    "link_ids": sorted(
                        item["id"]
                        for item in incident_links
                        if isinstance(item.get("id"), str)
                    ),
                    "support_count": len(supporting),
                    "evidence_count": len(incident_findings),
                    "mentions": int(thing.get("mentions") or 0),
                }
            )
        candidates.sort(
            key=lambda item: (
                -item["support_count"],
                -item["evidence_count"],
                -item["mentions"],
                item["thing_id"],
            )
        )
        result.extend(candidates)
        if len(result) >= maximum:
            break
    return result[:maximum]


def _hypothesis_claim(document: dict[str, Any]) -> str:
    hypothesis = document.get("hypothesis")
    hypothesis = hypothesis if isinstance(hypothesis, dict) else {}
    articulation = hypothesis.get("articulation")
    articulation = articulation if isinstance(articulation, dict) else {}
    for field in ("mechanism", "statement"):
        value = articulation.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    subject = hypothesis.get("subject_name")
    object_name = hypothesis.get("object_name")
    if isinstance(subject, str) and isinstance(object_name, str):
        return f"{subject} is hypothesized to affect {object_name}."
    raise ContractError("canonical hypothesis has no mechanism or statement")


def _source_for_finding(
    finding: dict[str, Any], papers_by_id: dict[str, dict[str, Any]]
) -> str | None:
    paper_id = finding.get("paper")
    paper = papers_by_id.get(paper_id) if isinstance(paper_id, str) else None
    if isinstance(paper, dict):
        for field, prefix in (("pmid", "PMID:"), ("doi", "DOI:"), ("id", "PAPER:")):
            value = paper.get(field)
            if isinstance(value, str) and value.strip():
                return prefix + value.strip()
    if isinstance(paper_id, str) and paper_id.strip():
        return "PAPER:" + paper_id.strip()
    return None


def assemble_clinical_thesis(
    *,
    graph: dict[str, Any],
    focus: dict[str, Any],
    hypothesis_document: dict[str, Any],
    frame: dict[str, Any],
    branch_id: str,
) -> dict[str, Any]:
    """Assemble only branch evidence/hypothesis fields plus explicit setup values."""

    papers = graph.get("papers") if isinstance(graph.get("papers"), list) else []
    papers_by_id = {
        item["id"]: item
        for item in papers
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    findings = graph.get("findings") if isinstance(graph.get("findings"), list) else []
    evidence: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict) or not _matches_focus(finding, focus["thing_id"]):
            continue
        source = _source_for_finding(finding, papers_by_id)
        claim = finding.get("claim")
        if source is None or not isinstance(claim, str) or not claim.strip():
            continue
        says = finding.get("says")
        direction = {"yes": "supports", "no": "contradicts", "no_effect": "no_effect"}.get(
            says, "no_effect"
        )
        confidence = finding.get("confidence")
        strength = (
            max(0.0, min(1.0, float(confidence)))
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            else 0.0
        )
        evidence.append(
            {
                "claim": claim.strip(),
                "direction": direction,
                "source": source,
                "source_type": "publication",
                "strength": strength,
            }
        )

    asset = copy.deepcopy(frame["asset"])
    target = {
        "symbol": frame["target"]["symbol"],
        "direction": frame["target"]["direction"],
        "uniprot_accession": frame["target"]["uniprotAccession"],
    }
    thesis: dict[str, Any] = {
        "id": branch_id + "-clinical",
        "asset": asset,
        "target": target,
        "disease": copy.deepcopy(frame["disease"]),
        "biomarker_population": {
            "marker": focus["display_label"],
            "prevalence_in_disease": frame["biomarkerDefaults"]["prevalenceInDisease"],
            "assay_available": frame["biomarkerDefaults"]["assayAvailable"],
        },
        "endpoint": {
            "name": frame["endpoint"]["name"],
            "type": frame["endpoint"]["type"],
            "expected_effect_size": frame["endpoint"]["expectedEffectSize"],
        },
        "mechanism": _hypothesis_claim(hypothesis_document),
        "mechanism_hypothesis": frame["simulationContext"]["mechanismHypothesis"],
        "tissue": frame["tissue"],
        "evidence": evidence,
        "uncertainty": None,
        "as_of_date": frame["simulationContext"]["asOfDate"],
    }
    return thesis


def build_simulation_input(frame: dict[str, Any]) -> dict[str, Any]:
    accession = frame["target"]["uniprotAccession"]
    if not isinstance(accession, str) or not accession.strip():
        raise ContractError("tractability requires program.frame.target.uniprotAccession")
    return {
        "uniprot_accession": accession,
        "as_of_date": frame["simulationContext"]["asOfDate"],
        "disease_context": frame["disease"]["name"],
        "interaction_to_disrupt": frame["simulationContext"]["interactionToDisrupt"],
        "mechanism_hypothesis": frame["simulationContext"]["mechanismHypothesis"],
    }


class ScientificBranchRunner:
    """Run evidence once, then independent per-focus producer branches."""

    def __init__(self, root: Path, registry: ModuleRegistry, store: RunStore):
        self.root = root.resolve()
        self.registry = registry
        self.store = store

    def _start_run(self, run_id: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            if manifest["run_status"] != "CREATED":
                raise ContractError(f"run {run_id} cannot start from {manifest['run_status']}")
            manifest["run_status"] = "RUNNING"

        self.store.mutate(run_id, "SCIENTIFIC_RUN_STARTED", update)

    def _start_stage(self, run_id: str, module_id: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _top_stage(manifest, module_id)
            if stage["status"] != "QUEUED":
                return
            stage.update(
                {
                    "status": "RUNNING",
                    "execution_status": "RUNNING",
                    "started_at": utc_now(),
                    "note": "scientific branch execution running",
                }
            )
            manifest["current_stage"] = stage["id"]

        self.store.mutate(run_id, "SCIENTIFIC_STAGE_STARTED", update, {"module": module_id})

    def _terminal_stage(
        self,
        run_id: str,
        module_id: str,
        *,
        nodes: list[dict[str, Any]],
        index_path: Path | None,
    ) -> None:
        complete = [node for node in nodes if node.get("status") == "COMPLETE"]
        failed = [node for node in nodes if node.get("status") == "CANNOT_COMPLETE"]
        status = "COMPLETE" if nodes and not failed else "COMPLETE_WITH_WARNINGS"
        execution_status = "COMPLETE" if complete else "FAILED"
        if not nodes:
            execution_status = "SKIPPED"
        origins = {node.get("output_origin") for node in complete}
        output_origin = origins.pop() if len(origins) == 1 else "MIXED" if origins else "NOT_RUN"
        reason = failed[0].get("reason_code") if failed and not complete else None

        def update(manifest: dict[str, Any]) -> None:
            stage = _top_stage(manifest, module_id)
            if stage["status"] == "QUEUED":
                stage["status"] = "SKIPPED"
            elif stage["status"] == "RUNNING":
                stage["status"] = status
            stage.update(
                {
                    "execution_status": execution_status,
                    "output_origin": output_origin,
                    "reason_code": reason,
                    "note": (
                        f"{len(complete)} branch node(s) complete; "
                        f"{len(failed)} cannot complete"
                    ),
                    "completed_at": utc_now(),
                    "warnings": [
                        f"{len(failed)} branch node(s) returned CANNOT_COMPLETE."
                    ]
                    if failed
                    else [],
                    "output_ref": (
                        str(index_path.relative_to(self.store.run_dir(run_id)))
                        if index_path is not None
                        else None
                    ),
                    "output_hash": sha256_file(index_path) if index_path is not None else None,
                }
            )

        self.store.mutate(
            run_id,
            "SCIENTIFIC_STAGE_TERMINAL",
            update,
            {"module": module_id, "complete": len(complete), "failed": len(failed)},
        )

    def _cannot_complete(
        self, output_path: Path, *, reason_code: str, message: str
    ) -> dict[str, Any]:
        value = {
            "status": "CANNOT_COMPLETE",
            "reason_code": reason_code,
            "message": message,
        }
        dump_json_atomic(output_path, value)
        return value

    def _invoke(
        self,
        module: ModuleSpec,
        *,
        input_value: dict[str, Any],
        node_dir: Path,
        execution_mode: str,
    ) -> dict[str, Any]:
        node_dir.mkdir(parents=True, exist_ok=True)
        input_path = node_dir / "input.json"
        output_path = node_dir / "output.json"
        execution_path = node_dir / "execution.json"
        dump_json_atomic(input_path, input_value)
        input_schema_path = module.scientific_input_schema or module.input_schema
        output_schema_path = module.scientific_output_schema or module.output_schema
        try:
            validate_json(
                load_json(input_schema_path),
                input_value,
                label=f"{module.module_id} scientific input",
                schema_path=input_schema_path,
            )
        except ContractError as exc:
            output = self._cannot_complete(
                output_path, reason_code="INPUT_INVALID", message=str(exc)
            )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=0,
                execution_mode=execution_mode,
                exit_code=None,
            )

        configured = (
            module.live_command if execution_mode == "LIVE" else module.replay_command
        )
        if configured is None:
            output = self._cannot_complete(
                output_path,
                reason_code=f"{execution_mode}_COMMAND_NOT_CONFIGURED",
                message=(
                    f"{module.module_id} has no explicit {execution_mode.lower()} command "
                    "in the pinned module registry"
                ),
            )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=0,
                execution_mode=execution_mode,
                exit_code=None,
            )

        command = module.expand_command(
            root=self.root,
            input_path=input_path,
            output_path=output_path,
            execution_mode=execution_mode,
        )
        executable = command[0]
        available = Path(executable).exists() if "/" in executable else shutil.which(executable)
        if not available:
            output = self._cannot_complete(
                output_path,
                reason_code="RUNTIME_UNAVAILABLE",
                message=f"runtime {Path(executable).name} is unavailable",
            )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=0,
                execution_mode=execution_mode,
                exit_code=None,
            )

        started = time.monotonic()
        try:
            process = subprocess.run(
                command,
                cwd=module.module_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=module.timeout_seconds,
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            execution = {
                "command": command,
                "exit_code": process.returncode,
                "duration_ms": duration_ms,
                "stdout": process.stdout[-20_000:],
                "stderr": process.stderr[-20_000:],
            }
            dump_json_atomic(execution_path, execution)
            if output_path.exists():
                try:
                    output = load_json(output_path)
                except ContractError as exc:
                    output = self._cannot_complete(
                        output_path, reason_code="INVALID_OUTPUT", message=str(exc)
                    )
            else:
                output = self._cannot_complete(
                    output_path,
                    reason_code="OUTPUT_MISSING" if process.returncode == 0 else "PROCESS_FAILED",
                    message=(
                        "module exited without an output artifact"
                        if process.returncode == 0
                        else (process.stderr.strip() or f"module exited {process.returncode}")
                    ),
                )
            if process.returncode != 0 and output.get("status") != "CANNOT_COMPLETE":
                output = self._cannot_complete(
                    output_path,
                    reason_code="PROCESS_FAILED",
                    message=process.stderr.strip() or f"module exited {process.returncode}",
                )
            if output.get("status") != "CANNOT_COMPLETE":
                try:
                    validate_json(
                        load_json(output_schema_path),
                        output,
                        label=f"{module.module_id} scientific output",
                        schema_path=output_schema_path,
                    )
                except ContractError as exc:
                    invalid_path = node_dir / "output.invalid.json"
                    output_path.replace(invalid_path)
                    output = self._cannot_complete(
                        output_path, reason_code="OUTPUT_INVALID", message=str(exc)
                    )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=duration_ms,
                execution_mode=execution_mode,
                exit_code=process.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            dump_json_atomic(
                execution_path,
                {
                    "command": command,
                    "exit_code": None,
                    "duration_ms": duration_ms,
                    "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                    "timed_out": True,
                },
            )
            output = self._cannot_complete(
                output_path,
                reason_code="MODULE_TIMEOUT",
                message=f"{module.module_id} exceeded {module.timeout_seconds:g} seconds",
            )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=duration_ms,
                execution_mode=execution_mode,
                exit_code=None,
            )
        except OSError as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            output = self._cannot_complete(
                output_path, reason_code="PROCESS_START_FAILED", message=str(exc)
            )
            return self._node_result(
                module,
                input_path=input_path,
                output_path=output_path,
                output=output,
                duration_ms=duration_ms,
                execution_mode=execution_mode,
                exit_code=None,
            )

    def _node_result(
        self,
        module: ModuleSpec,
        *,
        input_path: Path,
        output_path: Path,
        output: dict[str, Any],
        duration_ms: int,
        execution_mode: str,
        exit_code: int | None,
    ) -> dict[str, Any]:
        cannot = output.get("status") == "CANNOT_COMPLETE"
        origin = output.get("output_origin")
        interpretability = output.get("interpretability")
        extensions = (
            interpretability.get("extensions")
            if isinstance(interpretability, dict)
            and isinstance(interpretability.get("extensions"), dict)
            else {}
        )
        if not isinstance(origin, str):
            nested_origin = extensions.get("output_origin")
            origin = nested_origin if isinstance(nested_origin, str) else None
        if not isinstance(origin, str):
            origin = "LIVE" if execution_mode == "LIVE" else "DETERMINISTIC_REPLAY"
        error = output.get("error") if isinstance(output.get("error"), dict) else {}
        reason_code = (
            output.get("reason_code")
            or output.get("reasonCode")
            or error.get("reason_code")
            or error.get("reasonCode")
        )
        message = output.get("message") or error.get("message")
        return {
            "module_id": module.module_id,
            "status": "CANNOT_COMPLETE" if cannot else "COMPLETE",
            "reason_code": reason_code if cannot else None,
            "message": message if cannot else "module output validated",
            "output_origin": "NOT_RUN" if cannot else origin,
            "input_ref": str(input_path.relative_to(self.store.runs_root)),
            "input_hash": sha256_file(input_path),
            "output_ref": str(output_path.relative_to(self.store.runs_root)),
            "output_hash": sha256_file(output_path),
            "duration_ms": duration_ms,
            "exit_code": exit_code,
            "producer": {
                "repository": module.repository,
                "git_sha": module.commit,
            },
            "output": output,
        }

    def _dependency_failure(
        self,
        module: ModuleSpec,
        *,
        node_dir: Path,
        dependency: str,
        execution_mode: str,
    ) -> dict[str, Any]:
        node_dir.mkdir(parents=True, exist_ok=True)
        input_path = node_dir / "input.json"
        output_path = node_dir / "output.json"
        dump_json_atomic(input_path, {"unavailable": True, "dependency": dependency})
        output = self._cannot_complete(
            output_path,
            reason_code="UPSTREAM_FAILED",
            message=f"required upstream node {dependency} did not complete",
        )
        return self._node_result(
            module,
            input_path=input_path,
            output_path=output_path,
            output=output,
            duration_ms=0,
            execution_mode=execution_mode,
            exit_code=None,
        )

    def _persist_branches(self, run_id: str, branches: list[dict[str, Any]]) -> None:
        def update(manifest: dict[str, Any]) -> None:
            manifest["scientific"]["branches"] = copy.deepcopy(branches)

        self.store.mutate(run_id, "SCIENTIFIC_BRANCHES_UPDATED", update)

    def _index_stage(
        self,
        run_id: str,
        module_id: str,
        branches: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], Path | None]:
        nodes = [
            branch["nodes"][module_id]
            for branch in branches
            if module_id in branch.get("nodes", {})
        ]
        if not nodes:
            return nodes, None
        path = self.store.run_dir(run_id) / f"branch-index-{module_id}.json"
        dump_json_atomic(
            path,
            {
                "schema_version": "labrador.branch-node-index.v1",
                "module_id": module_id,
                "nodes": [
                    {key: value for key, value in node.items() if key != "output"}
                    for node in nodes
                ],
            },
        )
        return nodes, path

    def run(self, run_id: str) -> dict[str, Any]:
        manifest = self.store.read(run_id)
        setup = manifest["setup"]
        if setup.get("requestSchemaVersion") != "labrador.run-setup.v3":
            raise ContractError("ScientificBranchRunner requires labrador.run-setup.v3")
        execution_mode = setup["executionMode"]
        frame = setup["programFrame"]["scientificFrame"]
        run_dir = self.store.run_dir(run_id)
        self._start_run(run_id)

        evidence_module = self.registry.by_id("evidence_mapper")
        self._start_stage(run_id, "evidence_mapper")
        evidence_node = self._invoke(
            evidence_module,
            input_value=copy.deepcopy(setup["programFrame"]["evidenceRequest"]),
            node_dir=run_dir / "scientific" / "evidence_mapper",
            execution_mode=execution_mode,
        )
        evidence_index = run_dir / "branch-index-evidence_mapper.json"
        dump_json_atomic(
            evidence_index,
            {
                "schema_version": "labrador.branch-node-index.v1",
                "module_id": "evidence_mapper",
                "nodes": [{key: value for key, value in evidence_node.items() if key != "output"}],
            },
        )
        self._terminal_stage(
            run_id,
            "evidence_mapper",
            nodes=[evidence_node],
            index_path=evidence_index,
        )
        if evidence_node["status"] != "COMPLETE":
            return self._finish_without_branches(run_id, evidence_node)

        graph = evidence_node["output"]
        focuses = select_focus_nodes(graph, maximum=setup["maxFocusBranches"])
        branches: list[dict[str, Any]] = [
            {
                "branch_id": f"BR-{index:02d}-{_safe_segment(focus['thing_id'])}",
                "focus": focus,
                "status": "RUNNING",
                "nodes": {},
            }
            for index, focus in enumerate(focuses, start=1)
        ]
        self._persist_branches(run_id, branches)
        if not branches:
            return self._finish_no_focus(run_id)

        for module_id in (
            "hypothesis_generator",
            "clinical_simulation",
            "simulation",
            "roi_calculator",
        ):
            self._start_stage(run_id, module_id)

        for branch in branches:
            branch_dir = run_dir / "branches" / branch["branch_id"]
            hyp_module = self.registry.by_id("hypothesis_generator")
            hyp_input = {
                "graph": copy.deepcopy(graph),
                "focus_thing_id": branch["focus"]["thing_id"],
                "profile": setup["hypothesisProfile"],
                "valuation_frame": copy.deepcopy(setup["programFrame"]["valuationFrame"]),
                "roi": copy.deepcopy(setup["hypothesisRoi"]),
            }
            request_id = hyp_input["roi"]["request_id"]
            hyp_input["roi"]["request_id"] = f"{request_id}-{branch['focus']['thing_id']}"
            hypothesis_node = self._invoke(
                hyp_module,
                input_value=hyp_input,
                node_dir=branch_dir / "hypothesis_generator",
                execution_mode=execution_mode,
            )
            branch["nodes"]["hypothesis_generator"] = hypothesis_node

            simulation_module = self.registry.by_id("simulation")
            try:
                simulation_input = build_simulation_input(frame)
                simulation_node = self._invoke(
                    simulation_module,
                    input_value=simulation_input,
                    node_dir=branch_dir / "simulation",
                    execution_mode=execution_mode,
                )
            except ContractError as exc:
                simulation_node = self._dependency_failure(
                    simulation_module,
                    node_dir=branch_dir / "simulation",
                    dependency=str(exc),
                    execution_mode=execution_mode,
                )
            branch["nodes"]["simulation"] = simulation_node

            clinical_module = self.registry.by_id("clinical_simulation")
            roi_module = self.registry.by_id("roi_calculator")
            if hypothesis_node["status"] == "COMPLETE":
                hyp_output = hypothesis_node["output"]
                document = hyp_output.get("hypothesis")
                roi_request = hyp_output.get("roi_request")
                if isinstance(document, dict):
                    try:
                        clinical_input = assemble_clinical_thesis(
                            graph=graph,
                            focus=branch["focus"],
                            hypothesis_document=document,
                            frame=frame,
                            branch_id=branch["branch_id"],
                        )
                        clinical_node = self._invoke(
                            clinical_module,
                            input_value=clinical_input,
                            node_dir=branch_dir / "clinical_simulation",
                            execution_mode=execution_mode,
                        )
                    except ContractError as exc:
                        clinical_node = self._dependency_failure(
                            clinical_module,
                            node_dir=branch_dir / "clinical_simulation",
                            dependency=str(exc),
                            execution_mode=execution_mode,
                        )
                else:
                    clinical_node = self._dependency_failure(
                        clinical_module,
                        node_dir=branch_dir / "clinical_simulation",
                        dependency="hypothesis_generator.hypothesis",
                        execution_mode=execution_mode,
                    )
                if isinstance(roi_request, dict):
                    roi_node = self._invoke(
                        roi_module,
                        input_value=roi_request,
                        node_dir=branch_dir / "roi_calculator",
                        execution_mode=execution_mode,
                    )
                else:
                    roi_node = self._dependency_failure(
                        roi_module,
                        node_dir=branch_dir / "roi_calculator",
                        dependency="hypothesis_generator.roi_request",
                        execution_mode=execution_mode,
                    )
            else:
                clinical_node = self._dependency_failure(
                    clinical_module,
                    node_dir=branch_dir / "clinical_simulation",
                    dependency="hypothesis_generator",
                    execution_mode=execution_mode,
                )
                roi_node = self._dependency_failure(
                    roi_module,
                    node_dir=branch_dir / "roi_calculator",
                    dependency="hypothesis_generator",
                    execution_mode=execution_mode,
                )
            branch["nodes"]["clinical_simulation"] = clinical_node
            branch["nodes"]["roi_calculator"] = roi_node
            branch["status"] = (
                "COMPLETE"
                if all(
                    node["status"] == "COMPLETE" for node in branch["nodes"].values()
                )
                else "CANNOT_COMPLETE"
            )
            self._persist_branches(run_id, branches)

        for module_id in (
            "hypothesis_generator",
            "clinical_simulation",
            "roi_calculator",
            "simulation",
        ):
            nodes, index_path = self._index_stage(run_id, module_id, branches)
            self._terminal_stage(
                run_id, module_id, nodes=nodes, index_path=index_path
            )
        return self._finish(run_id, branches)

    def _finish_without_branches(
        self, run_id: str, evidence_node: dict[str, Any]
    ) -> dict[str, Any]:
        for module_id in (
            "hypothesis_generator",
            "clinical_simulation",
            "roi_calculator",
            "simulation",
        ):
            self._terminal_stage(run_id, module_id, nodes=[], index_path=None)

        def update(manifest: dict[str, Any]) -> None:
            manifest["run_status"] = "FAILED"
            manifest["current_stage"] = None
            manifest["completed_at"] = utc_now()
            manifest["errors"].append(
                {
                    "code": evidence_node.get("reason_code") or "EVIDENCE_FAILED",
                    "module": "evidence_mapper",
                    "message": evidence_node.get("message"),
                }
            )
            manifest["highlander"]["ready"] = False

        return self.store.mutate(run_id, "SCIENTIFIC_RUN_FAILED", update)

    def _finish_no_focus(self, run_id: str) -> dict[str, Any]:
        for module_id in (
            "hypothesis_generator",
            "clinical_simulation",
            "roi_calculator",
            "simulation",
        ):
            self._terminal_stage(run_id, module_id, nodes=[], index_path=None)

        def update(manifest: dict[str, Any]) -> None:
            manifest["run_status"] = "COMPLETED_WITH_WARNINGS"
            manifest["current_stage"] = None
            manifest["completed_at"] = utc_now()
            manifest["warnings"].append(
                {
                    "code": "NO_SUPPORTED_FOCUS_NODES",
                    "message": (
                        "The evidence graph contained no biomarker or evidence-supported "
                        "process nodes; no fallback signals were created."
                    ),
                }
            )
            manifest["highlander"]["ready"] = False

        return self.store.mutate(run_id, "SCIENTIFIC_NO_FOCUS", update)

    def _finish(self, run_id: str, branches: list[dict[str, Any]]) -> dict[str, Any]:
        complete = [branch for branch in branches if branch["status"] == "COMPLETE"]
        failed_nodes = [
            (branch["branch_id"], node)
            for branch in branches
            for node in branch["nodes"].values()
            if node["status"] == "CANNOT_COMPLETE"
        ]

        def update(manifest: dict[str, Any]) -> None:
            manifest["current_stage"] = None
            manifest["completed_at"] = utc_now()
            manifest["run_status"] = (
                "COMPLETED" if len(complete) == len(branches) else "COMPLETED_WITH_WARNINGS"
            )
            manifest["highlander"]["ready"] = any(
                branch["nodes"]["hypothesis_generator"]["status"] == "COMPLETE"
                for branch in branches
            )
            manifest["scientific"]["branches"] = copy.deepcopy(branches)
            for branch_id, node in failed_nodes:
                manifest["errors"].append(
                    {
                        "code": node.get("reason_code") or "CANNOT_COMPLETE",
                        "module": node["module_id"],
                        "branch": branch_id,
                        "message": node.get("message"),
                    }
                )

        return self.store.mutate(
            run_id,
            "SCIENTIFIC_RUN_TERMINAL",
            update,
            {"branches": len(branches), "complete": len(complete)},
        )
