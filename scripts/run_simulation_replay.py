#!/usr/bin/env python3
"""Revalidate a recorded tractability dossier with pinned module code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--recorded-output", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.module_root = args.module_root.resolve()
    args.recorded_output = args.recorded_output.resolve()
    args.input = args.input.resolve()
    args.output = args.output.resolve()

    request = json.loads(args.input.read_text(encoding="utf-8"))
    recorded = json.loads(args.recorded_output.read_text(encoding="utf-8"))
    echoed = recorded.get("input")
    if not isinstance(echoed, dict):
        print("RECORDED_INPUT_MISMATCH: dossier has no input echo", file=sys.stderr)
        return 3
    keys = (
        "uniprot_accession",
        "as_of_date",
        "disease_context",
        "interaction_to_disrupt",
        "mechanism_hypothesis",
    )
    if any(request.get(key) != echoed.get(key) for key in keys):
        print("RECORDED_INPUT_MISMATCH: tractability cache identity differs", file=sys.stderr)
        return 3

    sys.path.insert(0, str(args.module_root))
    try:
        from simulation import build_interpretability

        dossier = dict(recorded)
        dossier.pop("interpretability", None)
        dossier["interpretability"] = build_interpretability(dossier)
    except Exception as exc:  # noqa: BLE001 - adapter boundary reports a clean nonzero
        print(f"TRACTABILITY_REVALIDATION_FAILED: {exc}", file=sys.stderr)
        return 4

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(dossier, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
