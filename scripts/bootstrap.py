#!/usr/bin/env python3
"""Clone the pinned frontend and five modules, then prepare Python runtimes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def clone_or_verify(module: dict[str, object]) -> Path:
    destination = ROOT / str(module["module_root"])
    expected = str(module["commit"])
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", str(module["repository_url"]), str(destination)])
        run(["git", "checkout", "--detach", expected], cwd=destination)
        return destination
    if not (destination / ".git").exists():
        raise RuntimeError(f"refusing to replace non-git path: {destination}")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destination,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if actual != expected:
        raise RuntimeError(
            f"{module['id']} is at {actual}; expected {expected}. "
            "Move it aside or check out the pinned SHA explicitly."
        )
    return destination


def main() -> int:
    if shutil.which("git") is None or shutil.which("uv") is None:
        print("bootstrap requires git and uv on PATH", file=sys.stderr)
        return 2
    lock = json.loads((ROOT / "module-lock.json").read_text(encoding="utf-8"))
    clone_or_verify(lock["frontend"])
    roots: dict[str, Path] = {}
    for module in lock["modules"]:
        roots[str(module["id"])] = clone_or_verify(module)

    run(["uv", "sync", "--extra", "dev"], cwd=roots["hypothesis_generator"])
    run(
        ["uv", "sync", "--locked", "--extra", "dev", "--no-editable"],
        cwd=roots["roi_calculator"],
    )
    bun = shutil.which("bun")
    if bun is None:
        print(
            "Bun not found; skipping optional evidence and recruitment dependency installs. "
            "The pinned judging replays remain available.",
            file=sys.stderr,
        )
    else:
        for module_id in ("evidence_mapper", "clinical_simulation"):
            if module_id in roots:
                run([bun, "install", "--frozen-lockfile"], cwd=roots[module_id])
    print(
        "\nPinned frontend and modules are present. Bun and Python module dependencies are ready."
    )
    print("Next: uv run python app.py preflight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
