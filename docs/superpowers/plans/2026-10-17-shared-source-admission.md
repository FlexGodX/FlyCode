# Shared source admission implementation plan

> Execute the approved delivery goal directly. Use the existing isolated worker
> for the source ledger/integrator boundary and the main checkout for legacy
> custody; reconcile both before final measurements.

**Goal:** exclude concurrent FlyCode writers to the same or overlapping source
folders across legacy chats and canonical integrations, including restart.

**Architecture:** one versioned source-claim ledger in the existing SQLite Store.
A claim binds resolved folder/ancestor identity, a private coordinator ownership
object, exact process receipts and the actual legacy run or integration owner.
No lease timeout, status label or PID establishes stopped ownership. Local tools
that execute project code use a versioned owned-process adapter; normal history
and existing single/dual model defaults remain independent of canonical tasks.

**Tech stack:** existing Python/Pydantic/SQLite, POSIX custody supervisor, pytest.

**Spec:** Plan §§100/103/104; connectome-orchestration ORC07/08;
task-tracker TRK09; aggregate delivery Tasks5/6/8 remain open.

## Constraints

- One workflow;4 instances/64 tasks/3 attempts/60 models/120 tools/30 minutes;
  preserve20% verification reserve. No filesystem/process/inference in SQL tx.
- Preserve dirty/untracked user bytes, legacy history, non-Git projects and
  projectless chats. Do not manufacture WorkItems or completion verdicts.
- Unknown external effects remain blocked; local process custody does not prove
  Docker/database/remote-resource shutdown or hostile sandbox isolation.
- Freeze source before final full/wheel/real-model measurements; retain REDs and
  all subsequent outcomes outside Git, publish checked changes with RU/EN docs.

## Review focus

Overlapping parent/child paths and symlink aliases must conflict; independent
folders may proceed. Missing/replaced lock or process receipt must not release
ownership. An HTTP response after Stop cannot authorize a write. An integrator
crash after apply retains the source until safe rollback/reconciliation. Restart
cannot treat `interrupted` as process/effect stop proof.

## 1. Ledger and canonical integrator

Files: new `domain/source.py`, `orchestration/sources.py`; modify
`orchestration/store.py`, `orchestration/integration.py`; source admission tests.

- [x] RED: two connections contend in both orders, aliases/ancestor roots,
  independent folders, inode replacement, missing/held custody, restart.
- [x] Add schema15 claims with immutable typed owner/root/coordinator bindings.
  `Sources.prepare(root)` performs filesystem identity reads outside SQL;
  `acquire(...)` atomically rejects overlaps; `guard`/`bind_process` use exact IDs.
- [x] Acquire inside the integrator's existing initial transaction/lane event,
  repeat source capture under claim, retain until terminal settlement/rollback.
- [x] `settle(claim_id)` independently holds coordinator and every stopped
  process receipt, checks actual linked calls/attempt state, then revision CAS.
  No public boolean release or timeout takeover.
- [x] GREEN: real Integration and legacy overlap, applied-crash recovery,
  changed source and existing result-delivery negative checks.

## 2. Legacy custody and compatibility

Files: `web/runs.py`, new `web/execution.py`, new `web/tool_worker.py`;
`verification/custody.py`, `domain/verification.py`; legacy/custody tests.

- [x] RED: two separate managers cannot write one registered folder, and Stop
  cancels an actual pytest with a descendant before a new source owner enters.
- [x] Persist optional `RunRecord.source_claim_id`, acquire before execution and
  hold a private coordinator lock through thread completion. Use a new Store
  connection per thread; keep legacy history separate from canonical attempts.
- [x] Add `tool-custody-v1` and an `on_prepare(ref)` callback before supervisor
  spawn. Its durable source binding precedes every project-code process.
- [x] Execute subprocess/project-code tools via a fixed internal worker with
  an immutable hashed request and bounded output; preserve ToolResult semantics.
  Pure file operations retain existing functions under the current source claim.
- [x] Bind primary/scorer HostTransport owner to the actual run ID; late/unknown
  model calls prevent release. Missing scorer remains disabled.
- [x] Restart/finish settlement validates ownership and receipts; unknown effects
  retain a blocker. Do not infer stopping from a thread Event or history label.
- [x] GREEN: native Stop/restart, process-receipt tamper, history/defaults,
  non-Git/projectless chats and real model-HTTP serialization.

## 3. Composed verification and publication

- [x] Review both diffs; run focused source, custody, web, result-delivery tests.
- [x] Freeze source; full pytest, relevant Ruff/mypy, extracted wheel CLI,
  biological graph and real local-model English smoke. Retain all outcomes.
- [x] Update Plan/handoff, aggregate plan, README RU/EN and evidence report with
  measured scope and remaining Tasks5–8. Commit/push only checked bytes.

## 4. Native source identity correction

- [x] Retain actual REDs for direct/nested root replacement between final guard
  and native rename, source parent creation, and incorrect absolute traversal
  after opening the claimed root descriptor.
- [x] Verify root identities when opening native descriptors; traverse both parent
  paths relative to pinned roots and guard before the syscall. Source directory
  creation uses the same boundary; preserve displaced objects and existing rollback.
- [x] Main focused source/atomic/rollback/final-move38 pass47.33s; Ruff and mypy53.
  The earlier438e full972/package167/native PASS is retained but does not establish
  this later correction. Denied HTTP test setup is not a functional RED.
- [x] Refreeze and verify the updated complete collection/distribution/native gates:
  970 pass/8 skip/978 exact IDs,191 wheel, CLI10, real graph5, Ruff/mypy53,
  actual English pipeline66.532s and legacy Stop21.495s. Independent audit verifies
  three released claims/27 stopped receipts, current verdict and known spending.
  Prior full timeout retained; unchanged web cases run serially after non-web.
  Post-pin user relocation may still affect the original physical
  directory; neither old nor new code establishes hostile filesystem isolation.
