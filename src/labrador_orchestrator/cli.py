"""Operator CLI for the one-machine hackathon demo."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from .contracts import ContractError
from .projection import project_ui_state
from .registry import ModuleRegistry
from .runner import SequentialRunner
from .server import create_server
from .store import RunStore, validate_setup


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_setup() -> dict[str, object]:
    return {
        "clinicalIndication": "Rheumatoid arthritis",
        "biomarkerRange": [1, 10],
        "maxBiomarkers": 3,
        "maxLiteraturePapers": 40,
        "hypothesisRange": [1, 10],
        "maxHypothesesPerBiomarker": 3,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="serve the UI and local API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--no-open", action="store_true", help="do not open the browser")

    preflight = subparsers.add_parser("preflight", help="validate pinned module contracts")
    preflight.add_argument("--no-git", action="store_true", help="skip pinned SHA checks")

    run = subparsers.add_parser("run", help="run the golden path synchronously")
    run.add_argument("--setup", type=Path, help="optional setup JSON; defaults to RA / 3x3")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        registry = ModuleRegistry.load(root)
        if args.command == "preflight":
            print(json.dumps({"modules": registry.preflight(check_git=not args.no_git)}, indent=2))
            return 0
        if args.command == "run":
            if args.setup:
                raw = json.loads(args.setup.read_text(encoding="utf-8"))
            else:
                raw = default_setup()
            store = RunStore(root, registry)
            manifest = store.create(validate_setup(raw, registry=registry))
            final = SequentialRunner(root, registry, store).run(manifest["run_id"])
            state = project_ui_state(root, registry, final)
            print(json.dumps(state, indent=2))
            return 0 if final["run_status"] != "FAILED" else 1
        if args.command == "serve":
            server = create_server(root, args.host, args.port)
            host, port = server.server_address[:2]
            url = f"http://{host}:{port}/"
            print(f"LABrador demo orchestrator: {url}")
            print("Press Ctrl-C to stop. Runs are written under ./runs/.")
            if not args.no_open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nStopping LABrador demo orchestrator.")
            finally:
                server.server_close()
            return 0
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
