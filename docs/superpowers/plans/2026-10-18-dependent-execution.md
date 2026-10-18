# Verified dependent execution Implementation Plan

> **For agentic workers:** Use the authorized native TDD workflow and independent read-only review. Execute each task to a checked working result; no approval is needed to repeat the already authorized execution method.

**Goal:** Execute genuine coding dependencies and resumable child waits without treating awaiting_review as success or finishing the workflow before verified integration.

**Architecture:** Separate immutable task-completion receipts from workflow IntegrationRecord. A receipt requires a stopped author, exact independently reviewed artifact, current owner criteria/evidence and pinned prerequisite receipts. Dependent workspaces compose those exact outputs over the original source in private storage; the final integrator applies the verified combined delta once. Preserve v1/v2 recipes and the meaning of result_id.

**Tech Stack:** Existing Pydantic contracts, one SQLite Store, native Runtime/Reviews/Integration, registered pytest, FastAPI/static views when exposing results.

**Spec:** `docs/Plan.md` §§100/101/104; ORC03/06/08 and AC01/04/07/14 in `docs/specs/connectome-orchestration.md`; aggregate delivery Tasks5/6/8.

## Global Constraints

- The parallel agent owns brain/data/training and its current CLI/importer edits. This work owns completion/dependency contracts and canonical runtime/projections; coordinate shared CLI changes before touching them.
- One active workflow; max4instances,64tasks,3attempts,60modelcalls,120tools,30min and verification reserve20%. Wait never holds a worker/pool slot or resets spending/deadline.
- WorkItem.dependencies require actual succeeded predecessors. Task success means checked task criteria; workflow success still requires the current integrated and applied result.
- No inference, process launch or filesystem preflight inside SQLite transactions. Every mutation rechecks metadata after filesystem preflight.
- Preserve immutable source/patch/review/evidence, physical project scope, dirty/untracked bytes, outbox atomicity, custody and fencing.
- Use feature tests after each change. Full repository/graph/live-model verification belongs to the authorized final completion audit, not every function.
- Do not publish the parallel agent's uncommitted files. Build packaged acceptance from an isolated snapshot of this work when the shared checkout contains its code.

## Review Focus

1. An approved-looking review with changed bytes, criteria, environment, lineage or missing evidence cannot issue task success.
2. Reopen/revoke/source change invalidates completion and descendants; a running descendant must lose publication authority before another writer is admitted.
3. A dependency receipt cannot be exchanged for a foreign task/attempt or silently recomputed against a new base; concurrent commands commit one receipt/event.
4. Sequential A:x0→x1, B:x1→x2 must work, while independent incompatible branches still conflict and never overwrite owner bytes.
5. Suspension must settle the exact owned process group before releasing capacity; retry/resume preserve all earlier costs, limits and continuation provenance.

### Task1: Verified task-completion gate

**Files:** add TaskCompletion to `domain/results.py`, completion_id to WorkItem in `domain/orchestration.py`, schema22 in `orchestration/store.py`; create `orchestration/completions.py` and `tests/unit/orchestration/test_task_completions.py`. Read every caller before modifying its contract.

**Interfaces:** `Completions(store, root).complete(scope, artifact_id, review_id, *, expected_revision, key) -> TaskCompletion`; `get(scope,id)`, `current(scope,id)`, SQL-only `metadata(scope,id)` and scoped `refresh`. Existing `result_id` remains a workflow IntegrationRecord ID.

- [x] RED real writer/reviewer: after an approved exact review, author becomes succeeded through the new gate, workflow remains running, owner source stays unchanged, dependent eligibility requires this success. Matching replay creates no extra effect or spending.
- [x] Add the immutable receipt, additive schema22 and two-phase host-owned completion gate. In one transaction recheck task/attempt/fence/profile/permission/Host state and review metadata, publish the receipt, finish the author and emit the event.
- [x] Cover rejected/foreign/self reviews, late ownership, paused/cancelled/deadline/clock rollback, tampered artifact/evidence, lost reply, two-connection race and outbox rollback. Bounded reads must reject special files promptly.
- [x] Refresh/invalidate receipts before admission and projection. Semantic changes/reopen/revocation invalidate descendants and stop their current ownership; metadata changes/archive preserve checked evidence. Run new cases plus directly affected review/Store checks; review and commit the checked gate.

```python
proof = Completions(store, runtime.root).complete(host, artifact.id, review.id,
    expected_revision=store.task(owner, task.id).revision, key='complete-author')
assert store.task(owner, task.id).completion_id == proof.id
assert store.task(owner, task.id).state == 'succeeded'
assert store.workflow(owner, workflow.id).state == 'running'
assert source.read_bytes() == original
```

### Task2: Immutable prerequisite composition and final integration

**Files:** `domain/orchestration.py` attempt/workspace fields, `domain/results.py` artifact lineage, `orchestration/{workspaces,artifacts,inputs,integration,integration_bindings}.py`; uniquely named dependency workspace/integration tests.

