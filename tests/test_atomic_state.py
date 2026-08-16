from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labrador_orchestrator.contracts import dump_json_atomic
from tests._support import FixtureProject


class AtomicStateTests(unittest.TestCase):
    def test_concurrent_reader_never_observes_partial_manifest(self) -> None:
        with FixtureProject() as fixture:
            path = fixture.root / "runs" / "atomic" / "manifest.json"
            dump_json_atomic(path, {"revision": 0, "padding": "x" * 65536})
            failures: list[BaseException] = []
            observed: list[int] = []
            finished = threading.Event()

            def writer() -> None:
                try:
                    for revision in range(1, 101):
                        dump_json_atomic(
                            path, {"revision": revision, "padding": str(revision) * 32768}
                        )
                except BaseException as error:  # pragma: no cover - retained for thread handoff
                    failures.append(error)
                finally:
                    finished.set()

            def reader() -> None:
                try:
                    while not finished.is_set():
                        value = json.loads(path.read_text(encoding="utf-8"))
                        observed.append(value["revision"])
                    observed.append(json.loads(path.read_text(encoding="utf-8"))["revision"])
                except BaseException as error:  # pragma: no cover - retained for thread handoff
                    failures.append(error)

            writer_thread = threading.Thread(target=writer)
            reader_thread = threading.Thread(target=reader)
            reader_thread.start()
            writer_thread.start()
            writer_thread.join(timeout=5)
            reader_thread.join(timeout=5)

            self.assertFalse(failures, failures)
            self.assertTrue(observed)
            self.assertEqual(observed[-1], 100)
            self.assertTrue(
                all(left <= right for left, right in zip(observed, observed[1:], strict=False))
            )


if __name__ == "__main__":
    unittest.main()
