# Shared Verification Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver TST-13 plan registration, start/cancel/rerun, status/events/artifacts and reproduction through the canonical Store, CLI, API and existing interfaces.

**Architecture:** Keep immutable subject/profile/plan records separate from mutable execution ownership. Metadata lives in the existing SQLite Store; captured bytes and evidence live in a pinned private directory outside the subject. Use the existing verification engine/oracles/evaluator and Coordinator stage pool, with explicit standalone budgets rather than fake agent tasks.

**Tech Stack:** Python3.12, Pydantic2, SQLite/WAL, existing pytest custody runner, FastAPI, packaged JavaScript/CSS.

**Spec:** `docs/specs/testing-module.md` TST-01..15, especially TST-05/08/11/12/13; `docs/Plan.md` §101/104; aggregate delivery Task8.

## Global Constraints

- «Метаданные/очередь — SQLite; большие файлы — серверное artifact storage.»
- «Standalone test plan имеет собственный явно видимый бюджет.»
- «Повторы сохраняют исходные результаты; изменение кода инвалидирует approval.»
- «API сохраняет loopback/origin/session-token требования.»
- «Клиент не передаёт raw argv, arbitrary URL или путь к manifest.»
- One active workflow; max4instances,64tasks,3attempts,60modelcalls,120tools,30min; reserve20% verification for workflows. No inference/process/filesystem preparation inside SQLite transactions.
- Standalone defaults:15min total,120s/check, registered pytest only; unsupported hostile sandbox must not execute. Standalone checks consume the same four Coordinator stage slots.
- Preserve existing classic/Desktop/projectless/non-Git behavior and `verify --repo`; no production/research admission from a suite pass.
- Native implementation is already authorized by the aggregate plan. Do not stop for missing superpowers execution skills or request another execution-method approval.

## Review Focus

1. Replacing a registered source/storage directory must deny fresh reads/execution; historical cancel remains possible.
2. Partial capture, special files, symlink substitution and source changes during capture must leave no published subject.
3. Same idempotency key with different IDs/limits must fail; simultaneous starts must create exactly one owner.
4. A crashed/late runner must not release capacity or publish success before exact process-group settlement.
5. HTML-like diagnostics, foreign artifact IDs and stale SSE cursors must not leak paths, execute markup or duplicate commands.

## File map

- Create `src/flycode/domain/verification_control.py`: server-owned catalogue, command, budget and execution contracts; schema version is separate from mutable revision.
- Create `src/flycode/verification/registry.py`: trusted subject/profile registration and scoped immutable plan creation.
- Create `src/flycode/verification/control.py`: transactional commands/events/claims and fenced execution receipts.
- Create `src/flycode/verification/execution.py`: actual engine adapter, custody callbacks, deadline/lease and recovery.
- Modify `orchestration/store.py`: atomic additive migrations; no second task queue/database.
- Modify `verification/engine.py`: execute a registered suite plan without silently replacing its ID/checks/snapshot; preserve acceptance route.
- Modify `orchestration/coordinator.py` and `host.py`: typed verification jobs in shared pool and recovery/drain/manual-busy checks.
- Modify `verification/cli.py`, `cli.py`; create `web/verification.py`; modify `web/app.py` and existing static views after discovering/reading their exact current paths.
- Tests: new uniquely named `tests/unit/verification/test_control_registry.py`, `test_verification_commands.py`, `test_verification_execution.py`; protected web/CLI tests and native browser acceptance.

### Task1: Trusted immutable subject/profile catalogue

**Files:** domain/verification_control.py, verification/registry.py, orchestration/store.py; test_control_registry.py; existing migration assertions.

**Interfaces:** `VerificationRegistry(store, directory: Path | None = None)`; `register_subject(scope, project_id: str) -> RegisteredSubject`; `register_profile(scope, project_id: str, draft: VerificationProfileDraft, *, oracle_root: Path | None = None) -> RegisteredVerificationProfile`; `subject(scope, id)`, `profile(scope, id)`, `subject_root(scope, id) -> Path`. These are trusted host/CLI registration APIs, absent from agent tools/HTTP bodies.

- [x] Write behavioral RED: capture dirty binary/untracked bytes of a non-Git project, mutate owner files, reopen Store, obtain unchanged copied bytes; reader/foreign scope and replaced source/storage deny access. No tools/model calls occur.

```python
subject = registry.register_subject(owner, project.id)
(root / 'dirty.bin').write_bytes(b'new owner bytes')
assert registry.subject_root(owner, subject.id).joinpath('dirty.bin').read_bytes() == b'\x00\xff original\r\n'
assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0
```

