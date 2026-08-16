# LABrador demo orchestrator

A local, one-user integration runner for the five repositories in the
`REagent-LABrador` GitHub organization. One Python process serves the functional three-screen
frontend and its API, runs the modules sequentially, validates every module boundary, and
projects their mixed outputs into one stable UI contract.

This is deliberately demo infrastructure, not a production platform. It has no auth, accounts,
queue, database, or cloud deployment, and it binds to `127.0.0.1` by default.

## Three supported request paths

- The existing browser and an unversioned six-field request use the frozen
  `golden.ra-irak4.v1` rheumatoid-arthritis / IRAK4 presentation profile. Only that exact profile
  may execute its labeled, profile-bound replay artifacts.
- `labrador.run-setup.v2` accepts an inline, analyst-supplied
  `labrador.program-frame.v1` for another target and indication. Clinical and ROI inputs are
  native module payloads in that frame, not values inferred from repository examples.
- `labrador.run-setup.v3` is the isolated scientific branch runner. It runs evidence once,
  selects real `biomarker` nodes followed by evidence-supported `process` nodes, and invokes one
  focused HypGen run per node. Each branch then invokes clinical, tractability, and ROI without
  substituting a fixture when a live node fails. A failed node is persisted as
  `CANNOT_COMPLETE` and other branches continue.

The v3 request keeps an explicit scientific program frame separate from the valuation frame.
Process nodes are labeled as mechanistic/PD readouts rather than biomarkers. `LIVE` and `REPLAY`
are explicit request modes and never fall through to one another. The checked-in module lock uses
the doubled ceilings: mapper and HypGen 20 minutes, clinical 10 minutes, tractability 90 minutes,
ROI 4 minutes, and Highlander 4 minutes.

The module JSON Schemas referenced by `module-lock.json`, together with the exact versioned
setup/frame validation in `src/labrador_orchestrator/setup_contract.py`, define accepted
behavior. Repository examples, golden payloads, and test fixtures are **non-normative**: they
show or exercise particular payloads but must never be mined for defaults for another program.

See [docs/MODULE_CONTRACTS.md](docs/MODULE_CONTRACTS.md) for the complete boundary contract.

## What actually runs on this Mac

The recorded golden rehearsal on 2026-08-16 completed with warnings:

| UI stage | Observed result | Meaning |
|---|---|---|
| Biomarker / evidence | `CACHED`; execution `COMPLETE` | The pinned mapper assembler rebuilds and revalidates the recorded IRAK4 search graph; no new literature search is claimed |
| Hypothesis | `LIVE`; execution `COMPLETE` | One local deterministic no-model run emits three candidates through HypGen's official cards adapter |
| Recruitability | `CACHED`; execution `COMPLETE` | The exact RA thesis/result pair is identity-checked and replayed; no fresh registry/model retrieval is claimed |
| ROI | `LIVE`; execution `COMPLETE` | Local calculator completed; the synthetic result remains `NOT_DECISION_GRADE` |
| Simulation / tractability | `CACHED`; execution `COMPLETE` | The pinned module's local resolver returns the exact bundled IRAK4 dossier and records the cache hit; this is not an atomistic simulation |

The backend executes recruitability before ROI so modeled enrollment delay can populate an ROI
scenario when the supplied ROI structure has that field. The browser preserves the PRD's visual
order, where ROI appears above recruitability.

## Quick start

