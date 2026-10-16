# FlyCode handoff brief

Authoritative roadmap: `docs/Plan.md` (§1–104). Operating rules: `AGENTS.md`.

## Feedback benchmark and rejected training candidates — 2026-10-16

English-prompt `benchmarks/feedback-v1` is executed on runtime `694d42d`:
200 frozen baseline + 40 primary trained + 40 exploratory refined + 10 teacher
and 6 real Qwen runs = 296 agent runs. Original tests/case IDs were independently
checked, with 306 audits and four pristine negative controls. All raw evidence,
repositories, telemetry and ten numeric checkpoints remain under
`~/.cache/flycode/experiments/2026-10-16-feedback-v1`; public outcome/feedback/
manifest hashes are in `benchmarks/feedback-v1/results.json`. Recipe and report:
`benchmarks/feedback-v1/README.md`, `docs/validation/2026-10-16-feedback-benchmark.md`.

Functional teacher (90 actions) improves imitation 44.4%→88.9% but regresses toy
completion 14/20→10/20. Separately registered refinement on ten successful Full
Fly bias episodes (50 actions) reaches 100% imitation and 20/20 toy completion,
but heuristic held-out remains 0/20. Real single-Qwen transfer: frozen 2/2,
primary trained 1/2, refined 0/2. Three failures exhaust 8-call budgets; no provider
errors. Entire live phase: 585.54s, 34 requests, 45 tools, sequential model events.
The refined helper case repeatedly tests without editing. Both training candidates
are rejected as live replacements; defaults stay unchanged. No further held-out
tuning was done. Preserve this negative result, not just the toy improvement.

Real graph source checksums/fingerprint and all ten checkpoint mappings/mode
readouts/topology were verified; only tool readout and bounded gain/leak change.
Identity now includes training and telemetry, reproduced RED then fixed. Relevant
research/training tests: 38 passed; opt-in real graph tests: 5 passed; Ruff/mypy passed.
This is four known synthetic families, not matched compute or §§90–99 evidence.
The orchestration goal remains active: canonical SQLite Task4 is still next;
this standalone benchmark does not implement or admit ORC19/24.

## Live single/dual model acceptance — 2026-10-16

On runtime `18913d5`, a real CLI `--no-decision-model` repair completed in 134.65s
wall time with 3 Qwen requests, 6 tools, no scorer construction and no model errors.
The explicit opt-in dual test passed (1 test/93.60s; 94.43s wrapper wall time):
missing `decision_model` preserved the Jev default, with 3 Qwen + 3 Jev requests,
nonempty decision scores, real repair and local verify/STOP. Each cortex's observed
model events were sequential. These are single-instance checks, not proof of a
shared multi-agent endpoint pool.

Both produced projects then passed the new owner-approved baseline negative control,
target, full fixture regression and integration plan, exact add/subtract case IDs,
unchanged original tests and current artifact checks. Public metadata/raw metrics:
`docs/validation/2026-10-16-model-selection.json`. Retained external bundles:
`~/.local/share/flycode/validation/model-selection-20261016-299afcda9b7b`.
The relocated verdicts were checked current. Different prompts/budgets/cache states
make the durations unsuitable for performance comparison; no research/orchestration
admission is inferred. Earlier 5s timeout remains recorded below.

Tasks1–3a are delivered for their supported scope. Next: canonical SQLite Task4,
then the shared runtime/resource/review/completion work. Seven biological-graph
cases remain opt-in/unrun in this increment; a fresh graph/research acceptance is
still required on the final runtime.

## Trusted acceptance plans — 2026-10-16

`AcceptanceContract` maps required criteria to exact check/case IDs. The external
`OracleRegistry` stores immutable owner-approved test bytes and semantic versions;
agent proposals append optional checks only. `PlanBuilder` binds staged snapshots
and subject refs; `VerificationEngine.verify_plan` runs baseline/target/regression/
integration with real subprocesses, graph dependencies and isolated oracle overlays.
Standalone `verify` remains `registered-suite`; acceptance has a distinct scope,
criterion coverage and check outcomes. Raw negative-control failures remain raw.
New owner approval, runtime/input changes and evidence tampering invalidate current
results. JUnit and observer outcomes now agree as well as case identities.

