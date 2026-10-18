# Independent team pipeline Implementation Plan

> **For agentic workers:** Use the authorized native TDD workflow and independent review; the referenced superpowers execution plugins are not installed.

**Goal:** Execute several independent writer/reviewer pairs followed by one current verified joint integration, from one inert owner approval.

**Architecture:** Add a version2 recipe in the existing pipeline tables and dispatcher. Preserve version1 requests, persisted records and receipts. Each reviewer consumes one exact stopped artifact; the integrator consumes every pinned artifact and approved review. Ordinary dependencies still require succeeded, so cross-writer dependencies and child-wait remain a separate protocol.

**Tech Stack:** Existing Pydantic contracts, SQLite Store, Dispatcher, native Runtime/Reviews/Integration and pytest.

**Spec:** `docs/Plan.md` §§100/104, `docs/specs/connectome-orchestration.md` ORC03/05/08 and aggregate delivery Tasks5/6.

## Global Constraints

- The parallel agent owns brain/data/training; this increment owns only domain/pipeline, orchestration/pipeline and their tests/docs.
- One active workflow, max4instances/64tasks/3attempts/60modelcalls/120tools/30min and20% verification reserve. Existing max_children8 permits at most3 sibling pairs plus one integrator.
- Approval is owner-only and inert. No model, process or filesystem execution inside SQLite transactions.
- Preserve source dirty/untracked bytes, semantic revisions, profile/approval pins and exact stopped custody.
- Do not weaken succeeded eligibility. Writers have no dependencies; reviewers use artifact receipts; integrator depends on all reviewer IDs.
- Run only pipeline/integration tests affected by this feature; no full suite, graph or live-model/training run.

## Review Focus

1. A forged/missing/changed second writer binding must block every assignment before resource spending.
2. One branch waiting for publication must not prevent another independent branch's exact review; a failed review must never be skipped at integration.
3. Receipt names must bind the reviewer ID and remain unique across restart/two connections; no duplicated ready task or binding after lost replies.
4. Source/approval/semantic changes or a pause between preflight and commit must block materialization without partial inputs/events.
5. Individually passing patches can fail combined checks or conflict; retain both outcomes and leave user files unchanged.

## File map and interfaces

- `src/flycode/domain/pipeline.py`: additive `TeamPipelineRecipe` with pinned branch records; retain `PipelineRecipe` version1.
- `src/flycode/orchestration/pipeline.py`: `approve_team(scope, workflow_id, pairs, integrator_id, *, source, expected_revisions, approval_hashes, key, reruns=0)`; `get`/`advance`/`_guard` dispatch both versions using the same receipts and immutable stage bindings.
- `tests/unit/orchestration/test_team_pipeline.py`: uniquely named native acceptance and authority/race negatives; reuse existing fixture model server and actual process ownership helpers.
- Existing `test_pipeline_dispatch.py`, `test_result_delivery.py`: compatibility and combined-result semantics.

### Task1: Inert versioned authority

- [x] Write RED for two pairs: `approve_team(...)` returns version2; matching replay returns the same object; zero attempts/model/tool records and unchanged budget; v1 persisted records still load.
- [x] Run only this test and observe the missing feature assertion.
- [x] Add the versioned contract and validation of unique sibling roles, exact revisions/profiles/permissions/oracles, common source and every worker binding. Recheck them before atomic publication.
- [x] Cover stale second binding, duplicate role IDs, forbidden writer dependencies, incomplete reviewer dependencies, foreign/runtime scope and changed idempotency payload. No source read after a known invalid authority.

```python
recipe = pipelines.approve_team(owner, workflow.id, pairs, integrator.id, **approval)
assert recipe.version == 2
assert pipelines.approve_team(owner, workflow.id, pairs, integrator.id, **approval) == recipe
assert store.attempts(owner, workflow.id) == []
assert store.budget(owner, workflow.id) == before
```

### Task2: Actual branch reviews and one integration

- [x] RED real HTTP-fixture/native processes: two disjoint repairs on the same captured base, two independent exact reviews and one integrator, with no manual claim or owner command after approval. All required tasks complete only with a current joint result.
- [x] Generalize stage selection to choose an available unmaterialized branch; use `review:<reviewer_id>` receipts in v2. Keep v1 receipt names unchanged. Integration waits for every exact approved review and no active writer.
- [x] Atomically recheck recipe/tasks/worker pins/artifact producer sequences/receipt set and insert one binding, ready task, receipt and event. Integration binds the full artifact/review set and common source hash.
- [x] Cover two-connection materialization, restart replay, outbox rollback, pause/source/approval change, failed branch review and incompatible combined regression/conflict. Preserve all failures and owner bytes.

```python
assert store.workflow(owner, workflow.id).state == 'succeeded'
assert len(store.attempts(owner, workflow.id)) == 5
assert integration.current(owner, result.id)
assert store.budget(owner, workflow.id).reserved == BudgetAmount()
assert dirty_file.read_bytes() == before_dirty
```

### Task3: Checked publication

- [x] Run new cases plus directly affected v1 pipeline/integration checks; retain REDs, exact case IDs and unchanged measured sources.
- [x] Relevant Ruff/mypy, extracted wheel contract/native checks and independent review. Fix findings with behavioral REDs.
- [x] Update handoff/Plan and README RU/EN with the precise independent-pair boundary; commit/push only this increment. General child-wait/retry/replan and neural admission remain open.

## Self-review

ORC03 lifecycle→Task2; ORC05 eligibility→Tasks1/2; ORC08 bounded shared resources→Tasks1/2; exact review/completion→Task2; compatibility/publication→Task3. This increment does not implement arbitrary DAG edges, dynamic subtask creation, retry/replan or neural policy. It adds working multi-writer execution without pretending those separate acceptance criteria are satisfied.


## Checked checkpoint
+
+Final42 native v1/v2 cases and6 extracted-wheel cases pass;12 inputs unchanged,
+169 package files/README equal, Ruff3/scoped mypy2 and CLI help pass. The first
+missing-feature RED and the replay-helper correction remain retained. Independent
+review found no concrete defect; branch independence, two-connection uniqueness,
+pause/rollback, failed review, combined regression and conflict have real native
+coverage. [Report](../../validation/2026-10-18-team-pipeline.md).
+This closes the independent-pair increment only; general DAG/child-wait/retry/
+replan, remaining common UI and neural/research acceptance stay open.