Requirements: Git, Python 3.11 or 3.12, and [uv](https://docs.astral.sh/uv/). Bun is optional for
the judging profile because its evidence and recruitment paths are Python-only recorded replays;
when Bun is present, bootstrap also installs those repositories' development dependencies. Fresh
remote evidence/recruitment work still requires the producer-owned credentials and services; the
judging profile deliberately uses truthful local replays when those are unavailable.

```bash
cd labrador-demo-orchestrator
uv sync
uv run python scripts/bootstrap.py
uv run python app.py preflight
uv run python app.py serve
```

Open <http://127.0.0.1:8787/>. The browser submits the legacy golden profile. Keep the indication
as **Rheumatoid arthritis**, adjust either 1–10 range if desired, and choose
**Run local exploration**.

Bootstrap pins the functional frontend under `.frontend/`; no Bun frontend server is needed.
For an offline UI-only rehearsal of the earlier wireframe, open `ui/index.html` directly. An HTTP
API failure never silently switches a live demonstration back to fixtures.

### Optional split-process frontend development

Keep the orchestrator running on port 8787, then serve a separate working checkout of
`REagent-LABrador/frontend`:

```bash
cd ../frontend
python3 -m http.server 4173 --bind 127.0.0.1 --directory app
```

Open
<http://127.0.0.1:4173/?base=http://127.0.0.1:8787>. Its real path creates one RA / IRAK4 run,
polls the five-stage snapshot every five seconds, and renders each native output without
renaming keys. Evidence uses `biomarkers[].station_payload`; all five stage outputs also remain
under each program's `station_payloads`. A top-level module `interpretability` object reaches the
readable node panel and remains available as verbatim JSON; its absence does not block the run.

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

Run the multi-branch scientific path after bootstrapping draft-compatible producer pins:

```bash
uv run python app.py run --setup fixtures/scientific/run-setup.v3.json \
  > /tmp/labrador-scientific-final-state.json
```

Its artifacts live under `runs/LR-…/scientific/` and `runs/LR-…/branches/`. Every node records
the exact producer SHA, native input/output, hashes, origin, runtime, and terminal reason. The
snapshot endpoint returns `labrador.scientific-snapshot.v1` for these runs. Representative demo
presentation is a watermark-only UI mode and cannot change the scientific artifact hashes.

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
curl -sS http://127.0.0.1:8787/api/runs \
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
[docs/MODULE_CONTRACTS.md](docs/MODULE_CONTRACTS.md). The evidence, recruitment, and tractability
replays are bound to the golden profile and reject a custom frame until live adapters or
frame-bound artifacts exist.

The golden snapshot contains three graph-grounded RA mechanistic/PD readout candidates. HypGen's
three real structural candidates are contextualized beneath each readout, filling all nine fixed
lanes. These are three native candidates projected into nine contexts, not nine independent
generations. Recruitment, ROI, and tractability are shared RA analyst-frame records and are
explicitly labeled `NOT_CANDIDATE_SPECIFIC`; no proxy programs are added.

For the golden judging profile only, `/snapshot` also carries a versioned
`REPRESENTATIVE_DEMO_SCENARIO_V1` display overlay. It provides distinct values for all nine
biomarker–hypothesis contexts so the graph and client-side Pareto comparison are legible. These
values are explicitly `NOT_NATIVE_MODULE_OUTPUT`; native metrics, station artifacts, hashes, and
execution truth remain unchanged. Custom frames never receive this overlay.

The bundled wireframe receives `labrador.ui-run-state.v1`. The functional frontend receives the
snake-case `/snapshot` projection. It renders the shared interpretability contract when present
and preserves every native station result unchanged in the run artifact. The judging UI projects
readable interpretation and representative display values without rewriting native scientific
outputs or manufacturing atomistic evidence.

Screen 3 currently performs an advisory three-dimensional Pareto comparison in the browser. It
maps every plan by P50 rNPV, recruitability, and simulation / tractability; any missing axis stays
incomparable instead of being imputed. In the golden RA profile, the Z axis uses the explicitly
labeled representative branch-context fit while the native cached dossier remains shared across
plans. The landed `hypothesis-highlander` packet consumer is pinned as `NOT_WIRED` and is not
invoked or presented as integrated in this judging slice.

## Verification

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
node ui/verify_mockup.mjs
node .frontend/verify_functional_app.mjs
```

Then run the terminal golden path and inspect the UI at 1440×900 and 1280×720. Confirm all five
stages have `execution_status: COMPLETE`; evidence, recruitment, and tractability retain
`output_origin: CACHED`, while hypothesis and ROI are `LIVE`. Do not infer scientific freshness
from a green stage card.
