# Message endpoint pins implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Execution here uses the existing native session authorized by the owner.

**Goal:** Prevent a model-selected message from being delivered to a replacement attempt behind a reused instance ID.

**Architecture:** Capture host-owned endpoint pins before each model request. Recheck them after HTTP and compare the selected action's ID, tool and arguments before execution. Validate the exact recipient pins again inside the broker's existing writer transaction; retain existing message hashes, TTL, audit, budgets and replay behavior.

**Tech Stack:** Python >=3.10, Pydantic v2, SQLite, pytest, actual local HTTP and owned subprocess workers.

**Spec:** `docs/Plan.md` §§100/104; `docs/specs/connectome-orchestration.md` ORC-06 and AC-04/05/09.

## Global Constraints

- One active workflow; max 4 instances, 64 tasks, 3 attempts, 60 model calls, 120 tools and 1800 seconds; reserve 20% for verification.
- No SQLite transaction during inference. Messages remain data, not permission or task transitions.
- At most 200 deliveries, 16 KiB per envelope, 4 hops, TTL 300 seconds and 3 explicit broadcast recipients.
- Preserve user dirty/untracked bytes and the parallel brain implementation. Only feature-related tests in frozen copies.
- Do not change retained AgentMessage, WorkerBinding, ReviewBinding or pipeline record schemas/hashes.

## Review Focus

- Reused instance with a different attempt: reject the old candidate even if the new target has identical scope.
- Paused/cancelled/expired recipient without incoming messages: invalidate held HTTP candidates and retain the paid call.
- Heartbeat and title changes: keep the same semantic endpoint valid.
- Changed candidate arguments or a forged ID: refuse before tool debit or journal publication.
- Recipient changes between tool admission and send: debit the admitted tool once, fail it, and publish no delivery.

---

### Task 1: Pin model-selected messages to exact endpoints

**Files:** modify `src/flycode/orchestration/messages.py`, `src/flycode/orchestration/cortex.py`, `src/flycode/orchestration/runtime.py`; extend `tests/unit/orchestration/test_agent_messages.py`.

**Interfaces:**
- `MessageEndpoint(QueueModel)` contains instance_id, attempt_id, task_id, fence, semantic_revision and contract_hash.
- `Messages.capture(scope, attempt_id, fence, identities) -> tuple[MessageEndpoint, ...]` exposes only current assigned/running endpoints permitted by existing scope checks.
- `Messages.validate_endpoints(scope, attempt_id, fence, pins) -> None` raises `StoreError('message_route_stale')` if any captured endpoint no longer owns the same task contract.
- `Messages.execute(..., routes, recipient_pins=None) -> ToolResult` and `Messages.send(..., *, recipient_pins=None)` retain trusted SDK behavior while the worker path supplies host pins.
- `AttemptCortex.execute_message(root, action) -> ToolResult` checks the host-generated ID plus tool/argument digest, then delegates through the normal tool budget and broker.
- `AttemptCortex.bind_message_candidates(candidates, *, recipient_pins) -> None` replaces the current host-parsed candidate set and validates its exact endpoints. The parallel registered-command path uses the same admission.

- [x] Write held real-HTTP tests with no incoming messages; replace/pause/cancel/narrow the recipient while HTTP is held. Assert `candidates == []`, routing error `message_route_stale`, one spent model call, no messages/tools, and live sender. Use real Store commands/settlement/claim for replacement.
- [x] Run these tests against the published baseline and retain the failing output.
- [x] Add the immutable endpoint model and capture/validation methods. Capture only the displayed live endpoints before HTTP; recheck after actual HTTP even when no message excerpts were included.
- [x] Assign fresh candidate IDs and retain only the current generation's selected message digests/pins. Refuse forged/modified actions before tool admission. Pass exact pins through Messages.execute and revalidate in Messages.send's writer transaction before inserts.
- [x] Pin raw worker progress to the initial ready-time endpoints; do not resolve the same instance ID into a later attempt. Keep blocked progress nonfatal.
- [x] Verify stable question/answer HTTP+RPC, no double debit, replay, TTL, metadata/heartbeat stability, endpoint replacement, argument mutation and atomic send race with scoped tests.
- [x] Run relevant Ruff/mypy, wheel/extracted-package tests and compatibility checks against an immutable merge of current brain changes; retain failures separately.
- [ ] Publish the checked source, tests, Plan/handoff and README RU/EN after final archive and exact-tree inspection. Publication receipt records the resulting commit separately.

### Boundary of this deliverable

This is the prerequisite for automatic owner-approved task routes. It does not add owner route grants, a dynamic sibling catalogue, new task creation from task_request, UI controls, neural admission or scientific acceptance. Those remain open in §104. Static trusted SDK callers keep the existing audit behavior for late messages; worker-selected messages gain exact endpoint admission.

### Verification assertions

```python
assert candidates == [] and error == 'message_route_stale'
assert store.budget(owner, workflow.id).spent.model_calls == 1
assert not Messages(store).journal(owner, workflow.id)
assert not Tools(store).calls(owner, workflow.id)
assert store._attempt(first.id).state == 'running'
```

```python
result = cortex.execute_message(source, action.model_copy(update={'arguments': changed_arguments}))
assert not result.success and result.error_message == 'message_candidate'
assert store.budget(owner, workflow.id).spent.tool_calls == 0
```

Run the relevant file with the workspace `.venv/bin/python -m pytest tests/unit/orchestration/test_agent_messages.py -q`; do not run a full suite after this feature.

Compatibility integration (excluded from the public 184-file package): preserve the full parallel neural setup and lazy cortex; pass Host-resolved message endpoints into registered-command context, hide send_message without live routes, bind response.id:index candidates after HTTP, and retain spent receipts on stale-route rejection. The frozen 225-file merge passed 213 tests, including eight command/message cases, without claiming biological acceptance.
