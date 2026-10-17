# Durable model catalogue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Native execution is already authorized for the aggregate delivery; unavailable execution skills do not block that authorization.

**Goal:** Preserve explicitly checked model capabilities across application and future coordinator restarts, without implicit inference or trusting profile snapshots.

**Architecture:** An optional database-backed ModelCatalogue uses the canonical SQLite store. A Pydantic admission binds each granted role to the exact completed host probe call. Optimistic compare-and-swap rejects a stale process instead of overwriting a newer grant or revocation; no transaction spans discovery, runtime metadata HTTP or inference.

**Tech Stack:** Existing Python, Pydantic v2, SQLite, HTTPX/OpenAI clients, FastAPI and pytest.

**Spec:** `docs/Plan.md` §102; `docs/specs/model-selection.md`; aggregate delivery Tasks2/5/8 in `2026-10-16-specification-delivery.md`.

## Global Constraints

- Discovery is not compatibility. Only explicit bounded primary/scorer probes confer roles.
- No automatic model loading, probing, inference or fallback during restore.
- One inference per endpoint; SQLite transactions never span model HTTP.
- Keep the existing in-memory catalogue available for trusted low-level callers.
- This increment is a dependency of the shared coordinator, not completed host leadership or Tasks5–8.

## Review Focus

1. Application restart must retain a real successful probe without sending another generation request.
2. Discovery, malformed protocol output and an uncertain/late/foreign probe receipt cannot confer a role.
3. A second process must not overwrite a newer admission or revocation from a stale snapshot.
4. Endpoint, loaded instance, context and load fingerprint changes must revoke stale compatibility.
5. Receipt or admission corruption must refuse eligibility; no silent reconstruction from profiles.

### Task1: Durable explicit admissions

**Files:** create `src/flycode/orchestration/model_catalogue.py`, `tests/unit/orchestration/test_durable_model_catalogue.py`, `tests/unit/orchestration/test_durable_probe_envelopes.py` and `tests/unit/web/test_model_catalogue_persistence.py`; modify `src/flycode/llm/catalogue.py`, `src/flycode/orchestration/transport.py`, `src/flycode/orchestration/store.py`, `src/flycode/web/app.py`. Update existing schema-version migration assertions to the new version, retaining rollback tests. Full validation additionally requires the demonstrated legacy publication correction in `web/runs.py`/`tests/unit/web/test_app.py` and absolute child import path in the existing custody test.

**Interfaces:** `ModelCatalogue(base_url=None, *, database: Path | None = None)` preserves memory-only defaults. `HostTransport.last_call_id` identifies only the current actual admitted request. `ModelAdmissions.load(endpoint_ref)` validates descriptor/role receipts; `save(entries, previous, proof=None)` compares saved bytes transactionally and rejects conflicts. Admission JSON is internal and is never accepted from browser input.

- [x] Write actual HTTP restart RED:

```python
with TestClient(create_app(workspace, history1), base_url='http://localhost') as first:
    ref = first.get('/api/models').json()['catalogue'][0]['model_ref']
    first.headers['X-FlyCode-Token'] = first.get('/api/bootstrap').json()['token']
    assert first.post('/api/models/probe', json={'model_ref': ref, 'role': 'primary'}).json()['roles'] == ['primary']
with TestClient(create_app(workspace, history2), base_url='http://localhost') as second:
    assert next(e for e in second.get('/api/models').json()['catalogue'] if e['model_ref'] == ref)['roles'] == ['primary']
assert len(model_server.requests) == 1
```

- [x] Run `.venv/bin/pytest tests/unit/web/test_model_catalogue_persistence.py -q`; retain assertion failure, not a denied socket setup.
- [x] Migrate schema16→17 with a `model_admissions(model_ref PRIMARY KEY, endpoint_ref, data)` table. Store descriptor plus exact per-role call IDs. Validate host-only probe ownership, endpoint, loaded model, role, completed/not-late SQL and JSON states before granting or restoring.
- [x] Add optional catalogue persistence, explicit database transport routing and current-call receipt. Restore only on explicit catalogue operations; refresh loaded metadata before actual execution through the existing runtime path. Web opts into the configured canonical database.
- [x] Exercise restart/discovery-only, both roles, malformed re-probe, changed runtime/endpoint, two independent catalogues with stale save, invalid receipt and failed migration rollback. During the real inference callback, successfully acquire a separate SQLite write transaction to prove no transaction spans HTTP.
- [x] Run focused catalogue/transport/profile/migration/web tests, Ruff and scoped mypy; run the default collection before calling this increment complete. Preserve failures and source identities. Check extracted-wheel import/CLI and saved catalogue restore.
- [x] Preserve malformed-envelope REDs (five failures/one already-safe empty-content case) and the busy-endpoint observed-revocation RED. Persist revocation for the actual malformed protocol exceptions; save newly observed metadata before returning busy, without another model request.
- [x] Preserve the first full run:1058 pass/2 fail/8 skip,1068 exact cases, unchanged source30d529. The custody failure is a missing PYTHONPATH fixture dependency before launch, not a production FD failure. Derive the child path from the imported package without weakening assertions.
- [x] Retain actual gated SourceExecution.finish RED: API incorrectly exposes completed before cleanup and record persistence. Keep computed result/status local, then save a detached final record under the manager lock before publishing it. Verify active status and second-run409 while cleanup is held, then restart with the complete persisted result.
- [x] Update Plan/handoff/README RU/EN with the verified boundaries. Keep aggregate Tasks5–8 open.
- [ ] Commit and publish checked bytes after the final independent evidence audit.

### Task2: Shared coordinator continuation

After this dependency, implement the separate per-database coordinator described in `/private/tmp/flycode-host-leadership-notes-524075e.md`: exact kernel leadership before recovery, durable physical DB/root/lock pins, safe first adoption, nonblocking control while review/integration execute, and saved recovery authority after source disappearance. Its native process REDs precede that implementation. This plan does not substitute catalogue persistence for those requirements.