Verification: full suite 452 passed, 8 skipped (188.54s); targeted verification/
identity62 passed. Ruff/mypy passed. Final wheel SHA256:
`95612fb177e7b42b8134bac24111cf107429d2981c6bba1755b74529272e5c3a`.
Extracted-package CLI pass/empty exit codes and a real staged acceptance plan
passed outside the checkout. Seven graph-dependent and one dual-LM opt-in tests
remain skipped in the default suite; they do not close live/research acceptance. This delivers the supported trusted-local pytest
path; hostile sandbox, other runners, durable recovery and workflow completion
remain later work. Next: canonical SQLite WorkItem/Profile/Instance/Attempt queue
(Task4), followed by runtime/review/integration and its acceptance gate.

Cleanup audit: one additional real subprocess test returned `PermissionError`
during owned-group cleanup after pytest exited successfully; 20 instrumented
repetitions did not reproduce it. The failure was retained as inconclusive.
Signal-edge regressions now require confirmed absence after a permission race and
SIGKILL; persistent permission failures remain interrupted/unconfirmed. Optional
checks cannot bypass this global cleanup gate. This does not establish a kernel
cause or hostile-process isolation.

## Searchable session journal — 2026-10-16

The existing Journal tab now projects canonical run events with text search,
source/error filters, lazy JSON details, bounded rows, event/step IDs and live
follow. Both classic and desktop shells use the same implementation. A new local
`GET /api/runs/{id}/journal` streams a fixed recorded prefix as JSONL outside the
writer lock; filters never change the exported history. Event schemas and training
telemetry remain unchanged. Upstream source analysis and scope are recorded in
`docs/specs/session-journal.md`; execution plan is `2026-10-16-session-journal.md`.

Verification: all 44 interface API tests passed. Both browser surfaces cover
export/restart, real repair, SSE replay, filtering, mobile, pagination, duplicate
events, keyboard focus and HTML text safety. The staged journal-only snapshot
passed 21 API tests and browser acceptance. Classic and desktop regressions also
passed. Ruff/mypy and JavaScript syntax passed; unrelated suites were skipped.
This UI addition does not close verification/orchestration roadmap phases.

## Optional desktop workspace — 2026-10-16

`/desktop` adds a separate presentation based on measured layout/tokens from
the installed Codex renderer. `/` retains the classic interface. Both use the
existing runtime, projects, run history, model preferences and event stream.
The desktop shell has light/dark themes, collapsible navigation, keyboard
controls and an optional inspector. No orchestration phase is closed by this UI.

Local projects may register existing folders or create new ones without Git.
The native chooser runs in a separate Python/Tk process. Projectless chats use
explicit `project=null` and separate empty directories; omitted project retains
the legacy API demo default. See `docs/specs/desktop-interface.md` and the two
UI plans dated 2026-10-16.

Verification: 17 interface API tests passed; desktop/390/320 browser scenarios
covered real demo repair, Stop, history/changes, themes and keyboard. Classic,
project and projectless-chat browser acceptance also passed. The staged UI-only
snapshot independently passed 17 API tests and desktop browser acceptance.
Changed-file Ruff/mypy and JavaScript syntax passed. No broad suite was run for
this UI task; the verification foundation remains separate work.

## Specification delivery — 2026-10-16

Active goal: implement §§100–103 using
`docs/superpowers/plans/2026-10-16-specification-delivery.md` and §104.
The goal contains the same eight delivery stages. Scientific claims remain gated.

First increment: optional scorer (`decision_model=null`, CLI `--no-decision-model`),
server catalogue and explicit one-request role probes, model dialog plus execution
settings button immediately to its right. Missing scorer field preserves dual default.
History inspection no longer overwrites next-run controls; personal model choice persists.
Runtime model timeout is now configurable from execution settings.

Verification before this increment's commit: 371 passed, 8 skipped (151.89s).
Changed-file Ruff and mypy passed. Visual checks at desktop, 390px and 320px:
model/settings targets44px, correct order, no horizontal overflow. Actual Qwen
compatibility probe timed out at30s; no live single-Qwen repair acceptance claimed.
Deterministic HTTP tests cover both model protocols and absence of scorer requests.
Existing scoped rollout evidence is stale after core source changes; regenerate after
implementation freeze rather than relabelling old evidence.

