# Managed shared database coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Native execution and publication are already authorized by the aggregate goal; unavailable execution skills do not block that authorization.

**Goal:** Run one managed coordinator per physical canonical SQLite database, independent of individual web clients, without recovering a live owner or resetting accounting.

**Architecture:** Schema18 binds an immutable database/root/lock registration to an external by-path pin. A non-inherited kernel lock grants a session generation before managed recovery or dispatch; timestamps/PIDs never grant takeover. A separate process services control and executes blocking pipeline stages on independent Store connections, reusing Runtime/Reviews/Integration and durable model admissions.

**Tech Stack:** Existing Python/Pydantic, SQLite, POSIX custody locks, subprocesses, threading and pytest.

**Spec:** `docs/Plan.md` §§101/103/104; `docs/specs/connectome-orchestration.md`; aggregate delivery Tasks5/6/8 in `2026-10-16-specification-delivery.md`.

## Global Constraints

- One active workflow, max4instances/64tasks/3attempts/60model calls/120tools/30min, verification reserve20%.
- No SQLite transaction spans filesystem inspection, HTTP, subprocess execution or inference.
- Heartbeat10s/lease30s are diagnostics/attempt scheduling, never proof that a coordinator died.
- Preserve unknown model/tool outcomes, spending, reservations, user dirty/untracked bytes and historical events.
- New execution requires current physical project registration; recovery/Stop may use saved historical project IDs.
- No automatic model probe/load/fallback or synthetic biological topology.
- Trusted local POSIX processes only; missing/substituted ownership state refuses execution.
- This plan does not close neural orchestration admission or scientific §§90–99.

## Review Focus

1. Two first-start processes and pathname aliases must select one exact lock/session, with no losing recovery or claim.
2. A missing/replaced lock, database, host root or ancestor must not create new authority over old records.
3. Active old manual attempts without a managed owner must block adoption before fences/reservations change.
4. A host crash must preserve uncertain HTTP/pytest effects and reconcile exact saved custody before reassignment.
5. Owner Stop must remain responsive while review/integration block, and closing a UI must not close the host.

### Task1: Physical leadership and managed admission

**Files:** create `src/flycode/orchestration/host.py`, `tests/unit/orchestration/test_host_leadership.py`; modify `src/flycode/orchestration/store.py`, `dispatch.py`, `runtime.py`, `resources.py`, `tools.py`, `review.py`, `integration.py`; update existing schema migration assertions.

**Interfaces:** `Host(database: Path, state_root: Path | None = None)` is an owning context manager. `Host.connect(catalogue=None, clock=time.time) -> Store` creates a same-thread connection with in-process leadership authority. `Host.recover()` uses a trusted historical runtime scope and transitions this session to ready only after fencing once. `Host.check()` validates the held descriptor and physical registration outside SQL; `Host.validate_sql(store)` validates current session generation inside SQL. `Host.close()` releases its own descriptor without fabricating worker/effect settlement. Registration/session records are internal Pydantic models in `host.py`, not browser input.

- [x] Write native subprocess RED: owner prints its actual session, then holds stdin; contender through an alias must report busy, and bare `Store.recover` must refuse managed records. An artificially old diagnostic heartbeat cannot grant takeover.

```python
with owned_host(database, state_root) as process:
    before = queue_snapshot(database)
    rejected = subprocess.run([sys.executable, '-c', contender, str(alias), str(state_root)], capture_output=True, text=True)
    assert json.loads(rejected.stdout)['error'] == 'host_busy'
    assert queue_snapshot(database) == before
```

- [x] Retain first-adoption RED over an actual manually claimed attempt. Host construction raises `host_adoption_busy`; compare task/attempt/instance/event/reservation/model/tool rows before and after. A missing worker execution record is not proof of quiescence.
- [x] Implement schema17→18 `host_registry(id=1,data)` and `host_sessions(id=1,data)` in one migration transaction, preserving rollback behavior and all existing rows.
- [x] Bootstrap under one exact lock. Persist the external pin before registration; require initialized row/pin/session to agree. Refuse incomplete one-sided initialization for trusted repair instead of guessing that preparation was effect-free. Refuse hard-linked DB before SQLite/WAL open, copied/replaced DB, missing/replaced lock, changed root/ancestors and a missing/foreign initialized session. Preserve damaged state.
- [x] Guard managed claim/dispatch/start/recover/expire and actual worker/review/integration/model/tool admission. Recheck the session inside each admission transaction; filesystem checks occur before it. Existing unregistered trusted low-level use remains compatible. Exact late transport acknowledgments remain able to preserve actual usage after fencing; never convert them into fresh admission.
- [x] Kill only the exact owned test Popen. A second host acquires the original lock, advances generation, fences old attempts once and retains uncertain reservation/model/tool records. Repeated recover does not advance task fences again. Test stale authority cannot claim/recover after its own context closes.
- [x] Run targeted native, migration, dispatcher/runtime/resource/tool/review/integration tests; retain REDs and corrections. This verifies the foundation only; coordinator completion and publication require Tasks2–3 below.

### Task2: Independent background lifecycle and responsive control

**Files:** create `src/flycode/orchestration/coordinator.py`, `tests/unit/orchestration/test_managed_coordinator.py`; modify `dispatch.py` to expose separate claim and launch phases if required, `web/tasks.py`, `web/app.py`, `cli.py` only for explicit server-owned lifecycle/binding routes.