- [x] Run `.venv/bin/pytest tests/unit/verification/test_control_registry.py -q`; require feature assertion failures, not collection/setup errors.
- [x] Implement frozen ID-bearing models; atomic schema20 catalogue tables; pin private storage identity once. Capture outside SQL, compare source before/after, verify copied file hashes/modes, publish only after current project/storage recheck. Failed publication removes only its own staged directory. Profiles accept registered pytest and existing owner-approved acceptance contracts, rejecting arbitrary argv/version/targets.

```python
with store.transaction():
    if projects.get(project.id) != project:
        raise StoreError('project_changed')
    store.db.execute('INSERT INTO verification_subjects VALUES (?,?,?)',
        (record.id, record.project_id, record.model_dump_json()))
```

- [x] Add negative tests for capture mutation, no partial publication, symlink/special inputs, storage inside source, revoked project and invalid profile; rerun snapshot/plan/project/migration compatibility.
- [x] Review and commit the checked catalogue increment; keep phase25/TST13 open.

### Task2: Immutable plans and transactional commands

**Files:** registry.py, control.py, verification_control.py, Store migration; test_verification_commands.py.

**Interfaces:** `create_plan(scope, draft: VerificationPlanDraft) -> RegisteredVerificationPlan`; `VerificationControl(store).command(scope, plan_id, command: VerificationCommand) -> VerificationExecution | RegisteredVerificationPlan`; `get(scope, plan_id)`, `events(scope, plan_id, after=0, limit=100)`. Draft contains only registered subject/profile IDs, profile version, explicit budget and idempotency key. Commands contain operation/start/cancel/rerun, expected revision and key.

- [ ] RED real two-connection race: two start commands at one revision must create one queued execution; identical lost-response replay returns the first result, changed payload conflicts.

```python
first = control.command(owner, plan.id, start)
assert control.command(owner, plan.id, start) == first
with pytest.raises(StoreError, match='revision|idempotency'):
    control.command(owner, plan.id, conflicting_start)
```

- [ ] Implement plan binding through existing PlanBuilder/OracleRegistry for acceptance and exact captured suite checks for registered-suite. Import host-owned staged workspace/artifact snapshots with provenance checks; reject fabricated or cross-project SubjectRef/project/workflow/task/attempt combinations.
- [ ] Implement CAS revision/events/idempotency in one transaction; draft does not execute. Limits are explicit/cumulative across attempts; max3 user attempts, diagnostic reruns included in tool spending. Cancel changes durable state, never signals a client PID.
- [ ] Verify foreign scope, unsupported trust, stale profile, missing subjects, changed budget/key, completed rerun retaining old failure/evidence, cancelled plan requiring explicit new intent; commit after passing checks.

### Task3: Real registered execution and shared Host lifecycle

**Files:** engine.py, execution.py, control.py, coordinator.py, host.py; test_verification_execution.py.

**Interfaces:** `VerificationEngine.verify_plan(plan, roots, *, should_stop)` supports both declared scopes; `VerificationExecutor(store, execution_id, fence).run()`, `.recover()`; Coordinator uses tagged `('verification', id)` jobs in its existing stage_pool4, not a second executor.

- [ ] RED actual subprocess suite: start a frozen subject, record exact ProcessRef before spawn, cancel while sleeping, require stopped group and cancelled evidence. A source edit invalidates current approval, never changes captured inputs.

```python
control.command(owner, plan.id, cancel)
for ref in process_refs:
    with stopped(ref) as receipt:
        assert receipt.stop_confirmed
assert source.read_bytes() == before
```

- [ ] Execute outside SQL using invocation_executor/on_prepare/on_admit; check host session/fence/budget at GO, debit each invocation, heartbeat10s/lease30s, bound timeout by remaining deadline. Recheck immutable inputs and source/environment identity before current verdict projection.
- [ ] Integrate selection signature, shared capacity counting including uncertain groups, Host.recover/drain/_manual_busy, scope discovery after root loss and typed recovery fingerprint. Keep reservations/unknown spending until exact settlement. A late acknowledgement may record cost/receipt, never publish a new current success.
- [ ] Verify pass/fail/empty/skipped/xfail/malformed/flaky/timeout, diagnostic reruns and user reruns, crash/restart, draining, stale owner and concurrent workflow/standalone capacity. Commit passing native lifecycle increment.

### Task4: Protected API, CLI and safe artifacts

