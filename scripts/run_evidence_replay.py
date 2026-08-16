#!/usr/bin/env python3
"""Rebuild a recorded mapper graph with the pinned module's own assembler."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--recorded-input", type=Path, required=True)
    parser.add_argument("--recorded-output", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.module_root = args.module_root.resolve()
    args.recorded_input = args.recorded_input.resolve()
    args.recorded_output = args.recorded_output.resolve()
    args.input = args.input.resolve()
    args.output = args.output.resolve()

    request = json.loads(args.input.read_text(encoding="utf-8"))
    recorded_request = json.loads(args.recorded_input.read_text(encoding="utf-8"))
    recorded = json.loads(args.recorded_output.read_text(encoding="utf-8"))
    if request != recorded_request:
        print("RECORDED_INPUT_MISMATCH: evidence request differs", file=sys.stderr)
        return 3
    if recorded.get("question") != request.get("target"):
        print("RECORDED_INPUT_MISMATCH: evidence question differs", file=sys.stderr)
        return 3
    assembler = args.module_root / "skills" / "graph-assembly" / "assemble.py"
    if not assembler.is_file():
        print(f"REPLAY_RUNTIME_MISSING: {assembler}", file=sys.stderr)
        return 4
    with tempfile.TemporaryDirectory(prefix="labrador-evidence-replay-") as temporary:
        rebuilt = Path(temporary) / "rebuilt.json"
        result = subprocess.run(
            [
                sys.executable,
                str(assembler),
                "--rebuild",
                str(args.recorded_output),
                "--out",
                str(rebuilt),
            ],
            cwd=args.module_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not rebuilt.is_file():
            sys.stderr.write(result.stderr or "EVIDENCE_REPLAY_FAILED\n")
            return result.returncode or 5
        rebuilt_value = json.loads(rebuilt.read_text(encoding="utf-8"))
        if rebuilt_value.get("question") != request.get("target"):
            print("RECORDED_INPUT_MISMATCH: rebuilt question differs", file=sys.stderr)
            return 6
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = args.output.with_name(f".{args.output.name}.tmp")
        temporary_output.write_text(
            json.dumps(rebuilt_value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_output.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
