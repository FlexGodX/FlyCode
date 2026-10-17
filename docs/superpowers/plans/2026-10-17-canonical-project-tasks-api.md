# Canonical projects and Tasks API delivery plan

**Goal:** expose protected canonical drafts, owner commands, comments and durable
events using verified physical project IDs, preserving the existing chat UI.

**Architecture:** schema16 adds immutable physical-folder registrations to the
same Store. The HTTP adapter derives each request's owner scope from server-known
folder selectors; it never accepts actor/role authority from JSON. TrackerService
remains a projection and command adapter, not a new mutable queue. This slice
does not start a background dispatcher or call recovery from web apps.

**Stack:** existing Python/Pydantic/SQLite/FastAPI, local HTTP and pytest.
**Spec:** Plan §§102–104; task-tracker TRK02/09 and Task8 of the aggregate plan.
The authorized native execution method and existing engineering/research
boundaries remain in effect.

## Constraints and review focus

- Preserve legacy selectors/history, non-Git folders, projectless chats and model
  defaults. No implicit historical project-ID import or inference from drafts.
- One workflow,4 instances,64 tasks,3 attempts,60 models,120 tools,30 minutes and
 20% verification reserve; no filesystem, subprocess or inference inside SQL.
- Verify physical folder/ancestor identity before issuing scope. Root replacement
  must revoke that request's authority even when bytes are identical.
- Quiescent internal project revoke cannot release live custody or spending;
  tombstones never silently rebind to replacement folders.
- Preserve every missed event on reconnect; scan frontier is distinct from
  delivered cursor. Scope expansion cannot acknowledge undelivered comments.
- Token/local-origin gates precede mutations and new private reads. Comments are
  literal bounded data; task states cannot be forced to succeeded.
- Unique DB host leadership and fresh physical authority before runtime effects
  remain required before adding background execution to this adapter.

## 1. Registry foundation

Files: `domain/project.py`, `orchestration/projects.py`, `orchestration/store.py`,
`tests/unit/orchestration/test_canonical_project_registry.py`; latest-schema
expectations in dispatcher/review/pipeline tests.

- [x] Isolated RED/GREEN: canonical UUIDs, aliases/two connections, replaced
  root/ancestor, migration rollback, quiescent revoke/claim race and old IDs.
- [x] Reviewed immutable schema16 patch:72 focused tests, Ruff7/mypy52 passed.
  Existing downgrade/re-upgrade fixtures exposed three real migration failures;
  additive IF NOT EXISTS migration preserves their original history assertions.
- [x] Integrate checked patch only after the source15 publication is sealed;
  verify baseline and final file hashes before composing with HTTP changes.

## 2. Canonical HTTP adapter

Files: `web/{app,projects,tasks}.py`, `tracker/{service,commands,projection}.py`,
`tests/unit/web/test_canonical_tasks_api.py`.

- [x] Missing routes/authority RED; explicit known-folder registration, root/child
  drafts, owner commands, comments, scoped keyset list/history and actual agents.
- [x] Actual local HTTP REDs preserve stale-revision serialization,64-bit cursor
  overflow, missing durable history, reconnect with two missed comments and scope
  expansion after a foreign global frontier.
- [x] GREEN19 combined cases: snapshot state frontier and replay_after have
  distinct meaning; separate scan/delivery cursors preserve every eligible
  missed event and subsequent live event. No duplicate command effects.
- [x] Existing web/registry/tracker compatibility92 pass109.75s in isolated copy.
- [x] Main composition:98 registry/API/identity compatibility cases pass112.41s;
  keep all outcomes and review protected reads, connection thread ownership,
  native local server shutdown and unchanged legacy options.

## 3. Implementation identity

Files: `research/identity.py`, `tests/unit/research/test_identity.py`.

- [x] Actual RED: editing a web policy did not change the experiment fingerprint.
- [x] Include Python web execution/API modules in the existing fingerprint;
 25 combined identity/API/tracker cases pass, Ruff and scoped mypy9 pass.
  Prior source15 measurements separately pinned all227 measurement files,
  including web bytes; their retained evidence is unchanged.
- [x] Verify new HTTP code changes now invalidate prior identity-bound admission.
  Do not relabel old checkpoints or source15 evidence as current admission.

## 4. Frozen delivery verification

- [x] Finish source, tests and RU/EN README before freezing. Register1017 complete
  test IDs,282 extracted-wheel cases and bounded English model protocol. Retain
  launcher setup rejection before any test; no product assertion changed.
- [x] Full default tests (1017 IDs:1009 pass/8 skip;651.131s), Ruff/mypy65,
  extracted wheel bytes/README/CLI, enabled real FlyWire, bounded explicit primary
  probe and actual English pipeline/Stop on one unchanged implementation.
- [x] Independently audit scopes, actual durable outcomes, source preservation,
  budgets and stop receipts. Retain every RED/prior full outcome outside Git.
- [x] Update Plan/handoff/report and publish checked commit to FlexGodX/FlyCode;
  keep Tasks5–8 open. UI board/CLI commands, verification endpoints, runtime host,
  neural encoder/shadow/replay and scientific admission remain separate work.
