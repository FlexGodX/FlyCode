# Owner-approved task message routes implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has authorized native execution in this continuing session.

**Goal:** Let an owner explicitly approve directed communication between tasks and exercise it through automatically dispatched workers, protected HTTP and the Tasks interface.

**Architecture:** Immutable grants bind the approved effective pipeline graph and its logical task contracts, with an atomic current-head CAS and retained grant history. Before each model request, the Host resolves logical tasks into exact live attempt endpoints; admission validates both the grant and the endpoint pins. A message-to-grant sidecar allows revocation to exclude pending/remembered context without changing immutable AgentMessage bytes. The existing pipeline metadata guard proves any approved materialization or retry transition; numerical revision increments alone grant nothing.

**Tech Stack:** Python >=3.10, Pydantic v2, existing SQLite Store, FastAPI, plain JavaScript, existing signed Host transport and Playwright.

**Spec:** `docs/Plan.md` §§100/104; `docs/specs/connectome-orchestration.md` ORC-06/09, AC-04/05/09/13; Task5 of `docs/superpowers/plans/2026-10-16-specification-delivery.md`.

## Global Constraints

- One active workflow; max4 instances,64tasks,3attempts,60modelcalls,120tools,1800seconds; reserve20% verification.
- At most200 deliveries,16KiB per envelope,4hops,TTL300seconds,3 explicit broadcast recipients.
- No SQLite transaction during inference. Messages cannot grant task/tool permission, approve review or alter task status.
- No biological topology, scientific admission or promotion changes. Preserve all parallel controller edits.
- Immutable PipelineRecipe/WorkerBinding/ReviewBinding/AgentMessage schemas and hashes stay unchanged.
- Component schema marker `counters['message_routes_schema']=1` is independent of public global22 and parallel global32; migrate all sidecars and marker atomically, refuse future component versions.
- Only directly related tests per feature; browser checks for changed UI, exact wheel and frozen parallel merge before publication.

## Review Focus

- Planned peer has no attempt yet: hide it initially, expose it on a later request without restarting the sender.
- Owner revokes a grant during HTTP or after tool admission: retain paid usage, publish no stale delivery, reject pending/remembered old-grant context.
- Source contracts materialize through approved immutable pipeline receipts: resolve only after the existing metadata guard proves them; unapproved semantic changes and graph replans invalidate the old grant.
- Duplicate/reordered/replayed grants and two connections: preserve the current head; a historical receipt never restores old authority.
- Single endpoint and worker scope overlap: no deadlock or second writer; use disjoint actual task write scopes and bounded real pytest gates in automatic acceptance.

---

### Task 1: Durable owner grants and component migration

**Files:** create `src/flycode/domain/message_routes.py`, `src/flycode/orchestration/message_routes.py`; modify `orchestration/store.py`, `owner_requests.py`, `owner_commands.py`; create `tests/unit/orchestration/test_owner_message_routes.py`.

**Interfaces:**
- `MessageRouteEdge(sender_task_id, recipient_task_id)`, `MessageTaskPin(semantic_revision, contract_hash)` and immutable `MessageRouteGrant` with server ID, owner/project/workflow/pipeline/graph hashes, logical pins, previous grant and time.
- `MessageGrantPin(id, sha256)` and `ResolvedMessageRoutes(grant, recipients)` are Host-only DTOs.
- `MessageRoutes.approve(scope, project, command)` returns an immutable grant; `MessageRoutes.view(scope, workflow_id)` returns the current owner projection without side effects.
- `MessageRoutes.live(scope, attempt_id, fence) -> ResolvedMessageRoutes` resolves only current approved task instances. `MessageRoutes.require(scope, attempt_id, fence, pin)` validates exact head/hash/graph/permission without filesystem IO inside the caller's transaction.
- New OwnerMessageRoutes mutation uses pipeline_id, expected workflow/task revisions, expected_grant_id, directed edges and idempotency_key; new OwnerMessageRoutesPreview uses workflow_id only. Both use existing server-selected project/scope/runtime.
- A distinct OwnerMessageRoutesResult extends only OwnerOutcome's result union, not retained result/recipe records.

- [x] Write failing tests for public22 upgrade, atomic rollback on DDL/marker failure, concurrent open, future component version, old record preservation and idempotent reopen.
- [x] Write failing owner tests: explicit edges, closed fields, scope/permission/CAS rejection, same-key replay, two racing head updates and empty revoke. Assert no attempts/model/tool calls or changed budgets/contracts/graph.
- [x] Implement three sidecars (`message_route_grants`, `message_route_heads`, `message_route_proofs`) and the component marker using separate execute statements in one BEGIN IMMEDIATE transaction; marker is last.
- [x] Implement bounded owner DTOs, immutable grants and head history using existing project preflight plus SQL pins and Host validation. Bind recipe task contracts and require exact pipeline metadata proofs for current derived contracts.
- [x] Run targeted tests, Ruff and mypy. Retain failing diagnostics and source hashes.

