# Validated FlyCode rollout implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and measure the remaining evaluation, shadow, bounded-influence and validated autonomous workflows, expose them in the browser, and publish verified increments without treating an unproved research hypothesis as a completed acceptance criterion.

**Architecture:** Extend the existing isolated experiment runner rather than change controller priors to force a positive result. An observer always receives actual observations; only the candidate-selection boundary may apply its bounded recommendation. A scoped evidence report gates production influence; existing research hard/bias commands retain their explicit experimental meaning. The browser selects server-owned artifacts and memory profiles.

**Tech Stack:** Existing Python >=3.10, Pydantic v2, NumPy/SciPy, pytest, FastAPI and packaged HTML/CSS/JavaScript; no new runtime dependencies.

**Spec:** `docs/Plan.md` §§53–68,80–88,97–99 and `docs/handoff.md`.

Execution is native in this session with test-first changes. Exploration is delegated read-only. The named superpowers execution subskills are not installed in this workspace; the user's instruction to continue implementation supplies execution authorization.

## Global constraints

- Preserve biological adjacency and original neuron IDs; never synthesize missing connectome data.
- Hermes means FlyCode itself, hosting the connected Qwen/Jev agents.
- All cross-module contracts use Pydantic; ordinary environment failures remain outcome data.
- Do not change the production cortex or neural priors to manufacture benchmark improvement.
- Zero influence preserves active action selection and model routing exactly.
- Existing safety masks apply at every influence level; claimed low risk is insufficient without a tool allowlist.
- An evidence report authorizes only its measured controller/artifact/cortex/task scope, never arbitrary repositories.
- Missing statistical benefit remains a reported null result. Implementing a command does not satisfy an empirical acceptance criterion.
- Work on the current project branch; no `codex/` prefix or authorship markers.

## Review focus

1. A failed task or missing checkpoint must remain in the report denominator; test mixed successful/setup-failed cases.
2. Memory must persist only within the same seed/controller/learning condition; test frozen-policy equality and independent episode repositories.
3. Duplicate IDs, nonfinite scores and observer failures must preserve the active choice; test fallback before applying influence.
4. A passing report for another graph/checkpoint/model/task must not unlock autonomy; test mismatched and tampered evidence.
5. Browser artifact/memory identifiers must not become arbitrary filesystem paths; test traversal/symlink rejection and history compatibility.

## Task 1 — Broader task evaluation (Phases 6/8/11)

**Files:** modify `environment/fixtures.py`, `research/benchmark.py`, `research/perturbation.py`, `cli.py`; create `research/cortex.py`; tests in `tests/unit/research/` and `tests/unit/environment/`.

**Interfaces:** `BenchmarkConfig` keeps old configurations valid. Extend `cortex` with `lmstudio` and explicit model/request budgets. Add two registered synthetic fixtures (`search_helper_bug`, `boundary_regression_bug`) separate from teacher toys. `ControllerSpec.lesion_fraction: float | None` applies the existing fixed-topology lesion before actual task execution. Report actual artifact identity and measured cost, not assumed compute equivalence.

- [x] Write red fixture tests: initial tests fail, a hand-written correct source edit passes all ordinary/edge cases, repeated materialization is identical. A helper search task must require reading the existing helper; no fixture-specific repair rule is added to the heuristic.
- [x] Write red benchmark tests for a missing checkpoint retained as an error case, both new fixture IDs, explicit local-model budget validation and actual lesion execution/provenance.

```python
config = BenchmarkConfig.model_validate({
    'controllers': [{'id': 'missing', 'brain': 'full', 'checkpoint': '/missing'}],
    'fixtures': ['boundary_regression_bug'], 'seeds': [0], 'controls': ['bias']})
report = await run_benchmark(config, tmp_path)
assert report['summary']['missing']['errors'] == 1
assert report['summary']['missing']['success_rate'] == 0
```

