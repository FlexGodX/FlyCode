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

- [x] RED actual parent worker suspends, exact group settles and slot is freed; children execute; only succeeded mandatory children allow a new fenced parent attempt. Duplicate wakeup/restart cannot start twice.
- [ ] Implement bounded durable waits, continuation/input lineage, dependency failure blockers, timeout/Stop and explicit retry/replan authority. Questions/messages remain addressed data and cannot self-grant rights.
- [ ] Verify retry/resume retain original spending/deadline, max3 attempts and pool4; no recursive child/question/replan avalanche. Parent and workflow each still require their own verified criteria. Commit checked runtime behavior.

- [x] Explicit negative-review retry authority, exact failed-byte/feedback lineage,
  retained costs/deadline/max3, races/restart/Stop and final wheel46 are checked.
  [Report](../../validation/2026-10-19-review-retries.md). Graph revision/replan
  remains part of the unchecked combined acceptance above.
- [x] Existing unfinished-child graph revisions, immutable stage generations and
  carried exact proofs are checked (wheel33+delta1).
  [Report](../../validation/2026-10-19-graph-replans.md). New-branch admission,
  controller proposals and the combined Task4 acceptance remain open.

#### Native parent-continuation route

Add owner-only `Pipelines.approve_parent(scope, workflow_id, pairs,
integrator_id, source=..., expected_revisions=..., approval_hashes=...,
key=..., preparation_steps=1)`. Its additive version4 includes the root as a
writer; every other writer is a mandatory technical child. Reviews/integrator
remain mandatory acceptance stages, not children that must finish before their
own author's continuation. Version1/2/3 grants keep their existing meaning.

Create `tests/unit/orchestration/test_parent_continuation.py`: actual root reads
context in one bounded step, exact group stops, child repairs parser0→1 and is
independently completed, root resumes with those exact inputs and partial bytes,
edits extra0→4, then independent review and integration verify both goals. Root
has a lifetime2 model/2 tool budget; preparation consumes1/1, leaving1/1 for a
new fenced attempt. All state transitions follow one inert owner approval.

Keep typed wait/checkpoint/resume records in `pipeline_receipts`, with atomic
events and exact original task/binding/child revisions. Use SQL/model-only phase
checks in all canonical claim paths. Runtime caps preparation from the approved
recipe; it adds no model tool or worker RPC operation. Artifacts remain immutable
partial checkpoints until independent criteria actually pass. A publication/outbox
crash must resume from existing exact receipts without repeating preparation.

Extend actual Runtime/Workspaces/Artifacts/Completions callers only after reading
them. Restore a parent's partial delta only over compatible composed child input;
keep the child baseline for its eventual reviewed patch. Pin checkpoint/context
lineage and block conflicts/tamper. Exercise two stores, restart, Stop/deadline,
failed child, changed authority/evidence and cumulative limits before publication.
This route does not by itself close the separate bounded retry/replan acceptance.

#### Owner-approved retry after a rejected review

This continuation implements ORC-03 negative-review recovery under ORC-08 limits;
replanning remains a separate graph-version change. Extend only version4
`approve_parent(..., retry_policies=None)` with an explicit writer-ID mapping of
`RetryPolicy(max_retries, writer_budget, reviewer_budget)`. Default/missing grants
no retry. Per-attempt caps apply to manual, automatic and bound dispatch; all
previous costs and the original deadline remain authoritative. Root preparation
counts toward the global max3 attempts, so a root can preapprove at most one
post-review retry. Ordinary writers may preapprove at most two.

1. Create `tests/unit/orchestration/test_pipeline_retries.py` using real
   `parent_ready`: child lifetime2/2, reviewer8 tools, policy1 retry capped1/1
   and0/4. First writer edits parser0→-1, review requests changes; retry sees
   those exact failed bytes and review diagnostics, edits -1→1 and completes
   fresh review plus parent/final integration from the original approval.
   Assert distinct fences, both immutable artifacts/reviews, current final proof,
   unchanged source until integration, lifetime spending and no manual claims.
2. Add Pydantic `RetryPolicy` and `RetryCheckpoint` in `domain/pipeline.py`;
   optional lineage in `domain/results.py`. Create `orchestration/retries.py`
   for host-owned exact failure validation, bounded context/restoration and one
   atomic retry receipt/event. Keep `pipeline_receipts`; no schema migration or
   new worker RPC. Preserve old omitted-field/full-model artifact hashes.
3. In `pipeline.py`, validate policies before enqueue; store caps without changing
   existing request replay when the mapping is absent. After a confirmed exact
   `changes_requested` review, archive the old review-stage receipt/binding and
   save a typed reviewer reset state. The author keeps its unchanged contract
   and source binding, loses old assignment ownership and receives a new fenced
   attempt. New review binding chains to the old binding; final integration
   selects the exact current successful reviewer fence, retaining failed history.