Second increment: `verification/` implements trusted-local snapshots (bytes/modes,
base/inventory, installed environment and runtime hash), a registered pytest runner,
independent collection/case observation + JUnit reconciliation, deterministic verdicts,
bounded redacted artifacts/checksums and `flycode verify --repo PATH`. All rerun outcomes
are retained; failure/skips/xfail/xpass/flaky, timeout/cancel and empty/incomplete reports
cannot become a false pass. Process-group cleanup includes children after pytest exits.
Untrusted execution is blocked. Git metadata files/directories are excluded, Git discovery
is confined and source index/status preservation is tested on a real worktree.

Verification: 423 passed, 8 skipped (171.43s); targeted verification/identity33 passed;
Ruff/mypy passed. A read-only audit found four defects; regression tests reproduced them
before correction, and the auditor independently reran the fixes (4+6 passed).
The live UI single-Qwen smoke made one primary request, no scorer request and ended
incomplete on its5s timeout. Stop prevented further work in a separate run; both model
and settings controls were disabled while active. History with a legacy pair left the
personal single-model choice intact. These are smoke outcomes, not successful live repair.

The trusted criteria/check/case mapping and staged pytest plan are now implemented
above. Next: canonical SQLite contracts/queue Task4; standalone suite verification
is not a task/workflow completion gate. No ORC/TRK implementation or new scientific admission exists.

The baseline MVP covers Phase 4 (§78). Phase 5 (§79) now runs the same tasks
with a verified, sparse biological graph. Hermes in the roadmap means FlyCode
itself: connected agents are hosted here and orchestrated by the controller.

## Proposed task tracker (2026-10-16)

`docs/Plan.md` §103 incorporates `docs/specs/task-tracker.md` v1.0. Proposed
Phases 26–29 cover task creation/backlog/board, atomic task requests and assignment,
live agent→task visibility, handoff/recovery and verification-backed completion.
TRK-01..11 map to TT-01..16. Reuse canonical WorkItem/AgentAttempt and the ORC
store/dispatcher; do not implement a second queue or equate profile names with
running instances. Multiple root tasks remain visible with one active workflow
in the first version. This is specification-only work; no tracker runtime or
new API was implemented.

## Proposed model selection and settings split (2026-10-16)

`docs/Plan.md` §102 incorporates `docs/specs/model-selection.md` v1.0, including
single Qwen without Jev, optional primary/scorer roles and future compatible models
through the same extensible catalogue. The composer has one model-selection
button and a separate connectome/steps/execution-settings button immediately to
its right. MOD-01..07 map to MS-01..12. The user explicitly requested specification
only; no nullable-decider support or UI implementation was performed. Model mode
must become part of admission identity rather than reuse dual-model evidence.

## Proposed verification module (2026-10-16)

`docs/Plan.md` §101 incorporates `docs/specs/testing-module.md` v1.0: proposed
snapshot/plan contracts, isolated runners, deterministic verdicts and immutable
evidence for project correctness, orchestration reliability and research.
TST-01..15 map to TV-01..17 and proposed Phases 21–25. Phases 21–22 support
single-agent execution first and must precede acceptance of multi-agent phase 17;
the numbering does not create a dependency on completing all of Phases 15–20.
This is documentation only. Existing pytest/tool/research results do not certify
this new module; `run_tests`, completion gates and admission keep their current
runtime semantics until an explicit, tested migration is implemented.

## Proposed next scope: multi-agent orchestration (2026-10-16)

`docs/Plan.md` §100 incorporates `docs/specs/connectome-orchestration.md` v1.0.
This is a specification-only delivery; no orchestration runtime or new API exists
yet. Proposed Phases 15–20 cover profiles/DAG/scheduling, communication and shared
budgets, isolated patch review/integration, neural shadow, scoped influence, and
the team interface. ORC-01..13 requirements map to AC-01..14 acceptance scenarios.
Existing Qwen/Jev model routing and single-agent admission must not be relabelled
as multi-agent execution or used to authorize it. The previous implementation
goal remains completed; implementing this new specification is separate work.

## Latest increment: guarded rollout and browser configuration (2026-10-16)