- [x] Run focused tests to observe the missing behavior; implement the fixture materializers, shared cortex construction and controller perturbation; rerun focused tests.
- [x] Run actual frozen/trained/student/baseline comparisons on available fixtures and seeds. Run local Qwen/Jev on held-out synthetic tasks with explicit budgets; preserve failures and actual model calls.
- [x] Record state/parameter/latency/training-budget comparability explicitly. Additional real connectomes require verified source artifacts; if only FlyWire is available, state that cross-connectome coverage is still absent.
- [x] Commit the independently usable evaluation increment with its report and documentation.

## Task 2 — Repeated-family measurement (Phase 10)

**Files:** create `research/adaptation.py`; modify `cli.py`; create `tests/unit/research/test_repeated_families.py` and a reproducible example configuration.

**Interfaces:** `AdaptationConfig` selects Functional Fly, seeds, fixture sequence, repeats, budgets, optional student checkpoint and cortex settings. `run_adaptation(config, output: Path) -> dict` holds one brain per seed/learning condition, uses fresh repositories/cortex/episodic memory each episode and saves incremental JSON/trajectories. CLI: `adapt --config FILE --output DIR`.

- [x] Red test actual repeated repairs with frozen/learning conditions; assert independent repositories, unchanged frozen policy fingerprints and retained learned updates across episodes.

```python
report = await run_adaptation(AdaptationConfig(seeds=[0], repeats=2), tmp_path)
frozen = [case for case in report['cases'] if not case['learning']]
assert all(case['policy_before'] == case['policy_after'] for case in frozen)
assert len({case['repository'] for case in report['cases']}) == len(report['cases'])
```

- [x] Implement the runner and CLI; record success, steps/calls, prediction error, updates, rollbacks and before/after policy hashes. Compare first/last repetitions only alongside completion rates.
- [x] Run multiple seeds and reversal/prediction tests, report actual benefit or its absence. Do not substitute predictor accuracy for faster repository repair.
- [x] Commit with an actual measurement report. Phase 10 empirical acceptance remains open if task adaptation is not faster.

## Task 3 — Shadow analysis and scoped validation (Phases 11/12)

**Files:** create `research/shadow.py`, `research/validation.py`; extend `research/benchmark.py`, `cli.py`; tests in `tests/unit/research/`.

**Interfaces:** controller configurations may attach one observer. `analyze_shadow(directory: Path) -> dict` reads durable steps and reports agreements, expected-risk deltas, errors and observer latency. Counterfactual success/call deltas stay null unless paired task executions measure them. A validation artifact binds the report checksum, controller/checkpoint/graph, model settings, fixtures, seeds and budgets.

- [x] Write failing tests for legacy/no-shadow logs, errored observers, mixed valid records and paired benchmark coverage. Reject missing/error cases, regressions, unsafe outcomes and mismatched controller identities rather than dropping them.
- [x] Implement analysis and validation artifact creation/loading. A passing narrow toy scope must not authorize a novel repository or another model/checkpoint.
- [x] Run multi-seed shadow episodes and produce an inspectable report with explicit coverage limits; commit.

Status at the first delivery: software for Tasks 1–3 and the scoped real-graph pilot are verified. The initial local-model helper check was audited and strengthened to observable delegation; revision-2 model runs continue under `heldout-local-v2/`. Research acceptance (faster adaptation, matched compute, independent connectomes and unseen real repositories) remains unproved or unmeasured. Subsequent source changes deliberately invalidate earlier admission files.

## Task 4 — Bounded candidate influence (Phase 13)

**Files:** create `policy/rollout.py`; modify `brain/shadow.py`, `agent.py`, `telemetry/schema.py`, `cli.py`; create `tests/unit/policy/test_rollout.py` and extend `tests/integration/test_shadow.py`.

**Interfaces:** `RolloutConfig` selects influence from `0/.1/.25/.5/1`. Candidate intervention consumes the active `TournamentResult` and observer scores; it never changes the active `NeuralState` sent to Qwen/Jev. Preserve the zero-influence API. Report requested/applied influence, original/proposed/executed IDs, bounded score correction and rejection/rollback reasons.