4. `Store._eligibility` rejects reservations above the approved per-attempt cap;
   `_remaining_reservation` applies that cap for both automatic claim routes.
   Retry admission checks remaining task/workflow budgets, max attempts, time,
   uncertainty, source/criteria/profile/permission/Host and paused ancestry before
   the atomic recheck. Exhaustion saves a machine blocker and never refunds cost.
5. `Runtime`, `Workspaces` and `Artifacts` consume the exact retry checkpoint.
   Restore compatible failed bytes over the checked prerequisite baseline; append
   bounded immutable run/review data without dropping required diagnostics.
   New artifacts pin the prior failed artifact/review/evidence, so tamper/deletion
   invalidates completion. A message or failed model assertion grants no authority.
6. Exercise two connections, outbox rollback/restart/lost reply, root and child
   retries, max3/lifetime exhaustion, absent policy, inconclusive/unknown checks,
   changed bytes/criteria/permissions/source and Stop/deadline/Host races. Verify
   old version1/2/3/4 routes and retained hash contracts, then exact wheel/CLI,
   independent read-only review, README RU/EN/status/evidence and checked push.

Do not mark all Task4 complete from this recovery route: bounded graph replanning
and remaining aggregate acceptance still require their own actual verification.

#### Owner-approved bounded graph revisions

The first graph-replan increment revises objectives/dependencies of existing,
uncompleted child writers. Keep task IDs and all criteria/permissions/budgets;
no replacement IDs can reset lifetime spending or max3. It does not close new
branch creation, neural replan proposals or aggregate Task4 acceptance.

Files: `domain/pipeline.py` typed graph receipt/input, `domain/results.py`
optional artifact lineage; `orchestration/replans.py` owner CAS and immutable
chain; exact consumers `pipeline.py`, `store.py`, `continuations.py`, `retries.py`,
`artifacts.py`; test `test_pipeline_replans.py`.

1. [x] RED native rejected child reaches stopped review; `Pipelines.replan` is
   absent. Assert replacement objective, new fenced worker from original bytes,
   fresh review, parent continuation and final integration. Retain previous
   base recipe, receipts, author/review/evidence and cumulative ledger/deadline.
2. [x] Implement `Pipelines.replan(scope, pipeline_id, *, changes,
   expected_revision, expected_revisions, key)`. Owner-only, exact workflow and
   complete task revisions, strict objective/dependency whitelist, version4,
   at most2 graph revisions. Reject active/unknown custody, pause/cancel,
   resumed parent/integration, cycles/foreign edges, completed or already terminal writers, changed
   criteria, missing models/source/oracles/evidence, max3 or spent budgets.
3. [x] Append typed `GraphRevisionReceipt` to existing pipeline_receipts;
   original pipelines row stays unchanged. Pin base/previous hashes, owner,
   effective recipe, retained physical stage hashes and exact rejected-review checkpoints.
   Namespace future stages by graph revision. All execution readers use the
   same effective recipe and logical receipt view; stale grants fail closed.
4. [x] Atomically retire exact stopped author, reset its reviewer and bind a
   new writer contract without refunds, grant/deadline extension or SQL-held
   source reads. Carry parent wait and unchanged completed branches with exact
   receipts; preserve original checkpoint authority. New artifacts pin graph
   input so tampering old failure/graph evidence invalidates current result.
5. [x] Verify native end-to-end, two connections, duplicate/lost reply, rollback
   and restart; stale owner/task/graph grants, strict inputs, cycles, Stop,
   deadline, exhausted attempts, unresolved costs, source and receipt tamper.
   Run affected retry/parent/dependency routes and retained hash consumers,
   Ruff/mypy, extracted wheel and exact source inventory. Review and publish
   only checked bytes with README RU/EN and honest phase status.

#### New owner-approved branches in a graph revision

ORC-03 requires new subtasks to create a new DAG version. Extend the same explicit
owner replan command, with no legacy create/enqueue/bind calls and no replacement
of existing failed IDs. Keep graph revision bound2, max64 historical tasks,
max3 attempts, pool4,60 model/120 tools/30min and20% verification reserve.

Files: `domain/pipeline.py` new `BranchAddition` input only (no change to saved
GraphRevisionReceipt hash shape); `orchestration/branching.py` validates and
materializes new writer/reviewer pairs; `orchestration/replans.py` adds them to
one final CAS; test `test_pipeline_branches.py` and exact graph/hash consumers.

