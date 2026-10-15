# FlyCode handoff brief

Authoritative roadmap: `docs/Plan.md` (§1–99). Operating rules: `AGENTS.md`.
The baseline MVP covers Phase 4 (§78). Phase 5 (§79) now runs the same tasks
with a verified, sparse biological graph. Hermes in the roadmap means FlyCode
itself: connected agents are hosted here and orchestrated by the controller.

## Latest increment: adaptation, evaluation and shadow mode (2026-10-15)

Implementation plan: `docs/superpowers/plans/2026-10-15-adaptation-evaluation.md`.
Portable measurements: `docs/research-results/2026-10-15-adaptation.json`.

- `research/benchmark.py` implements isolated toy fixture/controller/checkpoint
  comparisons, explicit budgets, multiple seeds and controls, durable JSON
  configuration, per-case metrics/errors, controller/cost descriptions and
  actual trajectories. `benchmark --config docs/examples/benchmark.json
  --output PATH` runs Functional/FSM/RNN baselines. Compute is **not matched**.
- Research-only `TournamentCortex` preserves production proposals and adds
  read-only run_tests/git_diff alternatives during EXECUTE. Production cortex
  and brain priors are unchanged. Phase 6 now passes a limited task-level
  acceptance on this explicitly named probe, not on general repositories.
- v2 distillation uses 49 quadratic features and 784 readout parameters; v1
  25-feature/400-parameter checkpoints remain supported. Validation replays
  actual Functional Fly state and outcome feedback on held-out whole episodes.
  Mode arbitration stays unchanged. Default .1 RMSE/.9 agreement gates remain.
- Phase 10 learning uses actual executed transitions, including final STOP.
  Task reset preserves projection, MB credits and RPE estimates; transient
  compass, novelty, habituation and reward state reset. `clear_learning()` is
  the explicit wipe. `--learn` enables learning; `--learning-memory PATH`
  atomically loads/saves validated JSON. Keep the same seed/projection.
- Bounds: learning rates <= .1, update norm <= .5, credit magnitudes <= 2,
  RPE values <= 10, memory/replay bounded to 1024 patterns/128 samples.
  Detached snapshots and replay error/policy-drift checks roll back harmful
  updates; nonfinite limits are rejected before policy mutation. Guard counters
  are per instance; MB lifetime update counts and replay are persisted.
- `lesion` zeros input and existing incident gains for sampled neurons without
  deleting CSR entries; `sensitivity` varies bounded dynamics parameters.
  Both replay recorded trajectories with fresh matched initialization and
  report pooled-state/bias changes. Counterfactual task success is unmeasured.
- `run --shadow-brain full --telemetry-dir PATH` wraps the active controller
  with a zero-influence observer. Optional `--shadow-connectome-path` and
  `--shadow-checkpoint` select its artifact. Same candidates/safety mask;
  action choice and model routing come entirely from the active controller.
  `TrainingStep.shadow_decision` records agreement, expected-risk difference,
  observer latency/errors; no counterfactual success/call estimate is invented.
  Observer runtime failures do not halt active execution. Observer learning is
  disabled. This implements Phase 12's CLI mechanism and initial telemetry.
- Research, online memory and shadow controls are CLI-only. The browser still
  exposes Functional Fly and frozen FlyWire. No UI source changed here.

Actual measurements with FlyWire v783 (fingerprint below):

