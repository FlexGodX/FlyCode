# Bound worker dispatch implementation plan

**Goal:** automatically select an owner-approved task/instance pair in the canonical
store and launch the existing real Runtime, without manual per-attempt claims.
**Spec:** Plan §§100/104, connectome-orchestration ORC-03/05/08 and Task5 of the
2026-10-16 delivery plan. Native execution is already authorized. This worker
slice leaves review/integration bindings, child-yield/recovery and aggregate
Tasks5–8 open; existing verification/completion gates stay authoritative.

## Files and interfaces

- Domain `orchestration.py`: frozen `WorkerBinding` (ID, task/workflow/project,
  task/workflow semantic revisions, contract hash, owner, source/runtime roots,
  source subject hash, brain/seed, version/previous binding, creation time).
  `AgentAttempt.execution_binding_id` is optional for backward compatibility.
- Store: schema12 immutable versioned worker bindings/current index in this same
  SQLite database. `dispatch_bound(scope, *, runtime_root, binding_ids, key)`
  returns existing `ClaimResult`. It ranks all eligible task/instance pairs,
  using candidate-specific remaining reservations and original baseline/aging.
  Reuse the checked automatic-reservation prototype, preserving old `claim` API.
- New `orchestration/dispatch.py`: `Dispatcher(store, root)`,
  `bind(owner_scope, task_id, source, *, expected_revision, key,
  expected_binding_id=None, brain='functional', seed=0) -> WorkerBinding`,
  `binding(scope, task_id) -> WorkerBinding`, `tick(runtime_scope, *, key) -> ClaimResult`.
  Owned process handles are ephemeral; tasks/attempts/status/spend stay canonical.
- Runtime/Workspaces: optional exact expected source hash, checked before worker
  spawn. Existing direct callers keep their behavior. Unsupported binding modes
  never become generic worker execution.
- Tests: new unique `tests/unit/orchestration/test_dispatcher.py`; extend existing
  store/workspace tests only for changed public contracts.

## Steps and verification

1. RED owner approval and source authority. Register a binding for an inert draft;
   require exact captured hash and contract, zero model/tool calls/attempts.
   Reader/runtime/foreign scope fail before source inspection. Repeated key returns
   the same record; replacement requires exact old binding ID and task revision.
   Binding writes, event and command receipt commit atomically. Old versions remain.

   ```python
   bound = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
   assert bound.contract_hash == task.contract_hash
   assert store.task(owner, task.id).state == 'planned'
   assert not store.attempts(owner, workflow.id)
   ```

2. RED stale contract, DAG, source and other runtime root. A tick excludes these
   before effects/reservation. Capture/source inspection happens outside SQLite
   transactions; current binding identity and semantic stamps are rechecked inside
   atomic dispatch. Root/source paths come only from this trusted host API.

   ```python
   source.joinpath('src/parser.py').write_text('value = 2\n')
   result = dispatcher.tick(host, key='changed-source')
   assert result.attempt is None and 'snapshot_changed' in result.reasons
   assert store.budget(owner, workflow.id).spent == BudgetAmount()
   ```

3. RED global baseline selection and races. Make the highest-priority task match
   the last idle instance in stable order. It must still win. Two connections
   racing for the same assignment create one attempt/reservation/event. Unbound
   tasks never consume a slot. Replay never launches a second worker. Pending
   admission is serialized until actual start/failure, preserving eighth-start
   aging rather than relying on process-ready order. Preserve4/64/3/60/120/30min,
  20% verification reserve, dependencies, scope conflicts, pause and original spend.

4. RED actual HTTP/process execution through `tick`, with no manual `claim` in
   the test. Verify private corrected bytes, untouched owner bytes, canonical
   worker/model/tool events, actual stop and original budgets. Late/stale owner
   approval never produces a second writer. A pre-spawn known failure can settle
   only when there is no worker/execution lane; uncertainty retains ownership.
   Current Qwen probe failures remain separate and cannot be relabeled as success.

5. Verify targeted contracts, then compose the checked implementation into main.
   Freeze source/README for full exact-ID collection, Ruff/mypy, extracted wheel,
   CLI and actual graph. Register any new live model probe/case explicitly and
   retain all failures. Update Plan/handoff/RU+EN README, publish a checked commit.
   Worker dispatch alone does not close Task5 or substitute for dispatcher-driven
   review/integration/current verified completion and actual child waiting.


## Checked worker slice

Final source299775468f46bcef4bb465e13b6d6edc6427f9a4d0c877fcc22cff7bc85e98fe:
886 pass/8 skip (894 exact IDs), wheel40, graph5, Ruff/mypy48 and CLI10.
Actual English Qwen worker through tick passes81.14s/3 models/5 tools; private
artifact only, owner bytes preserved, physical stop and zero uncertain calls.
The compact primary probe passes; previous timeouts remain failed. First full
wrapper failure and replay RED/fix remain in evidence. See the dedicated report.
This completes the bounded worker deliverable above, not aggregate Task5.