- [ ] Red tests: zero reproduces the active result; finite bounded correction can change only allowed diagnostic choices; an active edit/stop cannot be displaced; invalid/duplicate scores fall back.

```python
assert decision.applied_influence == 0  # active winner is an edit
assert decision.executed_choice == active.winner.id
```

- [ ] Implement a narrow allowlist: read_file/list_files/search/git_status/git_diff/docker_logs/postgres_status/service_health with assessed risk below .4 and the existing safety mask. Exclude SQL queries, run_tests, edits and STOP from early intervention. Bound the observer correction and disable influence after a regression/failed intervened action.
- [ ] Add CLI evidence requirements for deployment. Isolated benchmark fixtures may exercise experimental influence to produce evidence; this does not unlock arbitrary project execution.
- [ ] Execute actual paired task comparisons and rollback tests at every supported level; commit the limited rollout implementation and findings.

## Task 5 — Validated autonomous mode (Phase 14)

**Files:** extend `policy/rollout.py`, `research/validation.py`, `agent.py`, `cli.py`; integration tests for evidence scope and actual end-to-end execution.

**Interfaces:** an explicit autonomous mode uses controller selection only inside a matching validated task/controller/model scope. It retains safety, budgets, verification and stop handling. Legacy research `--control hard` remains clearly experimental and is not a deployment attestation.

- [ ] Write red tests that absent, corrupt, stale/mismatched or insufficient evidence prevents environment mutation; valid scoped evidence permits a matching fixture and rejects an unrelated repository.
- [ ] Implement the scoped gate and autonomous selector with rollback to the active controller on measured failure; record the gate decision and reason in telemetry.
- [ ] Run validation and actual matching task executions. Keep general production readiness and research superiority unclaimed outside measured scope; commit.

## Task 6 — Browser configuration and activity (all implemented modes)

**Files:** modify `web/runs.py`, `web/app.py`, `web/static/index.html`, `web/static/app.js`, `web/static/style.css`; create `web/artifacts.py`; extend `tests/unit/web/test_app.py` and `tests/browser/agent-ui.cjs`.

**Interfaces:** bootstrap returns server-owned controller/artifact identifiers and available validated scopes. Requests select identifiers, observer, influence/mode and a memory profile name. Resolve paths only on the server under configured artifact/memory roots; load active memory before wrapping, close controllers in finally and save learning atomically. Old history remains readable with defaults.

- [ ] Read FastAPI/security skills and existing UI before changes. Capture current settings/activity screenshots.
- [ ] Red API tests: trained/reduced/shadow runs, missing artifact errors before worker start, memory traversal/symlink rejection, incompatible learning and evidence scopes, historical requests with old fields.
- [ ] Implement artifact catalogue, configuration and validated server execution; rerun API tests.
- [ ] Add clear Russian controls and separate observed/active/executed recommendations plus learning/rollback activity. Restore selections from history; disable unavailable combinations with a concrete reason.
- [ ] Visually verify desktop/mobile settings, a real completed shadow run and activity details; capture after screenshots and fix observed defects.
- [ ] Commit browser changes with acceptance evidence.

## Task 7 — Final verification and publication

- [ ] Update RU/EN README, handoff and portable JSON measurements for the final implementation. Separate software delivery, empirical acceptance and remaining data/evidence gaps for each phase.
- [ ] Run focused checks per edit and the full pytest suite at the final source revision; run relevant real-connectome/local-model cases and lint/types on changed modules. Report pre-existing static failures by name.
- [ ] Build/inspect the wheel, restart the idle local UI, verify durable history and no active task interruption.
- [ ] Review the complete diff, push verified commits to the existing public repository and verify local/remote state.
- [ ] Complete the goal only when every required deliverable above is implemented/verified; if an external data/evidence gap remains, retain an explicit active status rather than fabricate scientific acceptance.