```text
Constrained teacher: six successful full/tournament bias episodes, seeds 0–2,
  30 executed steps. Separately trained checkpoint for each initialization.
Standard heuristic comparison: frozen=12/12, trained=12/12, mean 5 steps.
Tournament probe: frozen=10/12, trained=12/12; mean steps 6.667 -> 5.
  Difference is seed 0 hard control: 0/2 -> 2/2. Seeds 1/2 show no advantage.
  Same 15-step/60-tool budgets; topology and graph fingerprint unchanged.
Distillation v2: one train / one held-out episode; held-out RMSE .0167944,
  top-tool agreement 1.0, accepted under unchanged default gates.
  Student/original Functional Fly: each 12/12 actual repairs, 9 steps each.
Online repeated families: 36 actual runs, all successful in 9 steps.
  Learning seeds 0/1/2: accepted updates 53/54/53, rollbacks 1/0/1.
  No faster task-level adaptation demonstrated (Phase 10 acceptance open).
10% lesion: 13,926 neurons; active edges 2,191,750; structural edges unchanged
  at 2,700,513; pooled state RMSE .00227947, bias RMSE .163497, agreement .6.
Gain sensitivity: .6/.9/1.2; baseline 1.2 reproduces scores exactly.
Real full-graph shadow vs active-only: both toys succeed in 9 steps; identical
  executed actions, agreement 12/18, observer mean ~7.1 ms per decision.
Persistent-memory CLI across separate runs: both repairs succeed, 17 retained
  accepted updates and 8 replay entries; also works with a shadow observer.
```

Artifacts stay under `~/.cache/flycode/experiments/2026-10-15-adaptation/`.
The accepted student is in `2026-10-15-constrained/student-v2`; the previous
rejected student/report remains in `student`. Full experiment repositories and
trajectories/checkpoints are not published. No matched-compute superiority,
unseen-repository generalization or general LLM improvement is established.

Final verification:

```text
Full pytest: 291 passed, 8 skipped in 76.16s.
FLYCODE_TEST_CONNECTOME=1 pytest tests/e2e/test_real_connectome.py: 5 passed.
  Includes actual training-task improvement with unchanged real topology.
Default skips: five real-graph, two real-graph Docker, one local-model opt-in.
Targeted Ruff: passed except pre-existing RUF046 in mushroom_body.py:118.
Mypy on eight new research/adaptation/student/shadow modules: passed.
Built wheel includes new research/learning/shadow modules; no biological data.
Local UI refreshed at 127.0.0.1:8790; page and history API return HTTP 200.
```

The earlier Docker and local Qwen/Jev acceptances below were not rerun against
this increment; ordinary Docker acceptance is included in the full suite.

## Earlier increment: real FlyWire controller

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

## Earlier increment: offline training and smaller circuits

- `training/{trajectories,checkpoint,constrained}.py`: executed JSONL episodes,
  reset boundaries, dataset fingerprint, bounded gain/leak search and ridge tool
  readout. `train` is operational; `run --brain full --checkpoint PATH` loads
  the result. Adjacency and its fingerprint stay unchanged. Checkpoints bind
  parameter checksums, graph fingerprint and output vocabulary; nonfinite,
  overflowing and mismatched arrays are rejected. Reset retains fitted weights.
- `connectome reduce --source PATH --neurons 4096 --output PATH` extracts a
  connected induced circuit with existing weights and original neuron IDs.
  Input mappings are sliced from the parent, not regenerated; readout/dynamics
  are inherited. `--checkpoint` optionally selects a trained parent.
  `run --brain reduced --connectome-path PATH` loads the bundled checkpoint.
- `distill --trajectories PATH --teacher-checkpoint PATH --output PATH` fits
  only tool biases in a 400-parameter Functional Fly readout. Mode arbitration
  is retained. Whole episodes are held out; default gates are RMSE <= .1 and
  top-tool agreement >= .9. Failed candidates are saved but cannot be enabled.
  Accepted candidates load with `run --brain functional --checkpoint PATH`.
- Fixed Functional Fly's unconditional MB update when learning is disabled.
  This is necessary for frozen comparisons; it does not complete Phase 10.
- The new research workflows are CLI-only. The browser still offers Functional
  Fly and the frozen full graph. Model weights and experiment data stay outside
  Git. Benchmark/lesion/sensitivity were still stubs in this earlier increment.

Measured with the actual v783 artifact on 2026-10-15:

