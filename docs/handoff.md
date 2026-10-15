# FlyCode handoff brief

Authoritative roadmap: `docs/Plan.md` (§1–99). Operating rules: `AGENTS.md`.
The baseline MVP covers Phase 4 (§78). Phase 5 (§79) now runs the same tasks
with a verified, sparse biological graph. Hermes in the roadmap means FlyCode
itself: connected agents are hosted here and orchestrated by the controller.

## Current extension: real FlyWire controller

- `--brain full` uses `FullConnectomeBackend`, not the legacy random projection.
  Install `.[research]`, then `flycode connectome download` and
  `flycode connectome prepare --source ~/.cache/flycode/flywire-783`.
- Official FlyWire v783 source: https://zenodo.org/records/10676866, CC-BY-4.0.
  The two source files and prepared graph stay outside Git under that cache.
  Source MD5 checksums are verified before preprocessing.
- The prepared graph contains 139,255 proofread neurons and 2,700,513 directed
  edges after summing neuropil duplicates and applying a five-synapse threshold.
  Fingerprint: `7adea43f9f823703c8116a6321a78adfdc9aac9c14c80e04bfa6d991f8c6712f`.
  Topology: `00d1a94bd95f39f6fde20a6890cc680e538270c5620eadc53a89837d683b644a`.
- CSR recurrence uses A[target, source], three leaky tanh substeps, modeled
  transmitter signs and normalized incoming weights. These dynamics, seeded
  input/readout and explicit workflow prior are engineering approximations;
  they are not measured software-solving behavior of a biological brain.
- All activity remains inside the backend; JSON events expose 64 pooled values
  plus provenance and counts. The browser settings offer Functional Fly/FlyWire.
  Missing or corrupt data fails explicitly without a synthetic fallback.

Verified on 2026-10-15:

```text
Full pytest: 246 passed, 7 skipped in 68.77s.
Real graph toy repairs: 4 passed (two fixtures × bias/hard).
Real graph Docker/PostgreSQL repairs: 2 passed (bias/hard), 46.03s.
Actual browser FlyWire + local Qwen/Jev: verified success, 8 steps, 86.40s;
  Qwen=5 and Jev=5 actual requests, no model errors.
20 warmed controller steps: mean 6.31 ms, measured array storage 30.17 MiB.
Changed Python files: targeted Ruff passed; new graph modules: mypy passed.
```

The seven default skips are four explicit real-graph tests, two real-graph
Docker cases and one local-model test. The corresponding real runs above were
executed separately. No comparative advantage has been established.

## Current extension: local browser workspace

Implementation plan: `docs/superpowers/plans/2026-10-15-agent-interface.md`.

- Start `flycode ui --workspace . --port 8790`, then open the loopback URL.
  FastAPI serves packaged HTML/CSS/JavaScript; no Node runtime/build is needed.
- Russian task workspace: history/search, task composer, model settings,
  live activity timeline, candidate rationale/scores, expandable tool results,
  applied patch view, raw event journal and responsive inspector.
- Controller/model/tool events are emitted before and after actual work.
  Diagram highlights represent real component calls; confidence/risk come from
  NeuralState. Action rationales are not hidden model chain of thought.
- One background worker executes the existing agent. The web event loop stays
  responsive during blocking model/tool requests. Stop prevents new actions;
  an in-flight call is allowed to finish, including skipping Jev after a stop
  during Qwen inference. Already applied edits are preserved.
- Project paths are scoped to `--workspace`; fixtures always use fresh per-run
  directories. Foreign origins/hosts and mutations without a session token
  are rejected. Dynamic output uses text nodes and a strict local CSP.
- Persistent records/events default to `~/.local/share/flycode/ui`; override
  with `--data-dir`. SSE supports cursor replay and reconnect. On restart,
  unfinished records become interrupted. The 50 most recently saved records
  are loaded; on-disk history is retained. Browser output strings are clipped
  at 16,000 characters; regular execution telemetry is retained separately.
- `src/flycode/web/{app,runs}.py` owns the web service; `static/` owns the UI.
  Optional callbacks in `agent.py` and `llm/lmstudio.py` preserve CLI callers.

Verified on 2026-10-15:

```text
Browser acceptance: demo repair, patch view, replay on reload, history,
  literal untrusted task text, no page errors, 390px layout and inspector.
Actual browser task with local Qwen/Jev: verified success, 9 steps, 113.00s;
  Qwen=6 and Jev=6 requests, no model errors.
Full pytest: 226 passed, 1 skipped in 66.58s.
  (The opt-in LM Studio test is skipped; the actual browser model run above passed.)
Targeted Ruff and web module mypy: passed.
Built wheel includes the server and all four static assets.
```

Browser acceptance is development-only: install Playwright outside the project
(or in your development environment) and ensure Google Chrome is available.
Start an isolated UI with `--data-dir /tmp/flycode-ui-test`, then run:

```bash
FLYCODE_PLAYWRIGHT=/path/to/node_modules/playwright \
  node tests/browser/agent-ui.cjs
```

It defaults to `http://127.0.0.1:8790`; override with `FLYCODE_UI_URL`.
Screenshots default to `/tmp/flycode-ui-screenshots`.

## Current extension: local Qwen + Jev

Implementation plan: `docs/superpowers/plans/2026-10-15-lmstudio-dual-model.md`.

- `--llm lmstudio` selects `qwen/qwen3.5-9b` for candidate generation and
  `jev-style-qwen3.5-2b-decision` for native single-token option scoring.
- Both exact IDs were verified through the local LM Studio API and both models
  were loaded. No weights were downloaded, changed or trained.
- Routing consumes CortexContext.neural_state. Blocked states skip inference;
  verified completion and simple non-Compose VERIFY steps are local; confidence
  >= 0.8 and hard control skip Jev. Safe candidate scoring is advisory, and the
  existing safety mask and brain tournament retain final control.
- Qwen uses structured JSON, a 1536-token response cap and disabled reasoning
  output. Jev uses the installed v1 protocol with one token and top-20 logprobs.
  Missing/nonfinite decision distributions produce an explicit diagnostic and
  preserve Qwen proposals. Qwen errors stop without an invented repair.
- Requests have no automatic retries and a per-run shared call budget. Failed
  requests count. The local default timeout is 120 seconds: on this host a
  366-token edit proposal took about 58 seconds at roughly 7 tokens/second.
- Configure `--base-url`, `--model`, `--decision-model`, `--request-timeout`,
  `--max-model-calls`. Optional authentication is `LMSTUDIO_API_KEY`; the endpoint
  can be set with `LMSTUDIO_BASE_URL`. There is no cloud fallback or inherited
  proxy routing for local inference.
- Per-model counts/errors are recorded in RunMetrics, and each executed step
  includes a model_routing trace. Consolidation makes no additional request.
- The SDK floor is now `openai>=1.61` for the transport and request interfaces.

Verified on 2026-10-15:

```text
python -m pytest -q -> 206 passed, 1 skipped in 89.52s
  (the single skip is the explicit opt-in local-model acceptance)
FLYCODE_TEST_LMSTUDIO=1 python -m pytest tests/e2e/test_lmstudio.py -q
  -> 1 passed in 148.09s
Real CLI toy repair -> success=True, 6 steps; Qwen=3 calls, Jev=3 calls;
  no model errors; post-edit tests and STOP required no inference
Targeted Ruff on new routing/clients/CLI/telemetry/tests -> all checks passed
Repository-wide Ruff -> 57 existing findings
mypy src/flycode --ignore-missing-imports -> 20 errors in the same 8 files
```

Local inference and the wiring are verified. No improvement over the baseline
has been established. The separate real-graph extension above satisfies Phase 5.

## Phase status

| Phase | Plan | State |
|---|---|---|
| 0 SoftwareGym | §74 | Implemented and tested |
| 1 Autonomous agent | §75 | Toy repositories repaired end to end |
| 2 Baselines | §76 | Mock, FSM, random reservoir, generic RNN available |
| 3 Functional Fly | §77 | Implemented and tested |
| 4 Environment autonomy | §78 | Real autonomous Docker/Postgres acceptance passes in bias and hard control |
| 5 Real connectome | §79 | Accepted: real FlyWire graph passes toy and Docker tasks in both control modes |
| 6–8 | §80–82 | Offline training, reduction and distillation remain unfinished |
| 9 Candidate Tournament | §83 | Implemented: existing safety-masked hard/bias tournament tests pass |
| 10–14 | §84–88 | Bounded adaptation, experiments and rollout inside FlyCode remain unfinished |