Plan: `docs/superpowers/plans/2026-10-15-validated-rollout.md`.
The implementation goal is fulfilled: Tasks 1–7 are implemented, verified and
published to public `FlexGodX/FlyCode` (`7d46629`, `456d444`). Research acceptance
gaps remain open separately from this software delivery.

- Phase 13: candidate-level corrections at 0/.1/.25/.5/1, bounded by
  `.2 * influence`, only inside an allowlist of safe diagnostics. Never promote
  edits, SQL, tests or STOP. Invalid scores, duplicate IDs and observer errors
  preserve the active choice. Failed actual interventions/regressions latch
  influence off for the rest of the episode. Tool effects are not undone.
- Phase 14: `--autonomous` requires matching evidence and uses guarded hard
  ranking; regression or failure of a hard choice different from bias switches
  subsequent cortex contexts and selection to bias. Legacy research hard mode
  remains available separately. Safety/budgets/verified completion remain.
- Admission is recomputed from complete paired raw trajectories. Guard telemetry
  must exist on every autonomous step; relabeling raw hard runs is insufficient.
  Nonzero-level promotion requires actual interventions. Local-model admission
  binds exact instruction text as well as controller/graph/checkpoint/model/
  seed/fixture/budgets. No arbitrary-repository admission is currently available.
- Browser: local prepared full/reduced, trained and distilled controllers;
  observer, discrete influence, admission, seed/budgets and Functional learning
  profiles. Artifact IDs resolve on the server under `FLYCODE_ARTIFACT_ROOT`
  (default `~/.cache/flycode`); escaping symlinks and memory traversal reject
  before workers start. Load active memory before wrapping, save atomically,
  freeze observers and close owned controllers. Old history uses safe defaults.
- Activity explains active/proposed/executed choices, influence, learning and
  fallbacks; raw details are collapsed. Connection status no longer dumps every
  installed model ID into the mobile dialog. Verified at 1280px and 390px with
  no page/dialog horizontal overflow. Real browser demos completed: zero-
  influence full observer + learning in 9 steps, guarded full autonomy in 5.

Measured results: `docs/research-results/2026-10-16-rollout.json`.
36/36 influence-level toy runs completed; zero interventions, so promotion above
.1 rejected. Final separate comparison: baseline 12/12 and observed 12/12 at
9 steps; guarded full 6/6 at 5 steps, no regressions/rollbacks. This is two
teacher toys × three seeds with heuristic candidates, not a general advantage.
Current evidence/admissions: `~/.cache/flycode/experiments/2026-10-16-final-rollout/`.
Admission becomes stale whenever fingerprinted runtime code changes.

The stronger helper revision-2 model study is finished: Functional 0/2, full
1/2 (boundary fix, 8 steps); neither solved observable helper delegation within
24 model calls. Source changed during that exploratory run, so preserve its
outcomes but never use it for admission or a stable-source causal comparison.
The original numeric-only helper study remains labelled separately.

Final verification of this increment: 359 passed, 8 skipped (161.92s); separate
real-connectome suite 5 passed (7.38s); current browser API suite 23 passed
(38.69s). Focused Ruff passed and mypy passed on 19 changed/runtime modules.
The wheel was built in an isolated build environment, its new modules/static
assets inspected and its extracted CLI executed. UI restarted idle on 8790;
existing history remained readable and a new guarded demo completed in 5 steps
with an explicit admission event. CLI telemetry also saves `run.admission.json`.
Browser acceptance was performed through the app browser; the development
Playwright script was updated but not executed in this session.

Still open empirically: independent connectomes; matched compute/state/training
budgets; faster adaptation; broad unseen repositories; paired Qwen/Jev rollout
admission; useful actual diagnostic interventions and evidence for later levels.

## Earlier increment: broader evaluation and scoped admission (2026-10-15)

Active goal: implement `docs/superpowers/plans/2026-10-15-validated-rollout.md`.
At that earlier revision Tasks 1–3 had software implementations; later delivery is documented above.
The overall goal remains active. No general research advantage is claimed.

