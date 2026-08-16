# Module contracts and demo operating status

This is the operator-facing contract for the five-stage local demo. The machine registry is
`module-lock.json`. Run only its pinned commits; do not pull moving default branches during a
rehearsal or presentation.

## What is normative

Behavior is defined by these boundaries, in order:

1. `module-lock.json` pins each repository, commit, schema path, execution mode, argument array,
   timeout, and fallback artifact.
2. The JSON Schemas named in that registry define native module input and output shape.
3. `src/labrador_orchestrator/setup_contract.py` enforces the exact versioned setup and program
   frame contracts, and `src/labrador_orchestrator/adapters.py` defines the permitted transforms
   between those boundaries.

Repository examples, `fixtures/golden/`, and objects created inside `tests/` are
**non-normative**. They may demonstrate or test one valid payload, but their values do not become
defaults, mappings, or scientific facts for another program. Preflight checks that an example is
schema-valid; it does not grant that example semantic authority.

The golden files are an explicit exception only for the named `golden.ra-irak4.v1` profile. Their
operational use is profile-bound and truth-labeled; they still do not define the general contract.

## Pinned modules

| Order | UI stage | Repository and commit | Current mode | Operator meaning |
| --- | --- | --- | --- | --- |
| 1 | Biomarker / evidence | `REagent-LABrador/research-evidence-mapper` at `551a5a8c4c7ff4143c21dab2e37519ac9f482c04` | **Replay** | Executes the pinned module assembler over the recorded IRAK4 graph. Execution completes, but scientific origin remains `CACHED`; no new literature search is claimed. |
| 2 | Hypothesis | `REagent-LABrador/Hypothesis_Generator` at `80cf2524372003b96b39106dc5487b1f98330fd3` | **Auto; profile-only fallback** | Runs one deterministic no-model selection and emits up to three candidates through the official cards adapter. |
| 3 | Recruitability | `REagent-LABrador/clinical_simulation` at `1d417b43ee8cb3b20a0769eedfc8e97474d65e30` | **Replay** | Identity-checks the exact RA thesis/result pair. Execution completes with `CACHED` scientific origin; `score` is recruitability, not probability of approval. |
| 4 | ROI | `REagent-LABrador/rnpv-roi-calculator` at `0a0abd5b6372969bf186d7d2cb496e898cf9703b` | **Auto; profile-only fallback** | Runs locally from its locked Python environment. Transport `status: "ok"` can coexist with `NOT_DECISION_GRADE`. |
| 5 | Simulation / tractability | `REagent-LABrador/simulation` at `63ed6e560e7c0e66e51085e9140fdcc28c7ca64d` | **Replay** | Runs the module's dependency-light local cache resolver. The exact golden request resolves to its bundled IRAK4 dossier; origin remains `CACHED` and this is not atomistic simulation. |

`auto` means attempt the pinned command and validate the output. It does **not** mean every request
may use the configured fallback. All current cached/fallback artifacts belong to the golden
profile and are available only when resolved setup has `fallbackPolicy: "PROFILE_MATCH_ONLY"`.

## Native schema boundaries

Paths are relative to the orchestrator root. Paths under `.modules/` exist after bootstrap.

| Stage | Input schema | Output schema |
| --- | --- | --- |
| Evidence | `schemas/evidence-input.schema.json` (`urn:reagent-labrador:orchestrator:evidence-request:1`) | `.modules/research-evidence-mapper/schema/graph.schema.json` |
| Hypothesis | `.modules/Hypothesis_Generator/schemas/knowledge-graph.schema.json` | `.modules/Hypothesis_Generator/schemas/cards.schema.json` (`https://labrador.dev/schemas/cards.schema.json`) |
| Recruitability | `.modules/clinical_simulation/schemas/input.schema.json` (`https://github.com/REagent-LABrador/clinical_simulation/schemas/input.schema.json`) | `.modules/clinical_simulation/schemas/output.schema.json` (`https://github.com/REagent-LABrador/clinical_simulation/schemas/output.schema.json`) |
| ROI | `.modules/rnpv-roi-calculator/schemas/input.schema.json` (`urn:reagent-labrador:rnpv_roi_calculator:input:1.0.0`) | `.modules/rnpv-roi-calculator/schemas/output.schema.json` (`urn:reagent-labrador:rnpv_roi_calculator:output:1.0.0`) |
| Tractability | `.modules/simulation/schemas/input.schema.json` (`https://github.com/REagent-LABrador/simulation/schema/input.schema.json`) | `.modules/simulation/schemas/output.schema.json` (`https://github.com/REagent-LABrador/simulation/schema/output.schema.json`) |