## Run and verify

Use Python 3.12 and install the dependencies declared in `pyproject.toml`.
The local `.venv` now includes the dependencies needed by the Phase 4 checks,
plus Ruff and mypy. Environment heuristics use `PyYAML.safe_load`.

```bash
source .venv/bin/activate
python -m pytest -q
python -m pytest tests/e2e/test_environment_autonomy.py -q
PYTHONPATH=src python -m flycode.cli run \
  --repo /tmp/flycode-environment --fixture fastapi_postgres_app \
  --task "Fix this FastAPI application and get it fully running" \
  --brain functional --control bias --max-steps 60 --max-tool-calls 60
```

The demo requires Docker/Compose and a free host port 8000. Its Postgres is
internal to Compose, so host port 5432 need not be free. Fixture destinations
are recreated. The CLI leaves the repaired service running; stop it with
`docker compose --project-directory /tmp/flycode-environment down`.
The acceptance tests choose a free HTTP port and clean up their own containers.
On this machine Colima is running and the installed Compose plugin is linked
under `~/.docker/cli-plugins/`; other projects' containers were left running.

## Phase 4 implementation

- The offline cortex reads repository files and derives narrow repairs for
  documented ports, migration paths, environment references, routes and the
  demonstrated missing SQLAlchemy import. It never reads fixture defect metadata.
  General repairs still require `--llm openai` and an API key.
- Tests and Alembic accept a Compose `service` argument, preserving the runtime
  dependencies and environment defined by the target application.
- Compose `up` rebuilds and waits for health; `ps` parses actual container states.
  Command success alone does not imply a running stack. HTTP requires a 2xx
  response and failures include the URL and status.
- Completion requires fresh passing tests and every declared infrastructure
  check: running stack, PostgreSQL, migration at head, health and API URLs.
  Edits and stack lifecycle actions invalidate verification. Premature STOP
  is rejected and cannot report verified completion.
- Successful diagnostic output reaches working memory. Edited file snapshots
  are discarded so the cortex does not repeatedly apply a stale repair.
- Infrastructure errors clear on recovery; HTTP errors are tracked per URL.
  Ordinary tool-usage failures do not poison environment health.
- Environment-variable reference fixes preserve credential values and require
  verification; actual credential edits and replacement-based bypasses remain
  blocked by the safety mask.
- The fixture retains its intended defects. Unintended invalid logging setup
  was removed, and the DB no longer publishes a host port.

## Acceptance evidence

The real `run_agent` acceptance passed for both `BIAS_CONTROL` and
`HARD_CONTROL`. Each run repairs the initially broken fixture, checks Postgres,
executes the migration, passes both application tests, checks `/health` and
`/users`, and reaches STOP. A separate SQL query verifies Alembic revision `001`.
The toy CLI smoke test also passes (`success=True`, 9 steps).

Phase 4 baseline verification (2026-10-15):

```text
python -m pytest -q -> 166 passed in 55.01s; no skips
Ruff on the new environment module, environment tools, readiness checks,
Phase 4 acceptance and associated new tests -> all checks passed
Ruff across src/tests -> 58 remaining findings
mypy src/flycode --ignore-missing-imports -> 20 errors in 8 files
```

Repository-wide static checks are not clean. Remaining findings include unused
imports, duplicate definitions, import sorting, existing model/category typing,
brain state typing and the hard/bias tournament result type. They were not
silenced or folded into an unrelated cleanup. Raw diagnostics from this session
are in `/tmp/flycode-ruff.log` and `/tmp/flycode-mypy.log`.


## Remaining work after Phase 5

- Constrained training, reduced models, distillation, bounded online learning,
  causal evaluation and gradual controller rollout inside FlyCode.
- CLI `train`, `distill`, `benchmark`, `lesion`, `sensitivity` remain explicit
  stubs. `--brain reduced` is not supported; `--brain full` is operational.
- Stressors declared on TaskSpec are not wired into the loop: partial
  observations, delayed rewards, context reversal and noisy feedback.
- Benchmark coverage remains narrower than the research success criteria.
  Investigate baseline performance before making comparative capability claims.
- Pre-existing duplicate `toy_import_bug` definitions and duplicate backend test
  methods remain. No unrelated refactoring or cleanup was performed.
