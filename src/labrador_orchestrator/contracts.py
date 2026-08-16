"""Strict JSON and JSON-Schema helpers shared by the orchestrator."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


class ContractError(ValueError):
    """A module contract or payload failed validation."""


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-standard JSON numeric constant {value!r} is not allowed")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    """Load strict JSON: no duplicate keys, NaN, or Infinity."""

    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(
                handle,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise ContractError(f"unable to read strict JSON from {path}: {exc}") from exc


def loads_json(payload: str, *, label: str = "JSON payload") -> Any:
    """Parse an in-memory strict JSON document with the same rules as :func:`load_json`."""

    try:
        return json.loads(
            payload,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ContractError) as exc:
        raise ContractError(f"unable to parse strict {label}: {exc}") from exc


def canonical_json(value: Any) -> str:
    """Return the stable representation used for hashes and immutable setup snapshots."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(f"value is not canonical JSON: {exc}") from exc


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def dump_json_atomic(path: Path, value: Any) -> None:
    """Write a complete JSON document, then atomically promote it into place."""

    payload = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _local_schema_registry(schema: dict[str, Any], schema_path: Path) -> Registry:
    """Register sibling schema documents under both file and declared URI identities."""

    schema_path = schema_path.resolve()
    root_id = schema.get("$id") if isinstance(schema.get("$id"), str) else None
    registry = Registry()
    for path in sorted(schema_path.parent.glob("*.json")):
        contents = schema if path.resolve() == schema_path else load_json(path)
        if not isinstance(contents, dict):
            continue
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        registry = registry.with_resource(path.resolve().as_uri(), resource)
        declared_id = contents.get("$id")
        if isinstance(declared_id, str):
            registry = registry.with_resource(declared_id, resource)
        if root_id:
            registry = registry.with_resource(urljoin(root_id, path.name), resource)
    return registry


def validate_json(
    schema: dict[str, Any],
    instance: Any,
    *,
    label: str,
    schema_path: Path | None = None,
) -> None:
    """Validate an instance and raise one bounded, stable error message."""

    validator_cls = validator_for(schema)
    try:
        validator_cls.check_schema(schema)
        options = (
            {"registry": _local_schema_registry(schema, schema_path)}
            if schema_path is not None
            else {}
        )
        errors = sorted(
            validator_cls(schema, **options).iter_errors(instance),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
    except ValidationError as exc:
        raise ContractError(f"invalid JSON Schema for {label}: {exc.message}") from exc
    if not errors:
        return
    first = errors[0]
    pointer = "/" + "/".join(str(part) for part in first.absolute_path)
    if pointer == "/":
        pointer = "<root>"
    raise ContractError(f"{label} failed at {pointer}: {first.message}")


def require_finite_numbers(value: Any, *, label: str = "payload") -> None:
    """Reject non-finite numbers even when a permissive producer constructed them in memory."""

    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"{label} contains a non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            require_finite_numbers(child, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            require_finite_numbers(child, label=f"{label}[{index}]")


def resolve_within(base: Path, candidate: Path, *, label: str) -> Path:
    """Resolve a path and reject traversal or symlink escape from ``base``."""

    resolved_base = base.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_base and resolved_base not in resolved.parents:
        raise ContractError(f"{label} escapes allowed root {resolved_base}")
    return resolved
