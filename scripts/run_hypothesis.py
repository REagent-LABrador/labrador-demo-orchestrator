#!/usr/bin/env python3
"""Normalize Hypothesis_Generator's directory output to one file-in/file-out command."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--craziness", type=float, default=0.5)
    args = parser.parse_args()

    executable = args.module_root / ".venv" / "bin" / "hypgen"
    if not executable.exists():
        print(f"hypgen environment missing at {executable}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="labrador-hypgen-") as temporary:
        output_dir = Path(temporary)
        environment = os.environ.copy()
        import_paths = [str(args.module_root / "src"), str(args.module_root)]
        if environment.get("PYTHONPATH"):
            import_paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(import_paths)
        result = subprocess.run(
            [
                str(executable),
                "--graph",
                str(args.input),
                "--profile",
                "default",
                "--craziness",
                str(max(0.0, min(1.0, args.craziness))),
                "--dry-run",
                "--out",
                str(output_dir),
            ],
            cwd=args.module_root,
            env=environment,
            check=False,
        )
        generated = output_dir / "hypothesis.json"
        if result.returncode != 0 or not generated.exists():
            return result.returncode or 3
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
