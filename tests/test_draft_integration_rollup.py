from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_draft_rollup_pins_every_compatible_draft_and_doubled_timeout() -> None:
    lock = _load("module-lock.json")
    modules = {module["id"]: module for module in lock["modules"]}

    assert lock["contracts"]["commit"] == "755499b42ab65d3b01f959b11624dd4e61bdd561"
    assert lock["frontend"]["commit"] == "95c9de4f3871f14108b3a595579cd991fc26c7ef"
    assert lock["portfolio_consumer"]["commit"] == (
        "e1cdbf317970ad61d21536cb0b271ec563856023"
    )
    assert lock["portfolio_consumer"]["timeout_seconds"] == 240

    expected = {
        "evidence_mapper": ("9faa0ffaa158c10495245e084ff2dce72dedf506", 1200),
        "hypothesis_generator": ("02ea1441412c556fdaf80a1264bbe605d0133ceb", 1200),
        "clinical_simulation": ("13783c962d303a04ff63c7dbb59e49b4369038c1", 600),
        "roi_calculator": ("29bf59ea5f64e0f68a58a2e35595f471b0c73311", 240),
        "simulation": ("a0b3d1f56805d9c9e0550291123064e492a16e5b", 5400),
    }
    assert set(modules) == set(expected)
    for module_id, (commit, timeout) in expected.items():
        module = modules[module_id]
        assert module["commit"] == commit
        assert module["timeout_seconds"] == timeout
        assert module["live_command"]
        assert module["replay_command"]
        assert module["scientific_input_schema"]
        assert module["scientific_output_schema"]

    # Provider-backed live commands are explicit and cannot select a replay wrapper.
    assert "run_evidence_replay.py" not in " ".join(modules["evidence_mapper"]["live_command"])
    assert "--mode live" in " ".join(modules["hypothesis_generator"]["live_command"])
    assert "run_clinical_replay.py" not in " ".join(modules["clinical_simulation"]["live_command"])
    assert "--mode live" in " ".join(modules["simulation"]["live_command"])


def test_scientific_replay_fixture_matches_the_identity_guarded_evidence_request() -> None:
    setup = _load("fixtures/scientific/run-setup.v3.json")
    recorded = _load("fixtures/golden/evidence.request.json")
    assert setup["execution"] == {
        "mode": "REPLAY",
        "presentationMode": "SCIENTIFIC",
    }
    assert setup["exploration"]["evidenceRequest"] == recorded
    assert setup["exploration"]["focus"]["maxBranches"] == 3
