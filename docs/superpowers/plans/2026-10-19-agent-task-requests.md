# Agent task requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. The owner has authorized native execution in this continuing session.

**Goal:** Let an idle registered instance request a permitted bound task through the same central dispatcher, with Host-issued identity, durable replay and no new execution authority from message text.

**Architecture:** Owner issuance creates a short-lived context pinned to one idle instance, profile and canonical project in the current physical Host session. An authenticated request becomes a bounded durable queue record. The coordinator consumes it through Dispatcher.select and Store.dispatch_bound; assignment, reservation, event and final request outcome share the existing writer transaction and execution pool.

**Tech Stack:** Python3.10+, Pydantic v2, SQLite, FastAPI and the existing authenticated local Host transport. No new package dependency.

**Spec:** docs/Plan.md §§100/103/104; docs/specs/task-tracker.md TRK-05, TT-03/04/05/06/11/16; docs/specs/connectome-orchestration.md ORC-03/04/05/06/08/09, AC-04/05/07/08/14; Task5/8 in 2026-10-16-specification-delivery.md.

## Global Constraints

- One active workflow; max4instances,64tasks,3attempts,60modelcalls,120tools,30min; reserve20% verification.
- No SQLite transaction while inspecting physical source trees, performing HTTP inference or executing tools.
- task_request message content remains untrusted data. request_task is a separate authenticated queue command; no free-text task creation, assignment or permission expansion.
- Preserve retained AgentInstance/AgentAttempt/WorkerBinding/PipelineRecipe hashes and public schema22 versus parallel schema32 migrations. New models and namespace component migration only.
- Use exact public31b71f6 in an isolated copy; preserve all parallel brain changes. Scoped tests per feature; full suite only at final aggregate gate.
- Credentials expire after300s and on Host generation/profile/instance-context replacement. Store only a credential hash, never the raw credential in events or command receipts.
- Requests accept idempotency, exact instance revision, bounded capability/availability hints and optional preferred task plus revision; no caller role, scope, reserve, binding, path or model override.

## Review Focus

- Owner session token alone must not impersonate an idle instance; forged/path-swapped/foreign contexts have no queue or budget effect.
- Lost reply/concurrent requests must yield one durable request/assignment/reservation; key reuse with changed payload conflicts.
- Host restart, expiry, profile supersession or another assignment invalidates new admission while retaining old audit records.
- Queue pause/no eligible work returns a bounded reason/backoff and does not start a busy loop or hold the Host serving thread.
- Accepted assignment must carry the exact execution binding and run in the common pool; source changes/fence loss/verification reserve remain real blockers.

---

### Task 1: Idle-instance credentials and component migration

**Files:** create src/flycode/domain/agent_requests.py and src/flycode/orchestration/agent_requests.py; modify orchestration/host.py and store.py; create tests/unit/orchestration/test_agent_request_identity.py.

**Interfaces:** `AgentRequests(store).issue(scope, project, *, instance_id, expected_revision, key) -> AgentRequestCredential`; `authorize(instance_id, credential) -> AgentRequestContext`; `AgentRequestContext` pins server ID, project ID, instance/profile IDs/version/hash, instance revision, Host registration/session/generation, owner and expiry. `AgentRequestCredential` returns context and a context-ID/HMAC credential. Host owns one memory-only random signing nonce per immutable session.

- [x] Write real managed-Host tests for issuance/replay without attempts/models/tools, owner-role/project/idle/version checks, forged/foreign credentials, expiry, replaced context, real Host restart and no secret persistence.
- [x] Run the identity file before implementation and retain the actual missing-feature RED.
- [x] Add atomic component1 migration for agent_request_contexts, agent_request_heads and agent_task_requests, plus marker-last; test rollback, concurrent open, future component/global schema and retained database bytes.
- [x] Implement issue preflight outside SQL, final Host/project/profile/instance CAS inside SQL, immutable context plus head and owner command receipt. Derive raw token only from private Host nonce and context identity; validate constant-time token/hash and scope.
- [x] Run scoped tests and typed/static checks; preserve all failure evidence.

```python
issued = service.issue(owner, project, instance_id=instance.id,
    expected_revision=instance.revision, key='idle-context')
assert service.authorize(instance.id, issued.credential) == issued.context
assert service.issue(owner, project, instance_id=instance.id,
    expected_revision=instance.revision, key='idle-context') == issued
assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
assert issued.credential not in '\n'.join(row[0] for row in store.db.execute('SELECT data FROM commands'))
```

### Task 2: Durable request queue and common bound admission

**Files:** extend domain/agent_requests.py; create orchestration/agent_admission.py for AgentTaskRequests and tests/unit/orchestration/test_agent_task_requests.py; modify orchestration/dispatch.py, store.py and coordinator.py; create tests/unit/orchestration/test_agent_task_requests.py.

