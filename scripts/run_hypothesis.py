#!/usr/bin/env python3
"""Emit three deterministic structural candidates through HypGen's official cards adapter."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--craziness", type=float, default=0.5)
    args = parser.parse_args()

    args.module_root = args.module_root.resolve()
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    environment_python = args.module_root / ".venv" / "bin" / "python"
    if not environment_python.exists():
        print(f"hypgen environment missing at {environment_python}", file=sys.stderr)
        return 2

    if os.environ.get("LABRADOR_HYPGEN_ENV") != "1":
        environment = os.environ.copy()
        environment["LABRADOR_HYPGEN_ENV"] = "1"
        return subprocess.run(
            [
                str(environment_python),
                str(Path(__file__).resolve()),
                "--module-root",
                str(args.module_root),
                "--input",
                str(args.input),
                "--output",
                str(args.output),
                "--craziness",
                str(args.craziness),
            ],
            cwd=args.module_root,
            env=environment,
            check=False,
        ).returncode

    import_paths = [str(args.module_root / "src"), str(args.module_root)]
    if os.environ.get("PYTHONPATH"):
        import_paths.append(os.environ["PYTHONPATH"])
    sys.path[:0] = import_paths

    try:
        from adapters.common import Bundle
        from adapters.webui.payload import emit
        from hyp_gen import Generator, KnowledgeGraph, Params

        graph = KnowledgeGraph.model_validate(json.loads(args.input.read_text(encoding="utf-8")))
        params = Params.at_craziness(max(0.0, min(1.0, args.craziness)))
        result = Generator(graph=graph, params=params).run()
        candidates = result.hypotheses[:3]
        if len(candidates) < 2:
            print("HYPOTHESIS_SLATE_TOO_SMALL", file=sys.stderr)
            return 3
        bundle = Bundle(
            provenance=result.provenance,
            hypotheses=candidates,
            asks=[
                ask
                for ask in result.asks
                if ask.for_hypothesis is None
                or ask.for_hypothesis in {candidate.id for candidate in candidates}
            ],
        )
        payload = emit(bundle).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - file/CLI boundary reports a clean nonzero
        print(f"HYPOTHESIS_SLATE_FAILED: {exc}", file=sys.stderr)
        return 4

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
