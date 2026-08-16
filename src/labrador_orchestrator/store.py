"""Filesystem-backed run state with atomic manifest revisions."""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import ContractError, dump_json_atomic, load_json, sha256_json
from .registry import ModuleRegistry
from .setup_contract import validate_setup as validate_setup

TERMINAL_STAGE_STATUSES = {"COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED", "SKIPPED"}
TERMINAL_RUN_STATUSES = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"}
PROFILE_ONLY_QUALIFIERS = {
    "ANALYST_FIXTURE_INPUT",
    "CACHED_DOSSIER",
    "CACHED_EVIDENCE_INPUT",
    "DERIVED_FROM_CACHED_EVIDENCE",
    "SYNTHETIC",
    "TRUNCATED_SEARCH",
    "VERBATIM_QUOTES",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunStore:
    def __init__(self, root: Path, registry: ModuleRegistry):
        self.root = root.resolve()
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _lock(self, run_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(run_id, threading.RLock())

    def run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("LR-") or not run_id.replace("-", "").isalnum():
            raise ContractError("invalid run id")
        return self.runs_root / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def create(
        self, setup: dict[str, Any], *, idempotency_key_hash: str | None = None
    ) -> dict[str, Any]:
        run_id = (
            "LR-"
            + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            + "-"
            + secrets.token_hex(2).upper()
        )
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=False, exist_ok=False)
        now = utc_now()
        stages = []
        for module in self.registry.modules:
            stage_dir = run_dir / f"{module.order:02d}_{module.module_id}"
            stage_dir.mkdir()
            qualifiers = list(module.qualifiers)
            if not setup.get("profileRef"):
                qualifiers = [item for item in qualifiers if item not in PROFILE_ONLY_QUALIFIERS]
            stages.append(
                {
                    "id": module.ui_stage,
                    "module_id": module.module_id,
                    "order": module.order,
                    "status": "QUEUED",
                    "execution_status": "QUEUED",
                    "output_origin": "NOT_RUN",
                    "result_basis": list(module.result_basis),
                    "runtime_maturity": module.runtime_maturity,
                    "reason_code": None,
                    "note": "queued",
                    "started_at": None,
                    "completed_at": None,
                    "attempts": [],
                    "warnings": [],
                    "qualifiers": qualifiers,
                    "input_ref": None,
                    "input_source": "NOT_AVAILABLE",
                    "output_ref": None,
                    "input_hash": None,
                    "output_hash": None,
                    "module": {
                        "name": module.module_id,
                        "repository": module.repository,
                        "git_sha": module.commit,
                    },
                }
            )
        manifest: dict[str, Any] = {
            "schema_version": "labrador.run-manifest.v2",
            "revision": 1,
            "run_id": run_id,
            "run_status": "CREATED",
            "current_stage": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "setup": setup,
            "configuration_hash": sha256_json(setup),
            "idempotency_key_hash": idempotency_key_hash,
            "stages": stages,
            "highlander": {
                "ready": False,
                "launched": False,
                "job_id": None,
                "packet_snapshot": None,
                "requires_gap_acknowledgement": True,
                "request_ref": None,
                "request_raw_hash": None,
                "request_canonical_hash": None,
                "result_ref": None,
                "result_hash": None,
                "result_raw_hash": None,
                "result_canonical_hash": None,
                "execution_ref": None,
                "last_error": None,
            },
            "scientific": {
                "enabled": setup.get("requestSchemaVersion") == "labrador.run-setup.v3",
                "execution_mode": setup.get("executionMode"),
                "presentation_mode": setup.get("presentationMode"),
                "branches": [],
            },
            "warnings": (
                [
                    {
                        "code": "PROFILE_FIXTURE_ACTIVE",
                        "message": "This run uses the explicit golden.ra-irak4.v1 profile.",
                    }
                ]
                if setup.get("profileRef")
                else [
                    {
                        "code": "ANALYST_FRAME_ACTIVE",
                        "message": (
                            "Custom analyst inputs are validated independently; "
                            "profile fallbacks are disabled."
                        ),
                    }
                ]
            ),
            "errors": [],
        }
        dump_json_atomic(run_dir / "00_program_input.json", setup)
        dump_json_atomic(self.manifest_path(run_id), manifest)
        self._append_event(
            run_id,
            "RUN_CREATED",
            {"configuration_hash": manifest["configuration_hash"]},
        )
        return manifest

    def read(self, run_id: str) -> dict[str, Any]:
        path = self.manifest_path(run_id)
        if not path.exists():
            raise FileNotFoundError(run_id)
        with self._lock(run_id):
            value = load_json(path)
        if not isinstance(value, dict):
            raise ContractError("manifest must be a JSON object")
        return value

    def mutate(
        self,
        run_id: str,
        event: str,
        callback: Callable[[dict[str, Any]], None],
        event_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock(run_id):
            manifest = self.read(run_id)
            previous = deepcopy(manifest)
            immutable_setup = json.dumps(manifest["setup"], sort_keys=True)
            immutable_hash = manifest["configuration_hash"]
            callback(manifest)
            if json.dumps(manifest["setup"], sort_keys=True) != immutable_setup:
                raise ContractError("run setup is immutable")
            if manifest["configuration_hash"] != immutable_hash:
                raise ContractError("configuration hash is immutable")
            self._validate_transition(previous, manifest)
            manifest["revision"] += 1
            manifest["updated_at"] = utc_now()
            dump_json_atomic(self.manifest_path(run_id), manifest)
            self._append_event(run_id, event, event_payload or {})
            return manifest

    def mutate_if(
        self,
        run_id: str,
        event: str,
        callback: Callable[[dict[str, Any]], bool],
        event_payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically apply a conditional mutation without recording a no-op."""

        with self._lock(run_id):
            manifest = self.read(run_id)
            previous = deepcopy(manifest)
            if not callback(manifest):
                return previous, False
            if manifest["setup"] != previous["setup"]:
                raise ContractError("run setup is immutable")
            if manifest["configuration_hash"] != previous["configuration_hash"]:
                raise ContractError("configuration hash is immutable")
            self._validate_transition(previous, manifest)
            manifest["revision"] += 1
            manifest["updated_at"] = utc_now()
            dump_json_atomic(self.manifest_path(run_id), manifest)
            self._append_event(run_id, event, event_payload or {})
            return manifest, True

    def terminalize_incomplete(
        self,
        run_id: str,
        *,
        active_code: str,
        queued_code: str,
        message: str,
    ) -> dict[str, Any]:
        """Fail an interrupted run while leaving no nonterminal stage behind."""

        def update(manifest: dict[str, Any]) -> None:
            now = utc_now()
            for stage in manifest["stages"]:
                if stage["status"] in TERMINAL_STAGE_STATUSES:
                    continue
                was_running = stage["status"] == "RUNNING"
                stage.update(
                    {
                        "status": "FAILED" if was_running else "SKIPPED",
                        "execution_status": "FAILED" if was_running else "SKIPPED",
                        "output_origin": "NOT_RUN",
                        "reason_code": active_code if was_running else queued_code,
                        "note": message,
                        "completed_at": now,
                    }
                )
                if was_running and stage["attempts"]:
                    stage["attempts"][-1].update(
                        {"status": "FAILED", "completed_at": now}
                    )
            manifest["run_status"] = "FAILED"
            manifest["current_stage"] = None
            manifest["completed_at"] = now
            manifest["highlander"]["ready"] = False
            manifest["errors"].append({"code": active_code, "message": message})

        return self.mutate(run_id, active_code, update)

    @staticmethod
    def _validate_transition(previous: dict[str, Any], current: dict[str, Any]) -> None:
        for field in ("schema_version", "run_id", "created_at", "idempotency_key_hash"):
            if current.get(field) != previous.get(field):
                raise ContractError(f"run {field} is immutable")
        old_stages = previous.get("stages", [])
        new_stages = current.get("stages", [])
        if len(old_stages) != len(new_stages):
            raise ContractError("run stage topology is immutable")
        for old, new in zip(old_stages, new_stages, strict=True):
            for field in ("id", "module_id", "order", "module", "result_basis", "runtime_maturity"):
                if new.get(field) != old.get(field):
                    if field == "module":
                        raise ContractError("stage module identity is immutable")
                    raise ContractError(f"stage {field} is immutable")
            old_status = old.get("status")
            new_status = new.get("status")
            if old_status in TERMINAL_STAGE_STATUSES and new_status != old_status:
                raise ContractError("terminal stage status is immutable")
            allowed = {
                "QUEUED": {"QUEUED", "RUNNING", "FAILED", "SKIPPED"},
                "RUNNING": {
                    "RUNNING",
                    "COMPLETE",
                    "COMPLETE_WITH_WARNINGS",
                    "FAILED",
                },
            }
            if old_status in allowed and new_status not in allowed[old_status]:
                raise ContractError(
                    f"invalid stage transition {old_status!r} -> {new_status!r}"
                )
        old_run = previous.get("run_status")
        new_run = current.get("run_status")
        if old_run in TERMINAL_RUN_STATUSES and new_run != old_run:
            raise ContractError("terminal run status is immutable")
        allowed_runs = {
            "CREATED": {"CREATED", "RUNNING", "FAILED"},
            "RUNNING": {"RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"},
        }
        if old_run in allowed_runs and new_run not in allowed_runs[old_run]:
            raise ContractError(f"invalid run transition {old_run!r} -> {new_run!r}")

    def _append_event(self, run_id: str, event: str, payload: dict[str, Any]) -> None:
        path = self.run_dir(run_id) / "events.ndjson"
        line = json.dumps(
            {"at": utc_now(), "event": event, "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