- Two additional synthetic fixtures: `search_helper_bug` and
  `boundary_regression_bug`. They are separate from teacher toys, not unseen
  real repositories. Helper revision 2 tests observable delegation to the shared
  conversion function; division inline cannot pass. The initial numeric-only
  revision missed that requirement and its model results are labelled as such.
- Benchmark supports actual local Qwen/Jev with explicit endpoint/model/call
  budgets. The local endpoint is resolved when the configuration is read,
  preventing environment changes from silently relabeling an experiment.
- `ControllerSpec.lesion_fraction` runs real tasks with zeroed existing neural
  gains. Parent files and structural topology are retained; report fingerprints
  distinguish the actual damaged graph from its parent.
- `adapt --config docs/examples/adaptation.json --output PATH` retains one
  Functional brain per seed/learning condition while resetting repositories,
  cortex and episodic memory. Records policy hashes, guard state, first/last
  repetition costs and task success; failures stay in denominators.
- Extending coverage found a real baseline bug: FSM referenced a missing
  recovery stage. Added the stage and skipped it after passed verification;
  regression and recovery-exit tests pass. All 24 FSM cases were rerun, not
  just the failed cases. Earlier raw failures remain in their source reports.
- Benchmark now records active/observer identities, completed/source-unchanged
  flags, implementation and trajectory checksums. `shadow-analysis` preserves
  error/missing recommendation coverage and rejects inconsistent traces.
- `validate-rollout` and `research.validation.authorize` recompute admission
  from raw report/metrics/trajectories. Require >=3 seeds, >=2 fixtures, complete
  paired coverage, clean verified outcomes, no extra target tool calls, matching
  scope and >=30 observer recommendations for the first bounded step (.1).
  Code/task/controller/graph/checkpoint/model/budget mismatches reject admission.
  This is compatibility evidence, not authentication or a general safety proof.
- Admission and autonomous execution are different deliverables: the latter and
  nonzero influence are not wired into agent/CLI/UI yet. Legacy research hard
  control retains its previous meaning. Browser changes have not started.

Actual measurements, available in `docs/research-results/2026-10-15-expanded-evaluation.json`:

```text
Expanded heuristic comparison: 192 case results (168 non-FSM plus 24 corrected
  FSM reruns), 3 seeds x 4 synthetic fixtures x 2 controls x 8 controllers.
  Teacher toys: FSM/Functional/full/trained/lesioned/reduced/student each 12/12;
  generic RNN 0/12. Additional synthetic tasks: every heuristic variant 0/12.
  Params/hidden states/training budgets are NOT matched; no superiority claim.
10% real-graph lesion: no task completion change on this limited suite.
Repeated-family runner: 36/36 repairs, both conditions 9 steps; learning
  accepted 160 updates and rolled back 2. Faster adaptation remains unproved.
Initial local-model pilot: Functional search passed numeric tests in 8 steps
  but did not reuse the helper; boundary failed at 24 model requests. Full
  search failed at 24 requests; boundary passed in 11 steps. Seed 0 only.
  Stronger helper contract revision 2 was measured separately; see latest limitations above.
Actual full-graph observer pilot: 36 paired/mode runs, 108 recommendations,
  100% coverage, 0 observer errors, agreement 70.37%, mean observer 10.21 ms.
  Narrow bounded and hard-mode admission checks passed on the two teacher toys;
  actual authorize() calls confirmed matching seed-0 configurations.
Full pytest: 310 passed, 8 skipped in 112.68s.
Real-connectome acceptance: 5 passed in 8.37s. Wheel build succeeded.
Focused Ruff/mypy on research/CLI/FSM and new tests passed.
Pre-existing fixture-module findings: I001, F401, RUF012, F811 remain.
```

Current experiment root: `~/.cache/flycode/experiments/2026-10-15-rollout/`.
`expanded-comparison/` retains pre-fix FSM errors; `fsm-recovery-fixed/` holds
all corrected FSM runs. `adaptation/`, `heldout-local/` (old helper checks),
`heldout-local-v2/` (stronger delegation checks) and `shadow-validation/` contain
raw evidence. Changing implementation invalidates earlier admission files;
regenerate after later rollout changes. Raw experiment repositories/weights are
not published. New commands and examples are documented in RU/EN README.

## Earlier increment: adaptation, evaluation and shadow mode (2026-10-15)

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