All five boundaries use JSON Schema Draft 2020-12.

Interpretability is projection data, not a sequencing dependency. The orchestrator preserves a
module's native result and transports an optional top-level `interpretability` object unchanged
inside the functional frontend's station payloads. At this snapshot evidence, recruitability,
ROI, and tractability emit shared contract version `1.0.0`. Hypothesis interpretability is a
separate generated cards artifact and is not yet part of the sequential runner.

## Versioned setup contracts

### Legacy golden request: `labrador.run-setup.v1`

An unversioned body containing exactly these six fields is treated as the legacy v1 request:

```json
{
  "clinicalIndication": "Rheumatoid arthritis",
  "biomarkerRange": [1, 10],
  "maxBiomarkers": 3,
  "maxLiteraturePapers": 40,
  "hypothesisRange": [1, 10],
  "maxHypothesesPerBiomarker": 3
}
```

This path accepts only rheumatoid arthritis (`RA` is normalized as an alias). It resolves to:

- `schemaVersion: "labrador.resolved-run-setup.v2"`
- `requestSchemaVersion: "labrador.run-setup.v1"`
- `profileRef: "golden.ra-irak4.v1"`
- `fallbackPolicy: "PROFILE_MATCH_ONLY"`
- a `labrador.program-frame.v1` with basis `PROFILE_FIXTURE`, IRAK4, UniProt `Q9NWZ3`, and
  small-molecule modality

The profile contains explicit RA/IRAK4 analyst assumptions. Its clinical thesis comes from the
profile fixture. Its ROI input is built from the synthetic golden ROI template and the available
recruitability delay. None of those values are defaults for a custom program.

### Analyst request: `labrador.run-setup.v2`

The v2 body has exactly `schemaVersion`, `exploration`, and `program`. `program` contains exactly
one of `frame` or `profileRef`. The only recognized profile is `golden.ra-irak4.v1`, and a v2
profile request must carry the exact profile evidence request; a different request is rejected as
`PROFILE_REQUEST_MISMATCH`.

For a new program, use an inline frame:

```json
{
  "schemaVersion": "labrador.run-setup.v2",
  "exploration": {
    "evidenceRequest": {
      "ask": "new_question",
      "target": "Analyst-authored scientific question",
      "depth": "standard"
    },
    "hypothesis": {"boldnessRange": [1, 10]},
    "presentation": {
      "biomarkerRange": [1, 10],
      "maxBiomarkers": 3,
      "maxLiteraturePapers": 40,
      "maxHypothesesPerBiomarker": 3
    }
  },
  "program": {
    "frame": {
      "schemaVersion": "labrador.program-frame.v1",
      "frameId": "analyst-program.v1",
      "basis": "ANALYST_SUPPLIED",
      "identity": {
        "programId": "PROGRAM-001",
        "displayName": "Analyst program",
        "indication": "Analyst indication",
        "targetSymbol": "TARGET",
        "uniprotAccession": null,
        "modality": "other"
      },
      "clinicalThesis": null,
      "plannedEnrollmentMonths": null,
      "simulationContext": {"interactionToDisrupt": null},
      "roiRequest": null,
      "notes": []
    }
  }
}
```

This is a contract-valid abstaining frame, not a claim that the current cached evidence and
tractability modules can execute it. Replace `clinicalThesis` with a complete payload valid under
the clinical input schema and `roiRequest` with a complete payload valid under the ROI input
schema when those analyses are requested. The orchestrator validates both native payloads before
creating a run; it does not fill them from examples.

Inline frame invariants:

- `basis` must be `ANALYST_SUPPLIED`.
- `identity` has exactly `programId`, `displayName`, `indication`, `targetSymbol`,
  `uniprotAccession`, and `modality`. Only the accession may be null.