**Interfaces:** attempts pin exact prerequisite receipt IDs during admission; Workspaces.prepare derives private composite baseline from their immutable artifacts and stores an input snapshot, preserving the original source snapshot. Artifact changes are relative to that baseline and retain prerequisite receipt IDs. Final Integration accepts checked task completions with the legacy awaiting_review route.

- [x] RED real A:x0→x1 and B:x1→x2 on declared A→B: B's actual checkout sees x1; final source becomes x2 only after the final merged and applied checks. Missing/stale/foreign prerequisite never launches B.
- [x] Implement deterministic ancestry composition with exact before/after hashes and modes, scope checks and pinned lineage; verify saved private baseline identities after copy. Do not mutate user source or normalize input bytes.
- [x] Final integration derives one original→combined delta from validated topological lineage; independent conflicting writes still use the existing conflict route. Retain all task criteria and rerun outcomes on the combined and applied source.
- [x] Cover branching/join, overwritten prerequisite bytes, tampered ancestry, cycle/foreign base, a changed source, independent conflict, combined regression, cancellation/restart and preservation of dirty/untracked bytes. Run affected tests and commit the checked composition/integration.

### Task3: General approved dependency recipe

**Files:** `domain/pipeline.py`, `orchestration/{pipeline,dispatch,coordinator}.py`; dependency pipeline tests.

**Interfaces:** additive version3 recipe pins the owner-approved task DAG, roles, profiles, criteria, budgets and original source. Runtime may materialize only exact completed predecessor inputs and a new corresponding bound task revision, with durable receipts. V1/v2 gain no implicit new authority.

- [x] RED real subprocess A→B→join with no manual claims or owner commands between stages, truthful states and current final result; validate the whole DAG before enqueue. Checked within the existing three-pair sibling layout.
- [x] Implement event-driven completion/materialization using task-completion and composition contracts. Pin future bindings/semantic revisions in receipts and atomically recheck dependencies and budget before claim.
- [x] Cover independent progress during a blocked branch, race/restart/lost reply, failed prerequisite, pause, late worker, changed criteria/source, max tasks/depth and exhausted budget. Commit after actual verified completion. The hierarchy extension adds four writers/three nested aggregates, exact parent checks and the inherited Store structural/fence boundaries.

The version3 checkpoint covers independent progress, two-connection races,
failed outbox/restart, failed prerequisites, branch/root pause, all canonical
claim routes, changed source/criteria/evidence and exact cumulative budget.
Existing Store graph-limit/expired-fence checks also pass. This paragraph records
the initial three-pair checkpoint; the checked hierarchy continuation below
extends version3 under those same limits. Resumable parent authority stays in Task4.

### Task3 hierarchy continuation

1. Add `test_hierarchical_recipe.py`: four approved writers across nested
   aggregate groups (depth3), real A→B→join→fourth output, nine attempts and
   independently checked group criteria. Confirm the existing three-pair gate
   produces a real RED before changing source.
2. Extend version3 only to the existing64-task/depth3/children8 limits. Infer
   structural aggregate nodes from the exact approved graph; they have no
   profiles, inputs or execution budget and never receive attempts. Validate
   every node before enqueue; v1/v2 retain their sibling contracts.
3. Include every aggregate's owner-approved oracle in the final combined and
   applied checks. Only that gate may complete an aggregate. Exact graph,
   criteria, source and evidence revisions still invalidate the result.
4. Exercise missing aggregate approval, executable/leaf impostors, ancestor
   pause, failed aggregate criterion and post-completion criteria changes.
   Run feature/legacy compatibility, package checks and independent read-only
   review before publishing this extension. Do not close durable child waits.

### Task4: Durable child wait and bounded retry/replan

**Files:** `domain/orchestration.py`, canonical Store/Runtime/worker/message routes and their exact callers discovered before editing; native continuation tests.

**Interfaces:** explicit aggregate versus resumable-parent authority; host-owned suspension receipt pins continuation and child IDs/revisions/inputs. Resume creates a new fenced assignment under unchanged cumulative limits. Replanning creates a graph revision and never removes required criteria or expands permission/budget without owner authority.

- [ ] RED actual parent worker suspends, exact group settles and slot is freed; children execute; only succeeded mandatory children allow a new fenced parent attempt. Duplicate wakeup/restart cannot start twice.
- [ ] Implement bounded durable waits, continuation/input lineage, dependency failure blockers, timeout/Stop and explicit retry/replan authority. Questions/messages remain addressed data and cannot self-grant rights.
- [ ] Verify retry/resume retain original spending/deadline, max3 attempts and pool4; no recursive child/question/replan avalanche. Parent and workflow each still require their own verified criteria. Commit checked runtime behavior.

### Task5: Common projections and publication

