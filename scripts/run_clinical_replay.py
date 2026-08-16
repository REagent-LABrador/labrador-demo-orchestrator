#!/usr/bin/env python3
"""Validate and promote the pinned recruitment result for its exact thesis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.setdefault("as_of_date", None)
    result.setdefault("mechanism_hypothesis", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorded-output", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.recorded_output = args.recorded_output.resolve()
    args.input = args.input.resolve()
    args.output = args.output.resolve()

    request = json.loads(args.input.read_text(encoding="utf-8"))
    recorded = json.loads(args.recorded_output.read_text(encoding="utf-8"))
    echoed = recorded.get("input")
    if not isinstance(request, dict) or not isinstance(echoed, dict):
        print("RECORDED_INPUT_MISMATCH: clinical input is not an object", file=sys.stderr)
        return 3
    if _normalized(request) != _normalized(echoed):
        print("RECORDED_INPUT_MISMATCH: clinical thesis differs", file=sys.stderr)
        return 3
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(recorded, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