**Interfaces:** `Coordinator(host).run(should_stop)` owns the control loop. Each stage thread opens its own `host.connect`; `Dispatcher` remains the only binding/claim/launch adapter. Host launch/attach selects the persisted physical registration, not workspace/data_dir. Owner commands continue through the existing canonical Store and protected API.

- [ ] Native RED: two application processes attach to one host, close one client, preserve host session/worker heartbeat; the other client can pause/cancel under original spending/deadline.
- [x] Split blocking stage execution from the control loop while retaining exact attempt/binding/generation ownership. Track exact owned stage threads/WorkerHandles; control polls committed commands, heartbeats every10s, and expires attempts on existing30s leases. Do not add a second mutable task queue.
- [x] Build fresh dispatch scope from current physical project registrations outside SQL. Build historical recovery/Stop scope from saved canonical records without granting new source execution. Derive recovery roots from exact saved bindings/executions, never a UI directory or arbitrary path parent.
- [x] Reuse Runtime.reconcile, Reviews.reconcile, Integration.reconcile and Sources.settle with exact saved refs. Unknown parent HTTP/pytest remains held; failed rollback/root loss stays reconciling. No PID-only signals or automatic failed/completed rewrites.
- [x] Wake dispatch only on meaningful canonical input changes; reuse an idempotency key for unchanged input. Own event writes/empty polling must not generate unbounded commands/events.
- [ ] Actual HTTP/native RED: hold a review/integration request, issue protected Stop from the other client, observe response and stopped process receipts before another writer. Preserve late usage and no duplicate inference.
- [x] Run an actual English writer→review→integration→current source verification through the managed host, without manual claims or legacy run commands; include restart/Stop and preserved dirty/untracked bytes. Native subprocess and direct canonical owner commands are checked; protected HTTP Stop/two app attachment remain separate open checks above.

### Task3: Verified publication

**Files:** `docs/Plan.md`, `docs/handoff.md`, aggregate delivery plan, README RU/EN and a new validation report/JSON.

- [x] Freeze production/static/test/config bytes and exact default collection; run full pytest with isolated artifact roots and Compose module affinity, then serial web group. Preserve expected skips as skips and independently compare exact IDs/hashes. Core increment:1130 IDs,1122pass/8skip,250unchanged; attachment remains Task2 work.
- [x] Relevant Ruff/mypy, real FlyWire and explicit already-loaded model smoke, wheel/extracted CLI and focused native host cases. Inspect original logs/evidence, not only generated booleans. Core increment: wheel108/CLI10, graph5 and actual Qwen pipeline3model/34tools pass.
- [ ] Independently review immutable implementation/evidence, retain every failure, seal only closed regular files, update honest remaining Tasks5–8/research scope and publish checked commit to public FlexGodX/FlyCode.

The durable-catalogue dependency was published as6c6d33e; its final source evidence is not reused as a full run of coordinator changes. The wider goal remains active until every aggregate acceptance requirement is met.

## Working evidence, not publication acceptance

The foundation remains uncommitted. Native/HTTP host cases passed36 checks in27.16s
before adding the schema18 rollback, simultaneous first-start and damaged-session
cases. The latter retained2 genuine failures (missing/foreign session silently
recreated) and2 passes before correction. The broad orchestration group passed560
checks in1275.56s before the final metadata correction; exact IDs and53 hashes
from a mid-run capture were independently checked. This is not a pre-start freeze
or post-correction full-suite run. Ruff over orchestration and its tests passes;
scoped mypy passes12 changed production files.

Additional retained actual REDs cover final pytest GO admission, HTTP accounting
during concurrent host close, artifact acceptance at entry and after preparation.
An initial correction run retained33 passes/2 failed test expectations: all model
reservations had already been spent, and known never-started pytest was correctly
failed rather than uncertain. Expectations now compare the actual pre-publication
remaining reservation and known stopped receipt. These are not hidden reruns or
claims that the original failures passed.

Evidence: `/private/tmp/flycode-host-final-boundaries-red.xml`,
`/private/tmp/flycode-host-prepared-artifact-red.xml`,
`/private/tmp/flycode-host-initialized-session-red.xml`,
`/private/tmp/flycode-host-leadership-green8.xml`. Raw regular-file copies and exact
pre-correction source are retained alongside these logs. Task2 remains open in full.

The final metadata correction is composed: malformed registry/session JSON becomes
a host StoreError so an exact already-spent HTTP acknowledgement persists as late.
Actual isolated RED4/green44 (31.89s) and resource/tool62 (33.50s) are retained.
Main final106 passed68.67s; exact IDs and53 final source/test hashes match pinned
parent/isolated manifests. `/private/tmp/flycode-host-foundation-status.json`
records the final scope. No default full-suite, current wheel/graph/live-model/
browser acceptance or publication is inferred. Task2 remains open in full.
[Working validation report](../../validation/2026-10-17-managed-host-foundation.md).

The internal core is now implemented and focused108 exact checks pass180.19s;
extracted-wheel108/CLI10,158 exact package files/README, Ruff/mypy13 and real
FlyWire5 pass. Actual English Qwen pipeline:74.002s,3model/34tools, current verified
integration, preserved owner bytes, no manual claims or post-approval owner
commands. First probe timeout/673token failed outcome and explicit retry are
retained. Initial full1130 finished1121pass/1old injection-seam failure/8skip;
unchanged assertions with public `launch` pass the full13-case project module.
Final full1130/250 frozen files completed1122pass/8expectedskip,0failures/errors
in1008.627s. Task2's checked core
items above supersede the historical foundation-only scope; application attachment
and protected HTTP Stop remain open. [Core report](../../validation/2026-10-17-managed-coordinator.md).