**Interfaces:** `AgentTaskRequests(store).submit(instance_id, credential, request: AgentTaskRequest) -> AgentTaskRequestReceipt`; `get(instance_id, credential, request_id) -> AgentTaskRequestReceipt`; `Dispatcher.select(scope, *, key, agent_request_id=None) -> ClaimResult`; Store.dispatch_bound receives the same optional server-only ID. Existing default request hashes remain unchanged when it is absent.

- [x] Write tests for duplicate/concurrent enqueue, changed payload, stale revision, narrowed capabilities, unavailable hints, foreign preferred task, queue pause and30s no-work backoff. Expected RED is absent durable request/admission, never environment setup.
- [x] Insert at most one pending request per active context, using an actor derived from its server ID. Validate credentials and exact instance before mutation; no source inspection or inference while holding SQL.
- [x] Have coordinator fingerprint/consume queued requests before normal selection. Reuse source/pipeline preflight; revalidate context, instance, profile and optional preferred revision inside bound dispatch. Narrow the candidate set to that exact instance and advertised capabilities. Keep baseline/aging safety ranking; preference only breaks equal baseline/age ties and never affects mandatory oldest-first dispatch.
- [x] Save assigned or no_eligible_task outcome with assignment/reservation/outbox in the same transaction. Replay returns the same request and attempt. A different fresh key during pending/backoff cannot create a recursive queue.
- [x] Prove actual worker launch from the request through the common coordinator pool, without manual claims or a second execution lane. Assert immutable deadline, shared spend/reserve and original source preservation.

```python
first = AgentTaskRequests(store).submit(instance.id, issued.credential, request)
assert AgentTaskRequests(store).submit(instance.id, issued.credential, request).id == first.id
# After automatic coordinator admission:
receipt = AgentTaskRequests(store).get(instance.id, issued.credential, first.id)
assert receipt.result.attempt.execution_binding_id == approved_binding.id
assert len(store.attempts(runtime, workflow.id)) == 1
```

### Task 3: Protected transport and public HTTP entry

**Files:** modify orchestration/owner_requests.py, owner_commands.py, web/owner.py; create tests/unit/orchestration/test_agent_request_http.py and test_agent_request_native.py.

**Interfaces:** owner command variants issue_agent_context, request_task and task_request_status delegate to the new service. POST /api/agents/{id}/task-request-context issues only through the existing selected-project owner adapter. POST /api/agents/{id}/task-requests and GET /api/agents/{id}/task-requests/{request_id} require the issued credential in an explicit request header with existing loopback/origin checks; that credential must not grant owner-route access. The URL ID is only checked against the credential.

- [x] Write real protected HTTP negatives: missing/wrong owner token, hostile Origin, owner token without credential, swapped instance, foreign selected folder, malformed/oversized hints and changed replay payload.
- [x] Extend closed command/result unions without eager variant-field access in shared UI. External agents do not receive the general owner token. Raw credentials never enter durable command receipts, events, URLs or logs; request commands do not grant a web owner actor implicitly.
- [x] Route all processing to the elected Host; return202 for queued and200 for a current stored terminal receipt, explicit409/403 for stale/invalid authority. Keep Stop/status responsive during model requests and backoff.
- [x] Repeat actual lost-reply/Host-restart tests and legacy owner browser success controls.

### Task 4: Exact package, parallel compatibility and publication

**Files:** README.md, README.en.md, docs/Plan.md, docs/handoff.md, delivery plan and docs/validation/2026-10-19-agent-task-requests.md/json.

- [x] Freeze source/tests and run directly related queue/dispatcher/Host/owner/API tests; scoped Ruff/mypy and browser checks for shared owner unions.
- [x] Build exact wheel, assert every source/static byte and UTF8 README metadata, import the extracted package and run the real request→common coordinator→worker route.
- [x] Merge into a frozen copy of current parallel-controller changes; run request identity/admission/registered-command compatibility before touching main. Do not publish its unfinished brain APIs.
- [x] Archive regular-file evidence, genuine RED/failure records and limits; publish checked source/docs only with exact tree/wheel parity and verify remote commit. Keep AC-14 avalanche/scientific/full-goal gates open until separately proven.

Checked delivery: exact189-file extracted wheel83 scoped checks84.41s; frozen241-file controller94 checks105.97s. Both old browser command/Stop/keyboard/mobile checks pass; scoped Ruff/mypy12 and package/UTF8 README/CLI parity pass. Evidence retained. AC-14, scientific and full-goal gates remain open. See ../../validation/2026-10-19-agent-task-requests.md.