**Files:** discover actual tracker/web projection and view callers; update shared API/CLI/UI result views, this plan, handoff, Plan and README RU/EN.

- [ ] Expose scoped task completion versus workflow integration, currentness, input lineage, waits, last action and exact blockers from the same Store; no second task state or arbitrary path/argv input.
- [ ] Feature-only API/native/browser checks; keyboard/desktop/390/320 Stop and safe evidence, then relevant Ruff/mypy and isolated wheel/extracted CLI. Preserve failed measurements and source boundaries.
- [ ] Independent review, verified commit/push containing only this work. Do not close the aggregate goal: neural orchestration/admission/matrix and remaining Task8/final full verification require their own evidence.

## Self-review

Task success/dependencies→Tasks1/2/3; data flow and exact merged result→Task2; preapproved DAG authority→Task3; child wait/retry/replan→Task4; truthful access and delivery→Task5. Final integration and research admission remain separate gates. This plan addresses the real dependency requirement rather than introducing weaker artifact-only edges.


## Task1 checked checkpoint, 2026-10-18

The standalone completion gate is checked; source and package boundaries,
all retained failures and the legacy reopen correction are in
[the report](../../validation/2026-10-18-task-completion.md). Native40 and final
24 overlapping correction/scope cases pass; final wheel10 cases pass. The earlier
230-case compatibility run had4 diagnosed failures, all reverified individually;
it was not repeated wholesale. Ruff19/mypy7 and exact package170/README/CLI pass.

Task2 RED is `test_dependent_workspaces.py`: real approved A output is x1,
actual claimed B checkout remains x0. Its failure is retained and excluded from
Task1 publication. Implement pinned composition next; do not treat eligibility
alone as a working dependency route. General recipes, child waits, common views
and aggregate/research acceptance remain open. Parallel brain edits stay separate.

## Task2 checked checkpoint, 2026-10-18

Pinned composite workspaces, original-to-final integration, conflict continuation,
registered imports and both resolved-root reopen layouts are checked. Initial30,
affected compatibility17 and overlapping correction deltas3/2/1/1/5/6 pass;
extracted-wheel6 and final scope-corrected wheel2 pass. Package171/UTF-8 README/CLI, unchanged279 inputs,
Ruff11 and scoped mypy10 pass. All failures and source boundaries are retained in
[the report](../../validation/2026-10-18-dependent-composition.md).
The final source filter excludes parallel controller operations/schema numbers;
the earlier snapshots and corrected contract assertions remain retained.

Next Task3: preapproved general dependency recipes must advance through completion
receipts automatically, with no manual claims or owner commands between stages.
Internal composition does not grant that authority to existing v1/v2 recipes.
Task4 waits/retry/replan, Task5 common projections and aggregate acceptance stay open.

## Task3 bounded checkpoint, 2026-10-18

Version3 executes three preapproved writer/reviewer pairs with acyclic writer
edges and one final integrator. Exact completions and future semantic bindings
advance automatically; v1/v2 retain their previous contracts. Final25 native,
compatibility6, Store limits/expired-fence3 and extracted-wheel7 pass. Ruff4,
scoped mypy3, package171/UTF-8 README/CLI and280 unchanged inputs pass. Counts
are overlapping; all failures and boundaries are in the
[report](../../validation/2026-10-18-dependency-recipe.md).

This publishes the bounded working route, not full general hierarchy acceptance.
Complete the remaining Task3 hierarchy scope, Task4 durable suspension/retry/
replan and Task5 common result projections before closing this subplan. The
aggregate goal and neural/research acceptance remain separate and active.

## Task3 hierarchy checkpoint, 2026-10-19

Version3 supports an acyclic developer/reviewer DAG with nested structural
aggregates under the existing64-task/depth3/children8 limits. The actual13-task
route has four authors, three groups, nine process attempts and nine separate
merged/applied requirements. Groups have no execution budget/profile/attempt;
only their own accepted criteria allow final completion. Prior attempts or
assignments leave a parent subject to ordinary author coverage.

Expanded native7pass/1fail363.11s retains a confirmed budget barrier in an
underfunded test scenario; the corrected positive1 passes124.64s. Twenty affected
compatibility cases pass367.52s; final extracted-wheel4 pass173.03s. This is no
claim of a complete repeated8-case native run. All six runtime files match these
final boundaries; test budget changed45→73, without changing the shared120 cap.
The successful route spends4 model/93 tool calls with no reservations/unknowns.
Ruff7/scoped mypy6, exact package171/UTF-8 README/CLI and281 packaging inputs pass.
Full failures, source boundaries and review limitations are in the
[report](../../validation/2026-10-19-dependency-hierarchy.md).

Task3's structural hierarchy route is checked. Task4 must distinguish a real
resumable parent from these non-executing groups and preserve a durable partial
checkpoint across stopped process/slot release and new fenced assignment. Retry/
replan, Task5 common views and aggregate/neural/research acceptance remain open.