- `clinicalThesis` is either a native clinical request object or null.
- `roiRequest` is either a native ROI request object or null.
- `plannedEnrollmentMonths` is a positive number or null.
- For inline v1 frames, `simulationContext` contains exactly `interactionToDisrupt`, which is a
  string or null.
- Clinical target, indication, modality, and supplied accession must match frame identity. ROI
  program ID, target, modality, and named indication must also match. A mismatch is rejected as
  `FRAME_IDENTITY_MISMATCH` before a run directory is created.

## Adapter and abstention boundaries

| Stage | Golden profile input | Inline analyst-frame input | Honest abstention |
| --- | --- | --- | --- |
| Evidence | Profile evidence request | `exploration.evidenceRequest`, validated against the evidence request schema | The v2 field is required; no repository example is substituted |
| Hypothesis | Full evidence graph | Full upstream evidence graph, unchanged | Missing evidence output gives `SKIPPED / MISSING_UPSTREAM_OUTPUT` |
| Recruitability | Profile clinical thesis | Native `program.frame.clinicalThesis`, unchanged | Null gives `SKIPPED / MISSING_CLINICAL_THESIS` |
| ROI | Synthetic profile template plus modeled recruitment delay | Native `program.frame.roiRequest`; the only optional overlay is a supplied recruitment-delay structure when both clinical output and `plannedEnrollmentMonths` exist | Null gives `SKIPPED / MISSING_ROI_REQUEST` |
| Tractability | Profile identity and simulation context | Constructed from explicit frame identity/context: resolved UniProt accession, indication, and interaction | Null accession gives `SKIPPED / MISSING_UNIPROT_ACCESSION` |

Recruitability-to-ROI uses enrollment time, never the recruitability score. For the golden profile,
delay is `max(0, round((simulated_months_to_enroll - 18) / 12))`. For an inline frame, the exact
month overrun relative to `plannedEnrollmentMonths` is written only when the native ROI request
already contains `execution.simulation_assumptions.launch_delay_years`; the adapter does not
invent that structure.

### No fallback crossing between programs

An inline analyst frame resolves with `profileRef: null` and `fallbackPolicy: "DISABLED"`.
Therefore:

- a failed live command cannot load an RA/IRAK4 fallback;
- a replay command cannot promote an RA/IRAK4 artifact whose native input identity differs;
- the stage is marked `FAILED / NO_MATCHING_CACHED_ARTIFACT`, with `output_origin: "NOT_RUN"`;
- an absent optional native input is instead an explicit `SKIPPED` stage with its own reason code.

The three replay artifacts are bound to `golden.ra-irak4.v1`. They refuse every custom frame until
a live adapter or an artifact explicitly bound to that frame is added. This is intentional
abstention, not a generalized end-to-end success claim.

## Module commands

Bootstrap and preflight from the orchestrator root:

```bash
uv sync
uv run python scripts/bootstrap.py
uv run python app.py preflight
```

Setup and execution argument arrays are also recorded verbatim in `module-lock.json`.

### Evidence mapper

The golden command runs `scripts/run_evidence_replay.py`, which verifies the exact recorded request and
executes the pinned repository's `skills/graph-assembly/assemble.py --rebuild`. The resulting
graph is schema-validated and semantically identical to the recorded search. This executes local
module code but does not perform a new Paperclip/Anthropic literature search.

### Hypothesis generator

```bash
cd .modules/Hypothesis_Generator
uv sync --extra dev
cd ../..
python3 scripts/run_hypothesis.py \
  --module-root .modules/Hypothesis_Generator \
  --input fixtures/golden/fallbacks/evidence.json \
  --output runs/manual-hypothesis.json
```

The wrapper runs the deterministic generator once, retains the first three selected structural
candidates, and emits the module's official `cards.json` WebPayload with shared interpretability.
For the 3×3 judging view, the orchestrator contextualizes those three unchanged source cards under
each of the three graph-grounded readout candidates. The resulting nine branch IDs are UI/program
contexts, not nine HypGen executions or nine independently generated hypotheses.
The UI's 1–10 boldness interval maps its midpoint to generator craziness with
`(midpoint - 1) / 9`; this changes search posture, not evidence quality.

