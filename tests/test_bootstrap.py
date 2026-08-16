from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bootstrap

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_pinned_frontend_contains_the_landed_cosmetic_surface(self) -> None:
        lock = json.loads((ROOT / "module-lock.json").read_text(encoding="utf-8"))
        frontend = lock["frontend"]
        checkout = ROOT / frontend["module_root"]
        actual_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        html = (checkout / "app" / "index.html").read_text(encoding="utf-8")

        self.assertEqual(actual_commit, frontend["commit"])
        self.assertNotIn('class="app-header"', html)
        self.assertIn('class="metric-button"', html)
        self.assertNotIn('<select id="metric-biomarker"', html)

    def test_main_clones_the_pinned_frontend_alongside_the_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = {
                "frontend": {
                    "id": "frontend",
                    "repository_url": "https://example.invalid/frontend.git",
                    "commit": "frontend-sha",
                    "module_root": ".frontend",
                },
                "portfolio_consumer": {
                    "id": "hypothesis_highlander",
                    "repository_url": "https://example.invalid/highlander.git",
                    "commit": "highlander-sha",
                    "module_root": ".modules/hypothesis-highlander",
                },
                "modules": [
                    {
                        "id": "hypothesis_generator",
                        "repository_url": "https://example.invalid/hypothesis.git",
                        "commit": "hypothesis-sha",
                        "module_root": ".modules/hypothesis",
                    },
                    {
                        "id": "roi_calculator",
                        "repository_url": "https://example.invalid/roi.git",
                        "commit": "roi-sha",
                        "module_root": ".modules/roi",
                    },
                ],
            }
            (root / "module-lock.json").write_text(json.dumps(lock), encoding="utf-8")

            with (
                patch.object(bootstrap, "ROOT", root),
                patch.object(bootstrap.shutil, "which", return_value="available"),
                patch.object(
                    bootstrap,
                    "clone_or_verify",
                    side_effect=lambda item: root / str(item["module_root"]),
                ) as clone,
                patch.object(bootstrap, "run"),
            ):
                self.assertEqual(bootstrap.main(), 0)

            self.assertEqual(
                [call.args[0]["id"] for call in clone.call_args_list],
                [
                    "frontend",
                    "hypothesis_highlander",
                    "hypothesis_generator",
                    "roi_calculator",
                ],
            )

    def test_main_keeps_replay_profile_available_without_bun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = {
                "frontend": {
                    "id": "frontend",
                    "repository_url": "https://example.invalid/frontend.git",
                    "commit": "frontend-sha",
                    "module_root": ".frontend",
                },
                "portfolio_consumer": {
                    "id": "hypothesis_highlander",
                    "repository_url": "https://example.invalid/highlander.git",
                    "commit": "highlander-sha",
                    "module_root": ".modules/hypothesis-highlander",
                },
                "modules": [
                    {
                        "id": "hypothesis_generator",
                        "repository_url": "https://example.invalid/hypothesis.git",
                        "commit": "hypothesis-sha",
                        "module_root": ".modules/hypothesis",
                    },
                    {
                        "id": "roi_calculator",
                        "repository_url": "https://example.invalid/roi.git",
                        "commit": "roi-sha",
                        "module_root": ".modules/roi",
                    },
                ],
            }
            (root / "module-lock.json").write_text(json.dumps(lock), encoding="utf-8")

            with (
                patch.object(bootstrap, "ROOT", root),
                patch.object(
                    bootstrap.shutil,
                    "which",
                    side_effect=lambda command: None if command == "bun" else "available",
                ),
                patch.object(
                    bootstrap,
                    "clone_or_verify",
                    side_effect=lambda item: root / str(item["module_root"]),
                ),
                patch.object(bootstrap, "run") as run,
            ):
                self.assertEqual(bootstrap.main(), 0)

            self.assertTrue(all(call.args[0][0] != "bun" for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