```python
result = OwnerCommands(store).execute(request)
assert result.error_code is None and result.result.grant_id
assert OwnerCommands(store).execute(request) == result
assert not store.attempts(owner, workflow.id)
assert store.budget(owner, workflow.id).spent == BudgetAmount()
```

### Task 2: Dynamic Host catalogue, grant pins and revocation

**Files:** modify `orchestration/messages.py`, `cortex.py`, `runtime.py`; extend `tests/unit/orchestration/test_owner_message_routes.py` and `test_agent_messages.py`.

**Interfaces:**
- `Messages.routing(scope, attempt_id, fence, *, static=())` returns a Host MessageRouting with exact endpoints and an optional MessageGrantPin.
- `Messages.validate_routing(...)` checks both grant and endpoints; existing capture/validate_endpoints remain available for trusted static callers.
- Extend `Messages.execute/send` with optional `route_proof`; validate before admission and inside publication. Save message_route_proofs in the same transaction.
- `AttemptCortex.bind_message_candidates(..., recipient_pins, route_proof=None)` keeps both authority and endpoint pins; use dynamic routing before every real request.
- Move automatic controller progress derivation into the existing Host activity path for dynamic routes; keep the existing static worker protocol compatible and pinned.

- [x] Write failing actual-HTTP cases for a future peer, head revoked during HTTP, regrant after old response, grant change after tool admission and revoked pending/remembered question context.
- [x] Implement dynamic lookup on each request and exact grant+candidate+endpoint validation. Keep TTL/hops/fanout/tool spending and old static SDK audit semantics.
- [x] Verify no SQL transaction is held by HTTP and Stop remains responsive; paused/reused recipients do not receive the old candidate.
- [x] Extend the local parallel registered-command bridge so its actual HTTP context and candidate IDs carry the same dynamic proof; preserve the complete neural setup.

```python
assert 'send_message' not in first_request_tools
assert recipient.id in later_request_prompt
assert late_candidates == [] and routing.error == 'message_route_stale'
assert store.budget(owner, workflow.id).spent.model_calls == 1
```

### Task 3: Protected owner API and Tasks form

**Files:** modify `web/owner.py`, `web/static/owner.js`, relevant browser owner harness/scenarios and API tests.

- [x] Add a read-only current-route preview through the existing token/origin/selected-project protected owner adapter; previews never add grants or initialize execution.
- [x] Add an explicit directed-edge form in the existing owner dialog with labelled sender/recipient choices, current routes, remove/revoke action and confirmation. Filter senders by approved communication permission; never expand task/profile permissions from the form.
- [x] Reuse saved exact request replay, workflow/task/head CAS and source-loss privacy behavior. Display stale-graph/revision errors without silently retrying a different request.
- [x] Check real HTTP for token/origin/foreign-project/unknown IDs and keyboard/desktop/390/320 screenshots; retain before/after evidence.

### Task 4: Real automatic question/answer and publication

**Files:** extend `tests/browser/owner_automatic_runner.py`, `owner-automatic.cjs` or a dedicated registered scenario; update README RU/EN, Plan, handoff and validation report.

- [x] Prepare a real managed parent pipeline with two child writer/reviewer pairs before owner approval. Use common read scope but disjoint actual writer scopes (`src/parser.py`, `src/second.py`) and two registered developer instances. Enable send_message in task/profile permissions before binding/approval.
- [x] Start through actual native app/owned Host attach and owner controls. No manual tick/claim loop in acceptance. Hold the first sibling in a genuine private pytest gate; second selects a question through actual HTTP, first consumes it and returns a correlated answer, second consumes the answer while still live.
- [x] Require actual message/tool ledger, exactly addressed attempts, bounded usage and no user source changes. Release gates, complete fresh independent reviews and current combined integration; repeat Stop/revoke/reuse negatives as scoped cases.
- [x] Freeze source, build and inspect the exact wheel/extracted CLI, run scoped tests/browser checks and frozen merged-controller compatibility. Preserve all genuine failures separately from harness preparation mistakes.
- [x] Update measured status, archive regular-file evidence and publish only checked source with README RU/EN. Keep unfinished task_request transitions, avalanche and scientific/final gates explicitly open until separately proven.