```text
Teacher data: two successful Functional Fly toy episodes, 18 executed steps.
Full graph: tool imitation accuracy .4444 -> .7778, MSE .07805 -> .01311.
Selected dynamics: gain=1.2, leak=.5; topology/fingerprint unchanged.
Actual task comparison: frozen and trained each solve 4/4 (two toys x two
  control modes), five steps each. No task-completion improvement.
Reduced graph: 4,096 neurons, 12,031 edges; 4/4 repairs, five steps each.
  Mean of 30 warmed steps: full 7.106 ms / reduced .117 ms.
  Array storage: full 30.172 MiB / reduced .379 MiB.
  Fingerprint: 162b8a20ba930cd15d9f02ab2355b48935431e86b69a7e9cd92beb8c86cd8ab5.
Distillation: one training episode, one held out; validation RMSE .02550,
  top-tool agreement .8889. Default .9 agreement gate failed as expected.
```

Artifacts: `~/.cache/flycode/experiments/2026-10-15-constrained/` contains
trajectories, checkpoint/training.json, student/distillation.json and
task-comparison.json. `2026-10-15-reduced/validation.json` contains the smaller
graph's actual runs and timing. Toy coverage is narrow; this is not evidence
of generalization, matched-compute superiority or biological cognition.

Final verification for this increment:

```text
Full pytest: 262 passed, 7 skipped in 77.28s.
Targeted Ruff: passed; mypy on eight new research/reduction modules: passed.
Built wheel includes training, reduced backend and UI; no biological data or
  checkpoint weights are bundled.
Actual CLI: trained full/import and reduced/arithmetic repairs both succeed
  with verification, five steps each.
Local UI restarted on 127.0.0.1:8790; completed history replay verified.
```

## Earlier increment: local browser workspace

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

## Earlier increment: local Qwen + Jev

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
| 6 Constrained training | §80 | Limited acceptance: tournament probe 10/12 frozen -> 12/12 trained across 3 seeds; difference at seed 0 |
| 7 Reduced connectome | §81 | Limited acceptance passed: same toy repairs with much lower compute |
| 8 Distillation | §82 | Limited acceptance: v2 real-controller replay RMSE .01679, agreement 1.0 on one held-out episode |
| 9 Candidate Tournament | §83 | Implemented: existing safety-masked hard/bias tournament tests pass |
| 10 Online learning | §84 | Bounded persistent updates, replay/rollback tested; faster task adaptation unproven |
| 11 Causal science | §85 | Lesion/sensitivity replay and multi-seed toy benchmarks; cross-connectome and matched compute open |
| 12 Shadow | §86 | Zero-influence CLI observer and initial telemetry; broader collection open |
| 13 Bounded influence | §87 | Not implemented; existing bias flag does not constitute validated rollout |
| 14 Autonomous Fly | §88 | Not implemented; gated by benchmark validation |

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


## Remaining work after this increment

- Phase 6/8: more varied held-out trajectories, unseen repositories and actual
  local-LLM comparisons; current acceptance covers narrow toy experiments.
- Phase 10: demonstrate faster end-to-end adaptation; current repeated-family
  result is a null finding. Existing unit reversal/prediction tests are not
  substitutes for task-level improvement.
- Phase 11: cross-connectome datasets, matched state/parameter/training budgets,
  lesions with actual counterfactual task executions and delayed-reward tests.
- Phase 12: broader recorded shadow episodes and analysis; counterfactual
  success/tool-call deltas stay unmeasured.
- Phase 13: explicit low-risk rollout levels, bounded influence and rollback
  validation. Phase 14 remains gated by benchmark evidence.
- UI selection for trained/reduced/shadow controllers and learned memory.
- Stressors declared on TaskSpec are not wired into the loop: partial
  observations, delayed rewards, context reversal and noisy feedback.
- Pre-existing duplicate toy fixture/backend test definitions and repository-wide
  Ruff/mypy findings remain; no unrelated cleanup was performed.