- [x] RED real parent wait and completed child, followed by explicit new branch,
  fails on absent `new_branches`. Assert server IDs, immutable original proof,
  two new tasks in new version, fresh model/pytest/review, parent continuation
  and final integration under original cumulative spending/deadline.
- [x] Add `BranchAddition(writer:TaskDraft, reviewer:TaskDraft,
  template_writer_id:str, approval_hash:str)` and optional
  `Pipelines.replan(..., new_branches=None)`. Missing/empty additions preserve
  old request idempotency. Canonical JSON revalidation must reject mutated model
  inputs before any source/evidence read. New dependencies refer to existing
  child writer IDs or request-local `new:<index>` references; resolve references
  to server UUIDs only inside the final transaction, including edges from changed
  existing writers and other new writers. No alias may survive persistence; writer/reviewer parents are the existing parent or an
  existing structural aggregate, with no owner-supplied task IDs.
- [x] Before final SQL: validate kinds, nonempty objective, approved tools/scopes,
  explicit budgets, matching writer/reviewer acceptance, supported existing
  worker template/source/runtime, profile roles/project/permissions/capabilities,
  owner oracle for both contracts, base snapshot and existing graph authority.
  Capture exact new profile/template models; model/filesystem availability stays
  outside the writer transaction. A failed required old task stays required.
- [x] In the final owner CAS only: `_new_task` allocates server IDs/short IDs;
  create ready/planned writer and planned reviewer, pin new WorkerBinding with
  approved template brain/seed and original source; extend integrator dependencies
  and semantic revision while retaining root/user acceptance and budgets. Validate
  the whole graph with all old history plus new rows, update the effective recipe
  tasks/profiles/branches/approval hashes and append one graph receipt/event.
  `_new_task` counter, inserts, bindings, integrator change and outbox roll back
  together. Do not change workflow semantic revision, which would invalidate
  carried original completions; the explicit graph grant owns this structure.
- [x] Native negatives: missing criteria/budget/permission, wrong reviewer role,
  foreign template/profile/parent/edge, changed template/profile after preflight,
  hostile argv/path/ID fields, insufficient limits (64/children/depth/31branches),
  unknown custody/pause/deadline, concurrent additions, rollback/restart/lost reply.
  Verify retained full-model/omitted-field hashes and original graph records,
  feature-scoped compatibility/static checks, exact wheel/CLI, README RU/EN and
  report every failed boundary before checked commit/push. Controller proposals,
  shared result views and full aggregate/scientific acceptance remain separate.

Checked new-branch checkpoint: [report](../../validation/2026-10-19-new-graph-branches.md),
final wheel36; old-replan34 and combined-brain3 pass. Boundary tests use configured
max_tasks6/max_children5; no literal64-task stress or full aggregate claim. The
combined Task4 checkboxes remain open until their full acceptance is established.

### Task5: Common projections and publication

**Files:** discover actual tracker/web projection and view callers; update shared API/CLI/UI result views, this plan, handoff, Plan and README RU/EN.

- [ ] Expose scoped task completion versus workflow integration, currentness, input lineage, waits, last action and exact blockers from the same Store; no second task state or arbitrary path/argv input.
- [ ] Feature-only API/native/browser checks; keyboard/desktop/390/320 Stop and safe evidence, then relevant Ruff/mypy and isolated wheel/extracted CLI. Preserve failed measurements and source boundaries.
- [ ] Independent review, verified commit/push containing only this work. Do not close the aggregate goal: neural orchestration/admission/matrix and remaining Task8/final full verification require their own evidence.

### Task5 result projection increment

Keep Task4 combined/controller acceptance open. Add presentation-only models in
`tracker/projection.py` and `tracker/results.py`; enrich TrackerService cards
through the real Completions.current/Integration.current consumers after the
metadata/cursor transaction closes. No schema migration, new execution command,
user path, argv or second result lifecycle. Preserve last_event, add last_action
that ignores lease/heartbeat bookkeeping and includes addressed graph events.

1. RED `test_tracker_results.py`: actual stopped parent wait, independently
   verified child, resumed parent exact inputs and completed integration must be
   distinct common views. Read page under real SQL and inspect current evidence
   outside transactions. Missing evidence and changed source must invalidate
   currentness without new spending. Include denied foreign project reads.
2. Add typed completion/integration/review/lineage/wait summaries with scoped
   server IDs and exact hashes only; never expose runtime/source/oracle paths.
   Snapshot card metadata and cursor atomically, inspect evidence outside SQL,
   and reject claims of currentness when task/references change during inspection.
   Reuse the same summaries and blockers for active AgentCards.
