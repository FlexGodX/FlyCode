# Connectome orchestration implementation plan

**Goal:** Continue phases 5–14 with real connectome dynamics orchestrating the agents hosted by FlyCode (called Hermes in the roadmap).

**Architecture:** Preserve the existing agent/cortex/tool boundary. Add a separately registered sparse full-connectome backend with explicit dataset provenance; keep the legacy random projection available for comparison. Training, reduction and experiments run offline against recorded SoftwareGym trajectories. Introduce controller rollout modes inside FlyCode only after measuring their behavior.

**Tech Stack:** Python 3.12, NumPy, SciPy CSR, PyArrow for one-time FlyWire import, Pydantic, existing pytest/CLI/local web UI.

**Spec:** `docs/Plan.md` §§35–43,53–68,79–88 and the user clarification that FlyCode itself is Hermes.

## Global constraints

- Never silently substitute synthetic topology for missing biological data.
- Freeze adjacency; learn only the parameters permitted in §§36–38.
- Use all released proofread neurons and disclose any synapse threshold/sign convention.
- Store source data outside Git; persist version, source URL, checksums and preprocessing settings.
- Full neural activity stays inside the backend; event/state summaries must remain JSON-compatible and bounded.
- Retain safety masks, actual per-model call accounting and verified-completion requirements.
- A working training command is not proof of an improvement. Report measured acceptance separately.

## Review focus

- Wrong matrix orientation reverses signal flow.
- Large neuron IDs must never pass through float conversion.
- Duplicate connections across neuropils must be summed before thresholding.
- Readout must emit real tool names, and graph perturbations must alter neural/readout behavior.
- A missing dataset or corrupt checkpoint must fail explicitly without changing the current working controller.

### 1. Phase 5: data and recurrent full backend

Files: `brain/full_connectome/{graph,sparse_backend}.py`, `brain/__init__.py`, `cli.py`, `pyproject.toml`, corresponding unit/integration tests.

- [x] Red tests: directed weighted propagation, duplicate aggregation, missing endpoints, nonfinite weights, topology fingerprint and immutable graph.
- [x] Build a CSR graph importer for official FlyWire 783 with source checksum validation, neuron IDs, normalized signed recurrence and a provenance manifest.
- [x] Red tests: recurrence/persistence, seed/reset determinism, topology-dependent tool bias, compact JSON state and no implicit data fallback.
- [x] Add `--brain full`, explicit dataset path/configuration, `connectome prepare` and metadata inspection commands.
- [x] Run the same toy SoftwareGym tasks in both control modes, including an opt-in acceptance using the downloaded biological graph. Measure memory and latency.

### 2. Phases 6–8: offline learning, reduction and distillation

Files: new `training/` modules, checkpoints and offline CLI commands; tests use tiny graphs and isolated trajectories.

- [x] Collect real executed observation/action trajectories with episode boundaries.
- [x] Fit readout and selected dynamics parameters while fingerprinting adjacency before/after; compare training loss and actual task metrics to frozen initialization.
- [x] Extract a connected, explicitly identified biological subgraph; measure retained behavior and compute relative to full.
- [x] Distill selected teacher behavior into Functional Fly and measure a declared agreement tolerance on held-out trajectories.
- [x] Mark acceptance only when measurements meet the roadmap criteria; retain null results.

### 3. Phases 9–11: tournament, adaptation and experiments

- [x] Audit existing hard/bias tournament and per-candidate credit tests against §83.
- [ ] Complete bounds, snapshots/rollback and drift checks for MB/readout updates; measure repeat-family adaptation against frozen controls.
- [ ] Implement isolated multi-seed benchmark, lesion and sensitivity commands with explicit compute accounting, saved configurations and results.

### 4. Phases 12–14: rollout inside FlyCode

- [ ] Shadow mode: record full/reduced recommendations while the current controller remains in charge.
- [ ] Bounded mode: allow only low-risk decision influence under bias control with explicit fallback.
- [ ] Full mode: enable only for a configured controller/checkpoint whose benchmark validation meets the recorded gate.
- [ ] Expose actual controller/rollout selection and provenance in the local UI; verify controller events, model calls and visual labels.

### Delivery

- [x] Update Russian/English README, research limitations and phase status with measured evidence.
- [x] Run focused tests during implementation, then full pytest, changed-file lint/type checks and UI acceptance for UI changes.
- [x] Publish completed, verified increments to the existing GitHub repository.

## Measured acceptance at this increment

Phase 5 passes real-graph toy/Docker acceptance. Phase 7 passes the same toy tasks with a much smaller circuit. Phase 6 improves imitation (.4444 → .7778) but ties task completion; Phase 8 misses its default agreement gate (.8889 < .9). Neither is marked accepted. Phase 9 already works. Phases 10–14 remain open; Hermes means FlyCode itself.