**Files:** web/verification.py, web/app.py, verification/cli.py, cli.py; discover exact web/CLI test paths before editing.

**Interfaces:** exact TST13 routes; CLI `verify --plan-id ID`, `verify --evidence-id ID --reproduce`; separate trusted registration subcommands for subjects/profiles. All commands consume Task2 contracts. API never accepts filesystem paths/argv/URLs.

- [ ] RED token/origin/scope/CAS/key and traversal/artifact confusion tests; actual CLI process registers and starts, exits while shared Host keeps running.

```python
assert client.get('/api/verification/plans/' + plan.id).status_code == 403
response = authenticated_client.post('/api/verification/plans', json=body_with_raw_argv)
assert response.status_code == 422
```

- [ ] Implement protected GET as well as mutation routes; server scope derives physical allowed folders. Separate minimal prior-grant cancel after folder loss; it grants no reads/restart. SSE supplies ordered cursor/sequence and bounded reconnect. Artifact ID resolves pinned regular file/hash, safe MIME/attachment, no executable HTML/private manifest paths.
- [ ] CLI preflight requires original resource versions/limits for reproduction, creates a fresh execution retaining all outcomes; preserves legacy --repo. Test removed evidence, changed environment/permissions, interrupted output and safe error messages; commit.

### Task5: Verification screen in both interfaces

**Files:** existing static classic/Desktop markup/scripts/styles and browser tests, discovered and read before editing.

**Interfaces:** Task4 projections only; model/execution controls stay independent; existing project/group/chat behavior preserved.

- [ ] Browser RED: create plan from subject/profile choices without raw paths; start, see actual check counts/cases/budget, cancel and rerun, view safe artifact. Reload/reconnect must not create duplicate executions.
- [ ] Render required/optional, baseline→target→regression→integration, exact snapshot revision/currentness, failed/skipped/flaky/infra classifications and blockers. Percent is completed required checks only; no ETA. Show recorded/live model origin explicitly and budget/reserve truthfully.

```javascript
await page.getByRole('button', {name: 'Остановить проверку'}).click();
await expect(page.getByText('Отменена', {exact: true})).toBeVisible();
```

- [ ] Verify keyboard/focus, dark-theme selected text contrast, desktop/390/320 Stop and failure reason without horizontal scroll; before/after screenshots; legacy project/projectless chats and model settings; commit.

### Task6: Frozen validation, evidence and publication

**Files:** docs/Plan.md, docs/handoff.md, README.md/README.en.md; bounded validation report and this plan.

- [ ] Freeze source/test/config bytes before full pytest; use isolated artifact root. Retain every failed run; never label a repeat as a clean first attempt.
- [ ] Run relevant Ruff/mypy, full pytest exact case IDs, opt-in real graph, actual English LM smoke with request/cost/STOP receipts, wheel build/extract and CLI/API/browser acceptance. Source stays unchanged during frozen measurements.
- [ ] Fresh reviewer checks whole patch against specs; correct with new RED/verification if needed. Recheck tracked bytes/evidence hashes and remote/public identity before commit/push.
- [ ] Update RU/EN README and honest phase status. TST13 complete only after all routes/CLI/UI/lifecycle scenarios above pass; aggregate Tasks5–8 and §§90–99 stay open until their own acceptance is proven.

## Self-review

Coverage: TST13 interfaces→Tasks2/4/5; immutable evidence/freshness→Tasks1/2/3/4;
TST05 lifecycle/recovery→Task3; TST08 all reruns→Tasks2/3; TST11 artifacts/reproduce→Task4;
TST12 budgets/capacity→Tasks2/3; compatibility/identity→Tasks3/5/6. Unsupported non-pytest/hostile profiles block explicitly rather than inventing support. Existing workflow review/integration verification remains on its common budget and is projected by the same UI without copying task state.
Review Focus1→Tasks1/3/4,2→Task1,3→Task2,4→Task3,5→Tasks4/5.
Native execution and aggregate scope remain unchanged; each unchecked task is required work.


## Task1 checkpoint —2026-10-18

Catalogue implementation and review are checked independently of the remaining
control tasks.31 new cases; focused68; full1236 exact IDs/1228pass/8expectedskip,
0fail/error with270 frozen files unchanged; wheel62/162 exact files/README/CLI;
FlyWire5 and Ruff13/scoped mypy4 pass. All failure/setup observations are retained.
[Evidence](../../validation/2026-10-18-verification-catalogue.md).
Tasks2–6 remain mandatory; this checkpoint does not complete TST13 or the goal.