3. Task detail/list/board/agents render completion separately from integration,
   exact inputs, parent wait, current graph and retained review/evidence IDs.
   Use literal text nodes, existing keyboard dialog and Stop paths. No legacy
   interface markup changes or execution grant through presentation.
4. Check native/API/CLI equality and scope/token/origin, actual browser desktop/
   390/320, keyboard/Stop and stale result display. Preserve all failed boundaries;
   run only feature tests plus scoped Ruff/mypy, extracted wheel/CLI, review and
   checked commit/push. README RU/EN, handoff/Plan and exact evidence follow.

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

## Task4 parent-wait checkpoint,2026-10-19

Version4 owner approval now runs a genuine parent preparation, stopped checkpoint,
mandatory child/review, fenced parent continuation and independent final review/
integration. The real five-task/six-attempt route spends3 model/36 tools, with
no reservations or unknowns. Original deadline, parent lifetime2/2, dirty/untracked
bytes and exact checkpoint/child lineage remain pinned. Partial work is not success.

Final native34 pass158.57s; affected compatibility12 pass
118.64s; extracted-wheel6 pass89.32s. Ruff12 targets,
scoped mypy10, package172/UTF-8 README/CLI and304 unchanged inputs pass. Counts
are overlapping. All REDs, source boundaries, independent-review findings and
archive metadata are in [the report](../../validation/2026-10-19-parent-continuation.md).
The final ownership guard also retains paid late model/tool outcomes after graph
revocation; it blocks new work without stranding endpoint/tool reservations.

Continue Task4 with bounded retry/replan; its first actual parent-wait checkbox is
checked, the whole task remains open. Task5 common result/wait views and overall
neural/research acceptance remain active. No full/live-model/graph/UI/training run;
parallel brain/schema23–25 changes are preserved and excluded from this commit.
Development source:`/private/tmp/flycode-parent-paid-outcomes-green-whojmkvm`; delivery:`/private/tmp/flycode-parent-delivery-20261019`.

## Task5 shared-result checkpoint,2026-10-19

The presentation increment above is checked: final wheel13, compatibility28,
five real native browser states at1440/390/320 with keyboard/Stop, old Tasks
browser, Ruff4/scoped mypy3/package176/312 unchanged inputs and UTF-8 README/CLI.
Independent review's cross-card cache and stale display findings are fixed.
Historical checkpoint remains continuation lineage after resume, not active wait.
Frozen brain snapshot interoperability3 plus final delta1 passes; all21 native
boundaries and both package rounds remain in the
[report](../../validation/2026-10-19-task-result-projections.md). Counts overlap.
Safe result-artifact drilldown and public owner recipe/replan commands are still
open, so Task5 aggregate checkboxes stay open. Combined Task4 acceptance and
controller/neural/research/final acceptance stay open. No whole-goal completion.

## Task5 saved-material checkpoint,2026-10-19

Safe saved patch/report downloads now share CLI/HTTP/Tasks and exact scoped
historical provenance. Selected reads tolerate later author bookkeeping/review;
old missing manifests are reported beside intact later materials. Late old
catalogue/download scope errors preserve a newer task/Stop. Seven feature cases
pass from final extracted wheel127.33s; compatibility14, old Tasks browser,
Ruff6/scoped mypy5/package177/315 unchanged inputs/UTF-8 README/CLI and frozen
combined-brain2 pass. [Report](../../validation/2026-10-19-task-result-materials.md)
retains15 earlier boundaries and preparation failures. Counts overlap.

Task5 aggregate remains open for public owner recipe/replan adapters, together
with combined Task4 acceptance. The owner command must run on the existing Host
connection, resolve roots server-side, and import oracle bytes under expected
hash before runtime head activation. Catalogue profile SQL pins belong in the
final owner CAS if that authority is promised. No temporary CLI Host/recovery,
client paths/argv/approval hashes or implicit grant through preview/UI. Ordinary
TaskCommand retry already exists; exact rejected-review policies belong initial
recipe approval. Controller/neural/research/final acceptance remain separate.

## Owner-import prerequisite checkpoint,2026-10-19

Checked owner-import prerequisite,2026-10-19: expected oracle fingerprint is
compared before approval/head writes; existing registered-plan final SQL pins the
full profile row/project column. Final wheel22, native feature22, compatibility43,
Ruff3/mypy2, package177/316 unchanged inputs/UTF-8 README/CLI and frozen brain204
interoperability22 pass. Counts overlap; invalid acceptance-fixture RED and static
format correction are retained in [the report](../../validation/2026-10-19-owner-import-pins.md).
This is not owner parent/replan final pin wiring, staging/RPC or public commands;
combined Task4, aggregate/controller/neural/research/final gates remain open.
