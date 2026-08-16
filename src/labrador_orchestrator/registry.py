"""Pinned module registry and preflight validation."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError, load_json, resolve_within, validate_json


@dataclass(frozen=True)
class ModuleSpec:
    module_id: str
    ui_stage: str
    order: int
    repository: str
    repository_url: str
    commit: str
    module_root: Path
    mode: str
    setup_command: tuple[str, ...]
    command: tuple[str, ...]
    timeout_seconds: float
    input_schema: Path
    output_schema: Path
    example_input: Path
    example_output: Path
    fallback_output: Path
    runtime_maturity: str
    result_basis: tuple[str, ...]
    qualifiers: tuple[str, ...]

    @classmethod
    def from_json(cls, root: Path, raw: dict[str, Any]) -> ModuleSpec:
        required = {
            "id",
            "ui_stage",
            "order",
            "repository",
            "repository_url",
            "commit",
            "module_root",
            "mode",
            "setup_command",
            "command",
            "timeout_seconds",
            "input_schema",
            "output_schema",
            "example_input",
            "example_output",
            "fallback_output",
            "runtime_maturity",
            "result_basis",
            "qualifiers",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise ContractError(f"registry module missing fields: {', '.join(missing)}")
        if raw["mode"] not in {"auto", "cached", "replay"}:
            raise ContractError(f"{raw['id']}: unsupported mode {raw['mode']!r}")
        if not isinstance(raw["command"], list) or not all(
            isinstance(item, str) for item in raw["command"]
        ):
            raise ContractError(f"{raw['id']}: command must be an argument array")
        return cls(
            module_id=str(raw["id"]),
            ui_stage=str(raw["ui_stage"]),
            order=int(raw["order"]),
            repository=str(raw["repository"]),
            repository_url=str(raw["repository_url"]),
            commit=str(raw["commit"]),
            module_root=root / str(raw["module_root"]),
            mode=str(raw["mode"]),
            setup_command=tuple(str(item) for item in raw["setup_command"]),
            command=tuple(str(item) for item in raw["command"]),
            timeout_seconds=float(raw["timeout_seconds"]),
            input_schema=root / str(raw["input_schema"]),
            output_schema=root / str(raw["output_schema"]),
            example_input=root / str(raw["example_input"]),
            example_output=root / str(raw["example_output"]),
            fallback_output=root / str(raw["fallback_output"]),
            runtime_maturity=str(raw["runtime_maturity"]),
            result_basis=tuple(str(item) for item in raw["result_basis"]),
            qualifiers=tuple(str(item) for item in raw["qualifiers"]),
        )

    def expand_command(self, *, root: Path, input_path: Path, output_path: Path) -> list[str]:
        values = {
            "orchestrator_root": str(root.resolve()),
            "module_root": str(self.module_root.resolve()),
            "input": str(input_path.resolve()),
            "output": str(output_path.resolve()),
            "output_dir": str(output_path.resolve().parent),
        }
        return [part.format(**values) for part in self.command]


class ModuleRegistry:
    def __init__(self, root: Path, modules: list[ModuleSpec], golden_program: dict[str, Any]):
        self.root = root.resolve()
        self.modules = sorted(modules, key=lambda item: item.order)
        self.golden_program = golden_program

    @classmethod
    def load(cls, root: Path, path: Path | None = None) -> ModuleRegistry:
        root = root.resolve()
        raw = load_json(path or root / "module-lock.json")
        if raw.get("registry_version") != "1.0":
            raise ContractError("module-lock.json must use registry_version 1.0")
        modules = [ModuleSpec.from_json(root, item) for item in raw.get("modules", [])]
        if len(modules) != 5:
            raise ContractError(f"expected exactly five modules, found {len(modules)}")
        ids = [module.module_id for module in modules]
        if len(set(ids)) != len(ids):
            raise ContractError("module ids must be unique")
        orders = [module.order for module in modules]
        if len(set(orders)) != len(orders):
            raise ContractError("module execution orders must be unique")
        stages = {module.ui_stage for module in modules}
        expected_stages = {"biomarker", "hypothesis", "recruitability", "roi", "simulation"}
        if stages != expected_stages:
            raise ContractError(f"module UI stages must be {sorted(expected_stages)}")
        ordered = {module.module_id: module.order for module in modules}
        for dependency, consumer in (
            ("evidence_mapper", "hypothesis_generator"),
            ("clinical_simulation", "roi_calculator"),
        ):
            if ordered[dependency] >= ordered[consumer]:
                raise ContractError(f"{consumer} depends on {dependency} and must execute later")
        return cls(root, modules, dict(raw.get("golden_program") or {}))

    def by_id(self, module_id: str) -> ModuleSpec:
        for module in self.modules:
            if module.module_id == module_id:
                return module
        raise KeyError(module_id)

    def preflight(self, *, check_git: bool = True) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for module in self.modules:
            report: dict[str, Any] = {"module": module.module_id, "ok": False, "checks": []}
            for label, path in (
                ("module root", module.module_root),
                ("input schema", module.input_schema),
                ("output schema", module.output_schema),
                ("example input", module.example_input),
                ("example output", module.example_output),
                ("fallback output", module.fallback_output),
            ):
                if not path.exists():
                    raise ContractError(f"{module.module_id}: missing {label} at {path}")
                if label != "module root":
                    resolve_within(self.root, path, label=f"{module.module_id} {label}")
                report["checks"].append(f"{label}: present")

            input_schema = load_json(module.input_schema)
            output_schema = load_json(module.output_schema)
            example_input = load_json(module.example_input)
            example_output = load_json(module.example_output)
            fallback_output = load_json(module.fallback_output)
            validate_json(
                input_schema,
                example_input,
                label=f"{module.module_id} example input",
                schema_path=module.input_schema,
            )
            validate_json(
                output_schema,
                example_output,
                label=f"{module.module_id} example output",
                schema_path=module.output_schema,
            )
            validate_json(
                output_schema,
                fallback_output,
                label=f"{module.module_id} fallback",
                schema_path=module.output_schema,
            )
            from .adapters import validate_semantics

            validate_semantics(module.module_id, fallback_output)
            report["checks"].append("schemas and examples: valid")

            git_state = "not checked"
            if check_git and not (module.module_root / ".git").exists():
                raise ContractError(f"{module.module_id}: missing Git metadata for pinned module")
            if check_git:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=module.module_root,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                actual = result.stdout.strip()
                if result.returncode != 0 or actual != module.commit:
                    raise ContractError(
                        f"{module.module_id}: expected commit {module.commit}, "
                        f"found {actual or 'unknown'}"
                    )
                git_state = actual
            report["git_commit"] = git_state

            if module.mode in {"auto", "replay"} and module.command:
                first = module.expand_command(
                    root=self.root,
                    input_path=module.example_input,
                    output_path=self.root / "runs" / ".preflight-output.json",
                )[0]
                available = (
                    Path(first).exists() if "/" in first else shutil.which(first) is not None
                )
                report["command_available"] = available
            else:
                report["command_available"] = False
            report["ok"] = True
            reports.append(report)
        return reports
