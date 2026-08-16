# LABrador demo orchestrator

A local, one-user integration runner for the five repositories in the
`REagent-LABrador` GitHub organization. It serves the interactive three-screen HTML mockup,
runs the modules sequentially, validates every module boundary, and projects their mixed
outputs into one stable UI contract.

This is deliberately demo infrastructure, not a production platform. It has no auth, accounts,
queue, database, or cloud deployment, and it binds to `127.0.0.1` by default.

## Two supported request paths

- The existing browser and an unversioned six-field request use the frozen
  `golden.ra-irak4.v1` rheumatoid-arthritis / IRAK4 presentation profile. Only that exact profile
  may use its labeled cached or fallback artifacts.
- `labrador.run-setup.v2` accepts an inline, analyst-supplied
  `labrador.program-frame.v1` for another target and indication. Clinical and ROI inputs are
  native module payloads in that frame, not values inferred from repository examples.

The module JSON Schemas referenced by `module-lock.json`, together with the exact versioned
setup/frame validation in `src/labrador_orchestrator/setup_contract.py`, define accepted
behavior. Repository examples, golden payloads, and test fixtures are **non-normative**: they
show or exercise particular payloads but must never be mined for defaults for another program.

See [docs/MODULE_CONTRACTS.md](docs/MODULE_CONTRACTS.md) for the complete boundary contract.

## What actually ran on this Mac

The recorded golden rehearsal on 2026-08-16 completed with warnings:

| UI stage | Observed result | Meaning |
|---|---|---|
| Biomarker / evidence | `CACHED`; execution `SKIPPED` | Validated pinned IRAK4 graph; the mapper was not invoked |
| Hypothesis | `LIVE`; execution `COMPLETE` | Local deterministic no-model dry run |
| Recruitability | `DEMO_FALLBACK`; execution `FAILED` | Bun was absent, so the configured live command could not start |
| ROI | `LIVE`; execution `COMPLETE` | Local calculator completed; the synthetic result remains `NOT_DECISION_GRADE` |
| Simulation / tractability | `CACHED`; execution `SKIPPED` | Validated dossier; not an atomistic simulation |

The backend executes recruitability before ROI so modeled enrollment delay can populate an ROI
scenario when the supplied ROI structure has that field. The browser preserves the PRD's visual
order, where ROI appears above recruitability.

## Quick start

Requirements: Git, Python 3.11 or 3.12, and [uv](https://docs.astral.sh/uv/). Bun is optional for
the named golden rehearsal because that profile has a labeled recruitability fallback; it is not
optional for a live clinical invocation.

```bash
cd labrador-demo-orchestrator
uv sync
uv run python scripts/bootstrap.py
uv run python app.py preflight
uv run python app.py serve
```

Open <http://127.0.0.1:8765/>. The current browser flow submits the legacy golden profile. Keep
the indication as **Rheumatoid arthritis**, adjust either 1–10 range if desired, and choose
**Run RA / IRAK4 profile**.

For an offline UI-only rehearsal, open `ui/index.html` directly or add `?mode=fixture`. An HTTP
API failure never silently switches a live demonstration back to fixtures.

### Run the landed functional frontend

Keep the orchestrator running on port 8765, then serve the separate
`REagent-LABrador/frontend` checkout:

```bash
cd ../frontend
python3 -m http.server 4173 --bind 127.0.0.1 --directory app
```

Open
<http://127.0.0.1:4173/?backend=http&base=http://127.0.0.1:8765>.
The explicit `backend=http` query is required because that frontend otherwise starts in its
deterministic mock mode. Its current real path creates one RA / IRAK4 run, polls the five-stage
snapshot every five seconds, and renders each available native output under
`station_payloads` without renaming keys. A top-level module `interpretability` object therefore
reaches the node inspector unchanged when a module supplies one; its absence does not block the
run.

## Runs from the terminal

Run the legacy golden profile:

```bash
uv run python app.py run > /tmp/labrador-final-state.json
```

Run a versioned request from a file:

```bash
uv run python app.py run --setup path/to/run-setup.v2.json \
  > /tmp/labrador-final-state.json
```

Every run is written under `runs/LR-…/`:

```text
00_program_input.json
manifest.json
events.ndjson
01_evidence_mapper/{input,output}.json
02_hypothesis_generator/{input,output}.json
03_clinical_simulation/{input,output}.json
04_roi_calculator/{input,output}.json
05_simulation/{input,output}.json
highlander_packet.json        # after explicit launch
```

`manifest.json` is atomically replaced and revisioned. Execution status, output origin, result
basis, runtime maturity, UI freshness, and interaction state remain separate fields. A stage
without an honest input is recorded as `SKIPPED` with a reason code; it does not borrow an example.

## Local API

- `GET /api/health`
- `GET /api/meta`
- `GET /api/modules/preflight`
- `POST /api/runs`
- `GET /api/runs/{runId}/state`
- `GET /api/runs/{runId}/snapshot`
- `POST /api/runs/{runId}/highlander`

Create a legacy golden run:

```bash
curl -sS http://127.0.0.1:8765/api/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: rehearsal-1' \
  --data '{
    "clinicalIndication":"Rheumatoid arthritis",
    "biomarkerRange":[1,10],
    "maxBiomarkers":3,
    "maxLiteraturePapers":40,
    "hypothesisRange":[1,10],
    "maxHypothesesPerBiomarker":3
  }'
```

For a custom program, post a `labrador.run-setup.v2` body with exactly one inline
`program.frame`; the full shape and abstention rules are documented in
[docs/MODULE_CONTRACTS.md](docs/MODULE_CONTRACTS.md). With the current lockfile, evidence and
tractability are configured as golden-profile caches, so those two stages will refuse a custom
frame until live adapters or frame-bound caches exist.

The bundled mockup receives `labrador.ui-run-state.v1`. The landed functional frontend receives
the snake-case `/snapshot` projection and treats each `station_payloads` value as an opaque,
verbatim native result; it does not adapt the five module schemas itself.

## Verification

```bash
uv run python -m unittest discover -s tests -v
node ui/verify_mockup.mjs
```

Then run the terminal golden path and inspect the UI at 1440×900 and 1280×720. Confirm the
manifest labels each result `LIVE`, `CACHED`, `DEMO_FALLBACK`, or `NOT_RUN`; do not infer origin
from a green stage card.
