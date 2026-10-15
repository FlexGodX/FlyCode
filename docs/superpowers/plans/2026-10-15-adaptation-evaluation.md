# Controller adaptation and evaluation implementation plan

**Goal:** Continue §§80,82,84–88 with reproducible task comparisons, validated distillation and bounded persistent learning inside FlyCode.

**Architecture:** Keep the agent/tool/safety boundary. Research runs instantiate isolated controllers and repositories from explicit Pydantic configurations. A separately named tournament probe offers alternative read-only diagnostics without changing the existing production cortex or neural priors. Offline student formats retain legacy compatibility. Online experience persists separately from per-task state and is guarded by replay-based rollback.

**Spec:** `docs/Plan.md` §§37–40,53–68,80–88; `docs/handoff.md` current measurements.

**Tech stack:** Existing Python/NumPy/SciPy/Pydantic/pytest; no new runtime dependencies.

## Constraints and review focus

- Freeze biological adjacency and compare identical fixture/cortex/budget/seed settings.
- Keep standard and tournament-probe results separate; never claim general advantage from the latter.
- Report null results, unmeasured counterfactuals and unmatched compute explicitly.
- Reject nonfinite learning signals before mutation; bound updates and retained experience.
- Preserve previous student checkpoints and keep validation episodes out of fitting.

## 1. Reproducible comparison (supports Phase 6 and §85)

Files: `research/{benchmark,tournament}.py`, `cli.py`, `tests/unit/research/`, `tests/unit/research/test_benchmark.py`.

- [x] Add failing tests for isolated fixture runs, unique experiment directories, checkpoint selection, complete failure recording and invalid configuration.
- [x] Implement `ControllerSpec`, `BenchmarkConfig`, `run_benchmark(config, output)` with saved configuration, trajectories, metrics, controller fingerprints and measured latency.
- [x] Add `TournamentCortex`: keep the normal proposals; add unique `run_tests` and `git_diff` diagnostics only during EXECUTE, up to four proposals. Test original proposals are retained and healthy STOP stays unchanged.
- [x] Implement `benchmark --config CONFIG --output DIR`; run frozen/trained comparisons with seeds 0,1,2 in bias and hard control on both registered toy tasks.
- [x] Mark Phase 6 acceptance only from actual task/step improvement with unchanged topology; label the exact evaluation scope.

## 2. Distillation (Phase 8)

Files: `training/{student,distillation}.py`, `brain/functional_fly.py`, corresponding tests.

- [x] Add failing test for a teacher trajectory requiring nonlinear feature response and for v1 checkpoint compatibility.
- [x] Fit a bounded nonlinear feature readout using only training episodes; persist its explicit feature schema and checksum. Load old linear readouts unchanged.
- [x] Validate the actual Functional Fly output on held-out episodes, including its habituation/state behavior, rather than only evaluating a detached matrix.
- [x] Re-run the fixed .1 RMSE/.9 agreement gate and real task checks; retain failed reports.

## 3. Persistent bounded learning (Phase 10)

Files: `cognition/{mushroom_body,rpe,adaptation}.py`, `brain/functional_fly.py`, `agent.py`, CLI, tests.

- [x] Red tests: disabled learning, finite/rate/norm limits, retained learned weights across task reset, bounded tables, detached snapshots and rollback.
- [x] Preserve encoding projection and learned policy across episodes while resetting transient compass/habituation/reward state; expose an explicit learned-memory clear operation.
- [x] Capture MB/RPE policy snapshots, maintain a bounded replay set, compare retained prediction error and policy change, and reject/rollback harmful updates.
- [x] Learn only from actual executed outcomes, including the final action; verify no cross-task synthetic transition is credited.
- [x] Measure repeated-family adaptation and reversal using fixed deterministic inputs, separately from end-to-end repository success.

## 4. Causal probes and zero-influence shadow (Phases 11–12)

- [x] Add lesion/sensitivity tests for unchanged source topology, actual state changes, invalid limits and report persistence; implement both CLI commands.
- [x] Add shadow tests for identical active actions/model-call counts, isolated observer failures, lifecycle reset and persisted comparison records.
- [x] Implement an optional observer over the same candidates/safety mask without affecting active routing; retain observer recommendations in executed-step telemetry.
- [x] Run actual full-graph lesion/sensitivity and two shadow repairs; preserve counterfactual success as unmeasured.

## 5. Delivery and next gates

- [x] Update Russian/English README and handoff with commands, measurements, remaining gates and exact scope.
- [x] Run focused tests, full pytest, new-module lint/type checks, then publish a verified increment.
- [x] Keep phases 13–14 gated until broader benchmark/shadow evidence exists; do not treat current bias/hard flags as completed rollout validation.
