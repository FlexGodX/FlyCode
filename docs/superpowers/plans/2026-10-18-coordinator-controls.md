# Coordinator controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the Tasks interface to the shared coordinator and keep authorized Stop reachable after source loss, page reload and application restart.

**Architecture:** Existing protected attach/status and pause/cancel remain authoritative. A bounded operational Stop projection derives four receipt fields from durable actor/project grants, without fresh card scope or private task content. The browser renders canonical responses and retries an unacknowledged Stop with its original command key.

**Tech Stack:** Existing Python/Pydantic/SQLite/FastAPI and packaged HTML/CSS/JavaScript; real Chromium and owned native application processes.

**Spec:** `docs/Plan.md` §§100–104; `docs/specs/task-tracker.md` TRK-02/04/09/10, TT-04/09/14; `docs/specs/connectome-orchestration.md` AC-07/08/12. Continue the existing authorized native execution method. Aggregate Tasks5–8 and scientific §§90–99 remain required.

## Global Constraints

- One active workflow; max4instances,64tasks,3attempts,60modelcalls,120tools,30min; verification reserve20%.
- No inference, socket/process work or physical-folder inspection inside SQLite transactions.
- Server-issued actor/project authority; token/origin protection and strict bounded pagination.
- Stop grants never authorize task content, comments, resume, bindings or execution approval.
- Receipt state never fabricates process-stop confirmation or released unknown expenses.
- Preserve classic/Desktop chats, local non-Git projects, projectless chats and personal model defaults.

## Review Focus

1. Root loss plus reload must discover only the caller's Stop handles, without browser task caches or private metadata.
2. Accepted Stop plus lost reply must keep the same operation/revision/key even when refresh advances the canonical revision.
3. Revision conflict updates the visible version and waits for another explicit user action; no automatic retry of intent.
4. Late card/history/status replies must not restore private content or a different task after scope loss/selection changes.
5. An unavailable coordinator must not hide Stop; attach must not occur on page load, and 320px/keyboard controls remain reachable.

### Task1: Bounded Stop discovery

**Files:** `src/flycode/domain/project.py`, `src/flycode/orchestration/projects.py`, `src/flycode/web/tasks.py`; `tests/unit/web/test_canonical_tasks_api.py`, `tests/unit/orchestration/test_project_stop_grants.py`.

**Interfaces:** Move the existing four-field `TaskStopReceipt` to the shared project contracts; add `TaskStopPage(items, next_cursor)`. `Projects.stop_page(actor_id, *, after=0, limit=50)` selects granted canonical task rows in rowid order with limit+1, retaining nonterminal tasks and any current attempt. `GET /api/tasks/stops?after=0&limit=50` is registered before dynamic task IDs and never calls `issued_scope` or `refresh_results`.

The direct projection contract used by the checks is:

```python
page = Projects(store).stop_page(actor_id, after=0, limit=1)
for receipt in page.items:
    assert set(receipt.model_dump()) == {'task_id', 'revision', 'state', 'paused'}
if page.next_cursor is not None:
    following = Projects(store).stop_page(actor_id, after=page.next_cursor, limit=1)
    assert {item.task_id for item in page.items}.isdisjoint(item.task_id for item in following.items)
```

- [x] Write actual HTTP RED: grant through successful prior card resolution, replace source, restart app; fresh card403, Stop page200 with exactly task_id/revision/state/paused. A never-resolved actor gets an empty page, including with supplied foreign actor/project query strings. Snapshot command/event/reservation/model/tool tables before/after the read.
- [x] Write keyset RED: interleave foreign tasks, cancel actual drafts, pause one; successive limit1 pages have each eligible own ID once. Reject after<0/after>2**63-1 and limit0/101. Missing token/foreign origin deny access. No grants are minted by discovery.
- [x] Implement `TaskStopReceipt` and `TaskStopPage` as frozen extra-forbid Pydantic contracts. Reuse exact grant validation with `stop_command`; select only server actor's grant rows and canonical workflow/task project matches. Return no WorkItem, title, path, objective, event or artifact.
- [x] Run selected grant/API checks; preserve RED and passing XML/logs. Check Ruff/mypy for changed Python.

### Task2: Reachable coordinator and Stop controls

**Files:** `src/flycode/web/static/tasks.html`, `tasks.css`, `tasks.js`; create `tests/browser/coordinator.cjs`; extend existing browser acceptance only where actual Stop routing changes.

**Interfaces:** `GET /api/tasks/coordinator` checks status, explicit empty-body POST attaches, `GET /api/tasks/stops` supplies minimal fallback rows, `POST /api/tasks/{id}/stop` handles pause/cancel. Other commands continue through fresh-scope `/commands`.

- [x] Browser RED: load Tasks without automatic attach; click the real attach button, observe server-issued session, then reload and attach from another app to the same session. Record before screenshot and HTTP results.
- [x] Browser RED: open a real task, lose root, reload, use the minimal Stop view. Confirm fresh card403 and no title/description/history/comment/resume controls. Exercise lost accepted response and actual409; the next explicit click uses either original replay body or refreshed revision as appropriate.
- [x] Add separate coordinator status/button and a minimal unavailable-task Stop section. Render literal text only; expose no socket/nonce/path details. Clear stale detail/history on denied scope. Keep Stop reachable independently of service status.
- [x] Route pause/cancel through `/stop`; keep pending Stop bodies until acknowledgement or a definitive server conflict. Refresh canonical projections after acknowledgement, without treating the receipt as process absence.
- [x] Verify responsive 390/320 layouts, keyboard activation/focus, before/after screenshots and unchanged classic/Desktop/project/projectless workflows.

### Task3: Real runtime composition and publication

**Files:** a UI-only browser fixture adapter reuses `managed_pipeline`, `native_app`, `owned_service_control`; validation report, Plan, handoff, README RU/EN.

**Interfaces:** Own actual app/Node Popen handles; use a separate inherited test-only socketpair for phase commands and the existing private daemon cleanup channel. Never signal a discovered PID or add test-only production routes.

- [x] Run review/integration with actual held verification subprocesses and deterministic local model HTTP; first browser explicitly launches, first app process exits, second app/browser retains the session. Replace source only after successful prior resolution, then reload and issue pause/cancel via UI.
- [x] Independently check actual stopped custody, preserved spending/deadline, retained unknown reservations, no new model calls and unchanged original/replacement owner bytes. Record Stop HTTP latency and canonical replay identity.
- [x] Freeze all Python/static/tests/config/README bytes. Because Task1 changes server contracts, run final full suite, relevant Ruff/mypy, wheel/extracted CLI, real graph/model smoke; browser evidence must cover main and packaged assets. Preserve all failed/setup outcomes separately.
- [ ] Reconcile reports against exact registered IDs and immutable artifacts, archive regular-file evidence, then commit/push checked changes to public FlexGodX/FlyCode. No aggregate or scientific completion claim without its remaining evidence.

The publication checkbox is resolved by the private publication receipt after the checked commit/push; this saved pre-publication source does not claim a future commit.