### Recruitability

The judging command runs `scripts/run_clinical_replay.py`. It normalizes only the two optional
null fields, requires the native input to equal the recorded output echo, and promotes the exact
schema-valid RA result. `score` is operational recruitability from modeled enrollment time. It
is not probability of technical success or approval. A fresh native run additionally requires
the producer's Anthropic/registry access.

### ROI calculator

```bash
cd .modules/rnpv-roi-calculator
uv sync --locked --extra dev --no-editable
.venv/bin/rnpv-roi run \
  --input ../../fixtures/golden/roi-input-template.json \
  --output ../../runs/manual-roi.json
```

Transport `status: "ok"` means calculation completed. A payload marked `NOT_DECISION_GRADE` is a
successful module execution with insufficient or synthetic decision evidence, not a transport or
process failure.

### Simulation / tractability

The module's published native interface is:

```bash
cd .modules/simulation
python3 -m pip install -r simulation/requirements.txt
python3 -m simulation run \
  --input ../../fixtures/golden/simulation-input.json \
  --output ../../runs/manual-simulation.json
```

The orchestrator runs that capability through `scripts/run_simulation_replay.py`, which first
requires the exact recorded request and then rebuilds interpretability with the pinned module
code. It runs locally without Bun, cloud credentials, or the former monorepo support tree. For
the golden request it records an exact hit against the module's bundled dossier cache, so
execution is `COMPLETE` while scientific origin remains `CACHED`. Retrieved precedent and
computed tractability are separate axes and must not be averaged into one score.

## Observed rehearsal snapshot

The golden run recorded on this Mac on 2026-08-16 ended
`COMPLETED_WITH_WARNINGS`:

| Module | Stage status | Execution | Origin | Reason |
| --- | --- | --- | --- | --- |
| Evidence | `COMPLETE_WITH_WARNINGS` | `COMPLETE` | `CACHED` | Pinned mapper graph rebuilt and revalidated; no new search |
| Hypothesis | `COMPLETE` | `COMPLETE` | `LIVE` | Three validated deterministic cards |
| Clinical | `COMPLETE_WITH_WARNINGS` | `COMPLETE` | `CACHED` | Exact recorded RA thesis/result replayed |
| ROI | `COMPLETE` | `COMPLETE` | `LIVE` | Validated local output; still `NOT_DECISION_GRADE` |
| Tractability | `COMPLETE_WITH_WARNINGS` | `COMPLETE` | `CACHED` | Exact dossier revalidated and interpretability rebuilt |

This is evidence about that rehearsal, not a guarantee about another machine or future checkout.
Always inspect the new run's `manifest.json`.

## Repair blockers and demo cut line

- Custom v2 programs cannot complete evidence, recruitment, or tractability with the current
  replay registry: the golden-profile artifacts are correctly refused on identity mismatch.
- Bun 1.3.14 is installed and the pinned Bun dependencies pass setup. Fresh managed-agent calls
  remain unavailable without the producer-owned Anthropic credentials.
- The evidence mapper now publishes its graph schema, but still needs a stable local
  file-in/file-out command and orchestrator request boundary before it can replace the profile
  cache.
- The simulation repository now resolves bundled dossiers locally. A request without a matching
  dossier still needs a separate live-science path; the golden cache must not be generalized.
- The browser currently creates the legacy golden request. Custom v2 requests are available via
  the API or `uv run python app.py run --setup path/to/run-setup.v2.json`.
- The Highlander repository has landed, but this narrow functional slice does not invoke its
  packet-comparison CLI. The current frontend comparison remains client-side while the five-stage
  run is tested and debugged; server-side Highlander interoperability is a separate follow-up.
- Tractability is not atomistic simulation. Do not relabel pocket and precedent evidence as
  molecular dynamics, binding free energy, or atomistic support.
- Never add API keys, tokens, credential files, or raw secret-bearing stderr to `runs/` or version
  control. Environment setup stays outside run artifacts.

Before presenting, confirm every stage in the new run manifest shows execution status, output
origin (`LIVE`, `CACHED`, `DEMO_FALLBACK`, or `NOT_RUN`), pinned commit, warnings, and artifact
paths.
