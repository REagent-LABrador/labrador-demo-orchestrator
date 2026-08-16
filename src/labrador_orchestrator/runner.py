"""Strictly sequential module execution with validated, truth-labeled fallbacks."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .adapters import (
    StageAbstention,
    build_module_input,
    hypothesis_craziness,
    input_source,
    validate_semantics,
)
from .contracts import (
    ContractError,
    dump_json_atomic,
    load_json,
    sha256_file,
    validate_json,
)
from .registry import ModuleRegistry, ModuleSpec
from .store import RunStore, utc_now

TERMINAL_STAGE_STATUSES = {"COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED", "SKIPPED"}


def _stage(manifest: dict[str, Any], module_id: str) -> dict[str, Any]:
    for stage in manifest["stages"]:
        if stage["module_id"] == module_id:
            return stage
    raise ContractError(f"manifest has no stage for {module_id}")


def _bounded(value: str, limit: int = 20_000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated {len(value) - limit} characters"


class SequentialRunner:
    def __init__(self, root: Path, registry: ModuleRegistry, store: RunStore):
        self.root = root.resolve()
        self.registry = registry
        self.store = store

    def _module_dir(self, run_id: str, module: ModuleSpec) -> Path:
        return self.store.run_dir(run_id) / f"{module.order:02d}_{module.module_id}"

    def _mark_run_started(self, run_id: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            if manifest["run_status"] != "CREATED":
                raise ContractError(f"run {run_id} cannot start from {manifest['run_status']}")
            manifest["run_status"] = "RUNNING"

        self.store.mutate(run_id, "RUN_STARTED", update)

    def _mark_stage_running(
        self,
        run_id: str,
        module: ModuleSpec,
        *,
        input_path: Path,
        input_hash: str,
        source: str,
        attempt: dict[str, Any],
    ) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            if stage["status"] != "QUEUED":
                raise ContractError(f"{module.module_id} cannot start from {stage['status']}")
            stage.update(
                {
                    "status": "RUNNING",
                    "execution_status": "RUNNING",
                    "note": "module attempt running",
                    "started_at": attempt["started_at"],
                    "input_ref": str(input_path.relative_to(self.store.run_dir(run_id))),
                    "input_hash": input_hash,
                    "input_source": source,
                }
            )
            stage["attempts"].append(attempt)
            manifest["current_stage"] = module.ui_stage

        self.store.mutate(
            run_id,
            "STAGE_STARTED",
            update,
            {"module": module.module_id, "attempt": attempt["attempt"]},
        )

    def _complete_stage(
        self,
        run_id: str,
        module: ModuleSpec,
        *,
        stage_status: str,
        execution_status: str,
        output_origin: str,
        reason_code: str | None,
        note: str,
        output_path: Path,
        warnings: list[str],
        attempt_update: dict[str, Any],
    ) -> None:
        completed_at = utc_now()

        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            if stage["status"] != "RUNNING":
                raise ContractError(f"{module.module_id} cannot complete from {stage['status']}")
            stage.update(
                {
                    "status": stage_status,
                    "execution_status": execution_status,
                    "output_origin": output_origin,
                    "reason_code": reason_code,
                    "note": note,
                    "completed_at": completed_at,
                    "warnings": warnings,
                    "output_ref": str(output_path.relative_to(self.store.run_dir(run_id))),
                    "output_hash": sha256_file(output_path),
                }
            )
            stage["attempts"][-1].update(attempt_update)
            stage["attempts"][-1]["completed_at"] = completed_at

        self.store.mutate(
            run_id,
            "STAGE_TERMINAL",
            update,
            {
                "module": module.module_id,
                "status": stage_status,
                "execution_status": execution_status,
                "output_origin": output_origin,
                "reason_code": reason_code,
            },
        )

    def _fallback(
        self,
        module: ModuleSpec,
        output_path: Path,
        *,
        origin: str,
        module_input: Any,
        setup: dict[str, Any],
    ) -> Any:
        if setup.get("fallbackPolicy") != "PROFILE_MATCH_ONLY":
            raise StageAbstention(
                "NO_MATCHING_CACHED_ARTIFACT",
                f"{module.module_id} has no cached artifact bound to this analyst frame",
            )
        fallback = load_json(module.fallback_output)
        validate_json(
            load_json(module.output_schema),
            fallback,
            label=f"{module.module_id} fallback",
            schema_path=module.output_schema,
        )
        validate_semantics(
            module.module_id, fallback, module_input=module_input, setup=setup
        )
        dump_json_atomic(output_path, fallback)
        return fallback

    def _execute(
        self,
        run_id: str,
        module: ModuleSpec,
        input_path: Path,
        output_path: Path,
        setup: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """Return output and an execution result; never invokes a shell."""

        module_input = load_json(input_path)
        if module.mode == "cached":
            output = self._fallback(
                module,
                output_path,
                origin="CACHED",
                module_input=module_input,
                setup=setup,
            )
            return output, {
                "stage_status": "COMPLETE_WITH_WARNINGS",
                "execution_status": "SKIPPED",
                "output_origin": "CACHED",
                "reason_code": "MODULE_CONFIGURED_CACHED",
                "note": "validated pinned artifact used; live module not invoked",
                "warnings": ["This stage is a pinned cached artifact, not a live invocation."],
                "attempt": {"status": "SKIPPED", "exit_code": None, "duration_ms": 0},
            }

        live_output_path = output_path.with_name("live-output.json")
        command = module.expand_command(
            root=self.root, input_path=input_path, output_path=live_output_path
        )
        if module.module_id == "hypothesis_generator":
            command.extend(["--craziness", f"{hypothesis_craziness(setup):.6f}"])
        executable = command[0]
        available = Path(executable).exists() if "/" in executable else shutil.which(executable)
        if not available:
            try:
                output = self._fallback(
                    module,
                    output_path,
                    origin="DEMO_FALLBACK",
                    module_input=module_input,
                    setup=setup,
                )
            except StageAbstention as exc:
                exc.attempt_update = {
                    "status": "FAILED",
                    "reason_code": "RUNTIME_UNAVAILABLE",
                    "exit_code": None,
                    "duration_ms": 0,
                    "stdout": "",
                    "stderr": f"runtime {Path(executable).name} unavailable",
                }
                raise
            return output, {
                "stage_status": "COMPLETE_WITH_WARNINGS",
                "execution_status": "FAILED",
                "output_origin": "DEMO_FALLBACK",
                "reason_code": "RUNTIME_UNAVAILABLE",
                "note": (
                    f"runtime {Path(executable).name} unavailable; "
                    "validated demo fallback used"
                ),
                "warnings": [
                    f"Live execution could not start because "
                    f"{Path(executable).name} is unavailable."
                ],
                "attempt": {"status": "FAILED", "exit_code": None, "duration_ms": 0},
            }

        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=module.module_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=module.timeout_seconds,
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            if result.returncode != 0:
                raise ModuleProcessError(
                    "PROCESS_EXIT_NONZERO",
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    duration_ms,
                )
            if not live_output_path.exists():
                raise ModuleProcessError(
                    "OUTPUT_MISSING", result.returncode, result.stdout, result.stderr, duration_ms
                )
            output = load_json(live_output_path)
            validate_json(
                load_json(module.output_schema),
                output,
                label=f"{module.module_id} live output",
                schema_path=module.output_schema,
            )
            validate_semantics(
                module.module_id, output, module_input=module_input, setup=setup
            )
            live_output_path.replace(output_path)
            if module.mode == "replay":
                return output, {
                    "stage_status": "COMPLETE_WITH_WARNINGS",
                    "execution_status": "COMPLETE",
                    "output_origin": "CACHED",
                    "reason_code": "PINNED_ARTIFACT_REVALIDATED",
                    "note": (
                        "module replay command revalidated a pinned scientific artifact; "
                        "no fresh external search or scientific computation was claimed"
                    ),
                    "warnings": [
                        "A module-owned replay/revalidation command ran successfully over "
                        "recorded scientific evidence; the scientific result remains CACHED."
                    ],
                    "attempt": {
                        "status": "COMPLETE",
                        "exit_code": result.returncode,
                        "duration_ms": duration_ms,
                        "stdout": _bounded(result.stdout),
                        "stderr": _bounded(result.stderr),
                    },
                }
            return output, {
                "stage_status": "COMPLETE",
                "execution_status": "COMPLETE",
                "output_origin": "LIVE",
                "reason_code": None,
                "note": "live module output validated",
                "warnings": [],
                "attempt": {
                    "status": "COMPLETE",
                    "exit_code": result.returncode,
                    "duration_ms": duration_ms,
                    "stdout": _bounded(result.stdout),
                    "stderr": _bounded(result.stderr),
                },
            }
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            error = ModuleProcessError(
                "MODULE_TIMEOUT",
                None,
                (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                duration_ms,
                attempt_status="TIMED_OUT",
            )
        except (ContractError, OSError) as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            if live_output_path.exists():
                invalid_path = output_path.with_name("live-output.invalid.json")
                live_output_path.replace(invalid_path)
            error = ModuleProcessError(
                "OUTPUT_INVALID" if isinstance(exc, ContractError) else "PROCESS_START_FAILED",
                None,
                "",
                str(exc),
                duration_ms,
            )
        except ModuleProcessError as exc:
            error = exc

        try:
            output = self._fallback(
                module,
                output_path,
                origin="DEMO_FALLBACK",
                module_input=module_input,
                setup=setup,
            )
        except StageAbstention as exc:
            exc.attempt_update = {
                "status": error.attempt_status,
                "reason_code": error.reason_code,
                "exit_code": error.exit_code,
                "duration_ms": error.duration_ms,
                "stdout": _bounded(error.stdout),
                "stderr": _bounded(error.stderr),
            }
            raise
        return output, {
            "stage_status": "COMPLETE_WITH_WARNINGS",
            "execution_status": error.attempt_status,
            "output_origin": "DEMO_FALLBACK",
            "reason_code": error.reason_code,
            "note": "live attempt failed; validated demo fallback used",
            "warnings": [f"Live attempt failed ({error.reason_code}); demo fallback used."],
            "attempt": {
                "status": error.attempt_status,
                "exit_code": error.exit_code,
                "duration_ms": error.duration_ms,
                "stdout": _bounded(error.stdout),
                "stderr": _bounded(error.stderr),
            },
        }

    def run(self, run_id: str) -> dict[str, Any]:
        self._mark_run_started(run_id)
        outputs: dict[str, Any] = {}
        fatal_error: str | None = None

        for module in self.registry.modules:
            if fatal_error is not None:
                self._skip_upstream_failed(run_id, module, fatal_error)
                continue
            manifest = self.store.read(run_id)
            module_dir = self._module_dir(run_id, module)
            input_path = module_dir / "input.json"
            output_path = module_dir / "output.json"
            try:
                module_input = build_module_input(
                    self.root,
                    module.module_id,
                    setup=manifest["setup"],
                    outputs=outputs,
                )
                validate_json(
                    load_json(module.input_schema),
                    module_input,
                    label=f"{module.module_id} adapted input",
                    schema_path=module.input_schema,
                )
                dump_json_atomic(input_path, module_input)
            except StageAbstention as exc:
                self._skip_unavailable_input(run_id, module, exc.code, str(exc))
                continue
            except ContractError as exc:
                fatal_error = f"{module.module_id} input adapter failed: {exc}"
                self._fail_before_start(run_id, module, fatal_error)
                continue

            attempt_number = len(_stage(manifest, module.module_id)["attempts"]) + 1
            attempt = {
                "attempt": attempt_number,
                "started_at": utc_now(),
                "completed_at": None,
                "status": "RUNNING",
                "exit_code": None,
                "duration_ms": None,
            }
            self._mark_stage_running(
                run_id,
                module,
                input_path=input_path,
                input_hash=sha256_file(input_path),
                source=input_source(module.module_id, manifest["setup"]),
                attempt=attempt,
            )
            try:
                output, result = self._execute(
                    run_id, module, input_path, output_path, manifest["setup"]
                )
            except StageAbstention as exc:
                self._mark_no_fallback(
                    run_id,
                    module,
                    exc.code,
                    str(exc),
                    attempt_update=getattr(exc, "attempt_update", None),
                )
                continue
            except ContractError as exc:
                fatal_error = f"{module.module_id} and its fallback failed validation: {exc}"
                self._mark_irrecoverable(run_id, module, fatal_error)
                continue

            outputs[module.module_id] = output
            self._complete_stage(
                run_id,
                module,
                stage_status=result["stage_status"],
                execution_status=result["execution_status"],
                output_origin=result["output_origin"],
                reason_code=result["reason_code"],
                note=result["note"],
                output_path=output_path,
                warnings=result["warnings"],
                attempt_update=result["attempt"],
            )

        hypothesis_output = outputs.get("hypothesis_generator")
        has_candidate = isinstance(hypothesis_output, dict) and (
            isinstance(hypothesis_output.get("hypothesis"), dict)
            or (
                isinstance(hypothesis_output.get("hypotheses"), list)
                and any(
                    isinstance(candidate, dict)
                    for candidate in hypothesis_output["hypotheses"]
                )
            )
        )
        return self._finish_run(run_id, fatal_error, has_candidate=has_candidate)

    def _skip_unavailable_input(
        self, run_id: str, module: ModuleSpec, reason_code: str, message: str
    ) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            stage.update(
                {
                    "status": "SKIPPED",
                    "execution_status": "SKIPPED",
                    "reason_code": reason_code,
                    "note": message,
                    "completed_at": utc_now(),
                    "input_source": "NOT_AVAILABLE",
                }
            )

        self.store.mutate(run_id, "STAGE_ABSTAINED", update, {"module": module.module_id})

    def _mark_no_fallback(
        self,
        run_id: str,
        module: ModuleSpec,
        reason_code: str,
        message: str,
        *,
        attempt_update: dict[str, Any] | None,
    ) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            stage.update(
                {
                    "status": "FAILED",
                    "execution_status": "FAILED",
                    "output_origin": "NOT_RUN",
                    "reason_code": reason_code,
                    "note": message,
                    "completed_at": utc_now(),
                }
            )
            if stage["attempts"]:
                stage["attempts"][-1].update(attempt_update or {"status": "FAILED"})
                stage["attempts"][-1]["completed_at"] = utc_now()
            manifest["errors"].append(
                {"code": reason_code, "module": module.module_id, "message": message}
            )

        self.store.mutate(run_id, "STAGE_FAILED", update, {"module": module.module_id})

    def _fail_before_start(self, run_id: str, module: ModuleSpec, message: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            stage.update(
                {
                    "status": "FAILED",
                    "execution_status": "SKIPPED",
                    "reason_code": "INPUT_ADAPTER_INVALID",
                    "note": message,
                    "completed_at": utc_now(),
                }
            )
            manifest["errors"].append(
                {"code": "INPUT_ADAPTER_INVALID", "module": module.module_id, "message": message}
            )

        self.store.mutate(run_id, "STAGE_FAILED", update, {"module": module.module_id})

    def _mark_irrecoverable(self, run_id: str, module: ModuleSpec, message: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            stage.update(
                {
                    "status": "FAILED",
                    "execution_status": "FAILED",
                    "reason_code": "FALLBACK_INVALID",
                    "note": message,
                    "completed_at": utc_now(),
                }
            )
            stage["attempts"][-1].update({"status": "FAILED", "completed_at": utc_now()})
            manifest["errors"].append(
                {"code": "FALLBACK_INVALID", "module": module.module_id, "message": message}
            )

        self.store.mutate(run_id, "STAGE_FAILED", update, {"module": module.module_id})

    def _skip_upstream_failed(self, run_id: str, module: ModuleSpec, message: str) -> None:
        def update(manifest: dict[str, Any]) -> None:
            stage = _stage(manifest, module.module_id)
            stage.update(
                {
                    "status": "SKIPPED",
                    "execution_status": "SKIPPED",
                    "reason_code": "UPSTREAM_FAILED",
                    "note": message,
                    "completed_at": utc_now(),
                }
            )

        self.store.mutate(run_id, "STAGE_SKIPPED", update, {"module": module.module_id})

    def _finish_run(
        self, run_id: str, fatal_error: str | None, *, has_candidate: bool
    ) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            manifest["current_stage"] = None
            manifest["completed_at"] = utc_now()
            if fatal_error:
                manifest["run_status"] = "FAILED"
            elif any(
                stage["status"] in {"COMPLETE_WITH_WARNINGS", "FAILED", "SKIPPED"}
                for stage in manifest["stages"]
            ):
                manifest["run_status"] = "COMPLETED_WITH_WARNINGS"
            else:
                manifest["run_status"] = "COMPLETED"
            terminal = all(
                stage["status"] in TERMINAL_STAGE_STATUSES for stage in manifest["stages"]
            )
            manifest["highlander"]["ready"] = terminal and has_candidate

        return self.store.mutate(run_id, "RUN_TERMINAL", update, {"fatal_error": fatal_error})


class ModuleProcessError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        duration_ms: int,
        *,
        attempt_status: str = "FAILED",
    ):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.attempt_status = attempt_status


class RunCoordinator:
    """Start at most one background worker per run ID."""

    def __init__(self, runner: SequentialRunner):
        self.runner = runner
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def start(self, run_id: str) -> bool:
        with self._lock:
            current = self._threads.get(run_id)
            if current is not None:
                return False
            thread = threading.Thread(
                target=self._run_safely,
                args=(run_id,),
                name=f"labrador-{run_id}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
            return True

    def _run_safely(self, run_id: str) -> None:
        try:
            self.runner.run(run_id)
        except Exception as error:  # noqa: BLE001 - boundary must persist a terminal failure
            message = str(error)[:1000]
            with contextlib.suppress(Exception):
                self.runner.store.terminalize_incomplete(
                    run_id,
                    active_code="ORCHESTRATOR_FAILURE",
                    queued_code="UPSTREAM_FAILED",
                    message=message,
                )

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()
