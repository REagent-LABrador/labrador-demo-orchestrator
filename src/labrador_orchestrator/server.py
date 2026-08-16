"""Dependency-free local HTTP server for the orchestrator and polished mockup."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .contracts import ContractError, dump_json_atomic, loads_json, resolve_within, sha256_json
from .frontend_projection import project_frontend_meta, project_frontend_snapshot
from .highlander_packet import (
    HighlanderInvocationError,
    HighlanderSpec,
    invoke_highlander,
)
from .projection import project_ui_state
from .registry import ModuleRegistry
from .runner import RunCoordinator, SequentialRunner
from .setup_contract import is_frontend_v0_request
from .store import RunStore, utc_now, validate_setup

MAX_REQUEST_BYTES = 1_000_000


class LabradorApplication:
    def __init__(self, root: Path):
        self.root = root.resolve()
        functional_ui = self.root / ".frontend" / "app"
        self.ui_root = (
            functional_ui
            if (functional_ui / "index.html").is_file()
            else self.root / "ui"
        )
        self.registry = ModuleRegistry.load(self.root)
        self.store = RunStore(self.root, self.registry)
        self._terminalize_orphans()
        self.runner = SequentialRunner(self.root, self.registry, self.store)
        self.coordinator = RunCoordinator(self.runner)
        self._idempotency_lock = threading.Lock()
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._highlander_guard = threading.Lock()
        self._highlander_locks: dict[str, threading.Lock] = {}
        self._load_idempotency_index()

    def _highlander_lock(self, run_id: str) -> threading.Lock:
        with self._highlander_guard:
            return self._highlander_locks.setdefault(run_id, threading.Lock())

    def _terminalize_orphans(self) -> None:
        for path in sorted(self.store.runs_root.glob("LR-*/manifest.json")):
            run_id = path.parent.name
            try:
                manifest = self.store.read(run_id)
                if manifest.get("run_status") not in {"CREATED", "RUNNING"}:
                    continue
                self.store.terminalize_incomplete(
                    run_id,
                    active_code="ORCHESTRATOR_RESTARTED",
                    queued_code="ORCHESTRATOR_RESTARTED",
                    message="local orchestrator restarted before this run completed",
                )
            except (ContractError, FileNotFoundError):
                continue

    def _load_idempotency_index(self) -> None:
        for path in sorted(self.store.runs_root.glob("LR-*/manifest.json")):
            try:
                manifest = self.store.read(path.parent.name)
            except (ContractError, FileNotFoundError):
                continue
            key_hash = manifest.get("idempotency_key_hash")
            configuration_hash = manifest.get("configuration_hash")
            if not isinstance(key_hash, str) or not isinstance(configuration_hash, str):
                continue
            existing = self._idempotency.get(key_hash)
            candidate = (configuration_hash, manifest["run_id"])
            if existing is not None and existing != candidate:
                raise ContractError("duplicate persisted Idempotency-Key binding")
            self._idempotency[key_hash] = candidate

    def state(self, run_id: str) -> dict[str, Any]:
        return project_ui_state(self.root, self.registry, self.store.read(run_id))

    def frontend_snapshot(self, run_id: str) -> dict[str, Any]:
        return project_frontend_snapshot(
            self.root, self.registry, self.store.read(run_id)
        )

    def create_run(self, payload: Any, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        setup = validate_setup(payload, registry=self.registry)
        configuration_hash = sha256_json(setup)
        if idempotency_key:
            if len(idempotency_key) > 200:
                raise ContractError("Idempotency-Key must be at most 200 characters")
            key_hash = "sha256:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            with self._idempotency_lock:
                existing = self._idempotency.get(key_hash)
                if existing:
                    existing_hash, run_id = existing
                    if existing_hash != configuration_hash:
                        raise ApplicationConflict(
                            "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
                            "Idempotency-Key was already used with a different run configuration",
                        )
                    return self.state(run_id), False
                manifest = self.store.create(setup, idempotency_key_hash=key_hash)
                self._idempotency[key_hash] = (
                    configuration_hash,
                    manifest["run_id"],
                )
        else:
            manifest = self.store.create(setup)
        state = project_ui_state(self.root, self.registry, manifest)
        self.coordinator.start(manifest["run_id"])
        return state, True

    def launch_highlander(self, run_id: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"acknowledgeGaps"}:
            raise ContractError("Highlander body accepts only acknowledgeGaps")
        acknowledge = payload.get("acknowledgeGaps") is True
        current = self.store.read(run_id)
        if current.get("scientific", {}).get("enabled"):
            return self._launch_scientific_highlander(
                run_id, acknowledge_gaps=acknowledge
            )
        packet_path = self.store.run_dir(run_id) / "highlander_packet.json"

        def update(value: dict[str, Any]) -> bool:
            highlander = value["highlander"]
            if not highlander["ready"]:
                raise ApplicationConflict(
                    "HIGHLANDER_NOT_READY", "a terminal candidate packet is required"
                )
            if highlander["requires_gap_acknowledgement"] and not acknowledge:
                raise ApplicationConflict(
                    "GAP_ACKNOWLEDGEMENT_REQUIRED", "terminal gaps must be acknowledged"
                )
            if highlander["launched"]:
                return False
            created_at = utc_now()
            packet = {
                "schema_version": "labrador.highlander-packet.v1",
                "run_id": run_id,
                "created_at": created_at,
                "comparison_basis": (
                    "the native HypGen slate is contextualized across the configured "
                    "evidence-graph signals with shared downstream outputs; "
                    "no Highlander module repository is wired"
                ),
                "stage_outputs": [
                    {
                        "stage": stage["id"],
                        "output_hash": stage["output_hash"],
                        "output_origin": stage["output_origin"],
                        "result_basis": stage["result_basis"],
                        "qualifiers": stage["qualifiers"],
                    }
                    for stage in value["stages"]
                    if stage.get("output_hash")
                ],
                "gap_acknowledged": True,
            }
            dump_json_atomic(packet_path, packet)
            highlander.update(
                {
                    "launched": True,
                    "job_id": f"HL-{run_id[3:]}",
                    "packet_snapshot": {
                        "id": f"PKT-{run_id[3:]}-R{value['revision'] + 1}",
                        "createdAt": created_at,
                        "stageOutputCount": len(packet["stage_outputs"]),
                        "gapAcknowledged": True,
                    },
                }
            )
            return True

        updated, _ = self.store.mutate_if(run_id, "HIGHLANDER_LAUNCHED", update)
        return project_ui_state(self.root, self.registry, updated)

    def _launch_scientific_highlander(
        self, run_id: str, *, acknowledge_gaps: bool
    ) -> dict[str, Any]:
        with self._highlander_lock(run_id):
            manifest = self.store.read(run_id)
            highlander = manifest["highlander"]
            if not highlander["ready"]:
                raise ApplicationConflict(
                    "HIGHLANDER_NOT_READY", "a terminal candidate packet is required"
                )
            if highlander["requires_gap_acknowledgement"] and not acknowledge_gaps:
                raise ApplicationConflict(
                    "GAP_ACKNOWLEDGEMENT_REQUIRED", "terminal gaps must be acknowledged"
                )
            if highlander["launched"]:
                return project_ui_state(self.root, self.registry, manifest)

            created_at = utc_now()
            try:
                spec = HighlanderSpec.load(self.root)
                artifacts = invoke_highlander(
                    self.root,
                    run_id,
                    manifest,
                    spec,
                    created_at=created_at,
                )
            except (HighlanderInvocationError, ContractError) as exc:
                failure_code = (
                    exc.code
                    if isinstance(exc, HighlanderInvocationError)
                    else "HIGHLANDER_PACKET_INVALID"
                )
                failure_message = str(exc)

                def record_failure(value: dict[str, Any]) -> None:
                    value["highlander"]["last_error"] = {
                        "code": failure_code,
                        "message": failure_message,
                        "at": utc_now(),
                    }

                self.store.mutate(
                    run_id,
                    "HIGHLANDER_FAILED",
                    record_failure,
                    {"code": failure_code},
                )
                raise ApplicationConflict(failure_code, failure_message) from exc

            def record_success(value: dict[str, Any]) -> bool:
                target = value["highlander"]
                if target["launched"]:
                    return False
                target.update(
                    {
                        "launched": True,
                        "job_id": f"HL-{run_id[3:]}",
                        "request_ref": artifacts.request_ref,
                        "request_raw_hash": artifacts.request_raw_sha256,
                        "request_canonical_hash": artifacts.request_canonical_sha256,
                        "result_ref": artifacts.result_ref,
                        "result_hash": "sha256:" + artifacts.result_raw_sha256,
                        "result_raw_hash": artifacts.result_raw_sha256,
                        "result_canonical_hash": artifacts.result_canonical_sha256,
                        "execution_ref": artifacts.execution_ref,
                        "last_error": None,
                        "packet_snapshot": {
                            "id": f"PKT-{run_id[3:]}-R{value['revision'] + 1}",
                            "schemaVersion": "highlander.packet-comparison-request.v1",
                            "createdAt": created_at,
                            "candidateCount": len(artifacts.candidate_hashes),
                            "candidateHashes": list(artifacts.candidate_hashes),
                            "requestRef": artifacts.request_ref,
                            "requestRawSha256": artifacts.request_raw_sha256,
                            "requestCanonicalSha256": artifacts.request_canonical_sha256,
                            "resultRef": artifacts.result_ref,
                            "resultRawSha256": artifacts.result_raw_sha256,
                            "resultCanonicalSha256": artifacts.result_canonical_sha256,
                            "gapAcknowledged": True,
                        },
                    }
                )
                return True

            updated, _ = self.store.mutate_if(
                run_id,
                "SCIENTIFIC_HIGHLANDER_LAUNCHED",
                record_success,
                {"candidates": len(artifacts.candidate_hashes)},
            )
            return project_ui_state(self.root, self.registry, updated)


class ApplicationConflict(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _handler(application: LabradorApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "LABradorDemo/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            # Keep the familiar local-server log but never include request bodies.
            super().log_message(format, *args)

        def _send_bytes(
            self,
            status: int,
            payload: bytes,
            content_type: str,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if urlsplit(self.path).path.startswith("/api/"):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Content-Type, Idempotency-Key"
                )
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, value: Any) -> None:
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._send_bytes(status, payload, "application/json; charset=utf-8")

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def _read_json(self) -> Any:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ContractError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ContractError("Content-Length must be an integer") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ContractError(f"request body must be at most {MAX_REQUEST_BYTES} bytes")
            raw = self.rfile.read(length)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractError("request body must be UTF-8 JSON") from exc
            return loads_json(text, label="request body")

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            try:
                if path == "/api/health":
                    self._json(
                        HTTPStatus.OK,
                        {
                            "status": "ok",
                            "service": "labrador-demo-orchestrator",
                            "schemaVersion": "labrador.health.v1",
                        },
                    )
                    return
                if path == "/api/meta":
                    self._json(HTTPStatus.OK, project_frontend_meta(application.registry))
                    return
                if path == "/api/modules/preflight":
                    self._json(
                        HTTPStatus.OK,
                        {"modules": application.registry.preflight(check_git=True)},
                    )
                    return
                parts = [unquote(part) for part in path.split("/") if part]
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "state":
                    self._json(HTTPStatus.OK, application.state(parts[2]))
                    return
                if (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "snapshot"
                ):
                    self._json(HTTPStatus.OK, application.frontend_snapshot(parts[2]))
                    return
                self._serve_static(path)
            except FileNotFoundError:
                self._error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "run does not exist")
            except ContractError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(exc))
            except Exception:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "INTERNAL_ERROR",
                    "the local orchestrator could not complete the request",
                )

        def _serve_static(self, path: str) -> None:
            relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
            if relative.startswith("api/") or relative.startswith("runs/"):
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "route does not exist")
                return
            try:
                file_path = resolve_within(
                    application.ui_root,
                    application.ui_root / relative,
                    label="static asset",
                )
            except ContractError:
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "asset does not exist")
                return
            if not file_path.is_file() or file_path.name == "verify_mockup.mjs":
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "asset does not exist")
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
            }:
                content_type += "; charset=utf-8"
            self._send_bytes(HTTPStatus.OK, file_path.read_bytes(), content_type)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            try:
                if path == "/api/runs":
                    payload = self._read_json()
                    frontend_request = is_frontend_v0_request(payload)
                    state, created = application.create_run(
                        payload, self.headers.get("Idempotency-Key")
                    )
                    if frontend_request:
                        self._json(
                            HTTPStatus.CREATED if created else HTTPStatus.OK,
                            {"run": {"run_id": state["runId"]}},
                        )
                    else:
                        self._json(HTTPStatus.ACCEPTED if created else HTTPStatus.OK, state)
                    return
                parts = [unquote(part) for part in path.split("/") if part]
                if (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "highlander"
                ):
                    self._json(
                        HTTPStatus.OK,
                        application.launch_highlander(parts[2], self._read_json()),
                    )
                    return
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "route does not exist")
            except FileNotFoundError:
                self._error(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "run does not exist")
            except ApplicationConflict as exc:
                self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
            except ContractError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(exc))
            except Exception:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "INTERNAL_ERROR",
                    "the local orchestrator could not complete the request",
                )

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802
            if urlsplit(self.path).path.startswith("/api/"):
                self._send_bytes(HTTPStatus.NO_CONTENT, b"", "application/json")
                return
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            self._send_bytes(
                HTTPStatus.METHOD_NOT_ALLOWED,
                json.dumps(
                    {"error": {"code": "METHOD_NOT_ALLOWED", "message": "method not allowed"}}
                ).encode(),
                "application/json; charset=utf-8",
                extra_headers={"Allow": "GET, POST"},
            )

    return Handler


def create_server(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    application = LabradorApplication(root)
    server = ThreadingHTTPServer((host, port), _handler(application))
    server.daemon_threads = True
    server.application = application  # type: ignore[attr-defined]
    return server
