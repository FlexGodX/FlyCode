# Local Projects Implementation Plan

**Goal:** Create projects backed by one local folder without Git and group
multiple independent chats under each project.

**Architecture:** Store explicitly registered project roots in the UI data
directory. Resolve runs through stable project IDs while preserving legacy
workspace selectors. Reuse run history as independent project chats.

**Tech Stack:** Python, Pydantic, FastAPI, plain JavaScript and CSS.

**Spec:** User request: one project, one local folder, several chats as in Codex.
Related existing behavior: `docs/Plan.md` §75; browser workspace implementation.

## Global Constraints

- No Git initialization or repository requirement.
- Existing user changes and run records must be preserved.
- External folders require explicit registration; raw run paths remain scoped.
- One active agent remains the current runtime limit.

## Review Focus

- Registered folders and chats survive a server restart.
- Missing folders fail clearly; symlink retargeting cannot change a saved root.
- Duplicate paths do not create competing project identities.
- Existing-folder registration preserves contents; new-folder creation refuses
  an existing target.
- Project switching and new chats retain the correct folder and filter history.

### Task 1: Persistent local projects

Files: `src/flycode/web/projects.py`, `app.py`, `runs.py`,
`tests/unit/web/test_projects.py`.

- [x] Add HTTP tests for registration, creation, restart, duplicates, invalid
  paths, token/origin protection and execution against an external non-Git folder.
- [x] Run `.venv/bin/pytest tests/unit/web/test_projects.py -q`; observe failures.
- [x] Add Pydantic project records and requests, atomic JSON persistence and
  explicit project-ID resolution; reuse existing workspace path validation.
- [x] Run project and existing web tests.

### Task 2: Project chats in the browser

Files: `src/flycode/web/static/{index.html,app.js,styles.css}`,
`tests/browser/projects.cjs`.

- [x] Add browser acceptance for folder registration, new-folder creation,
  project selection, two independent chats and history filtering after reload.
- [x] Run against the unchanged UI and observe the missing controls.
- [x] Add a project dialog, sidebar project list and selection persistence;
  filter existing run history by the selected project.
- [x] Run browser acceptance at desktop and mobile sizes; inspect screenshots.
- [x] Run full pytest, changed-file lint and type checks; report existing failures.

## Verification

- Project HTTP tests: 10 passed. Full web tests: 35 passed.
- Project browser acceptance: passed at 1440, 390 and 320 px, including
  delayed startup, reload, independent chats and project history filtering.
- Existing agent browser acceptance: passed with explicit offline demo selection
  after reload, preserving the current next-run model preference behavior.
- Changed-file Ruff, mypy and JavaScript syntax checks passed.
- Full pytest exposed two unrelated Phase 4 Docker/PostgreSQL acceptance failures:
  `test_agent_repairs_and_runs_fastapi_postgres[bias_control-functional]` and
  `[hard_control-functional]`, both exhausting 60 steps without verified completion.
  The first full run also retained an old test assertion loaded before its edit;
  the corrected project test and complete web suite pass on rerun.
- User restricted further verification to interface tests. The additional broad
  run was interrupted; no successful full-suite result is claimed.

## Follow-up: chats without a project

User requirement: chats may exist independently of any project or local folder.
Explicit `project=null` uses a separate empty directory for each run; omitted
project preserves the existing API demo default. Browser selection uses an empty
option, persists it, and filters null-project history separately from projects.

- [x] Observe HTTP rejection of `project=null` before implementation.
- [x] Observe the missing "Без проекта" browser action before implementation.
- [x] Add nullable UI request support and isolated, non-demo chat directories.
- [x] Add sidebar and composer choices, independent history and reload support.
- [x] Verify real browser creation of two chats, distinct directories, history
  filtering, reload, reopening and 320px mobile layout with an offline model
  catalogue fixture. Verify new and legacy UI API behavior: 3 tests passed.
- [x] Verify project browser acceptance, changed-file lint and type checks.

Only interface API and browser tests were run for this follow-up.

## Follow-up: native folder selection

- [x] Add a "Выбрать папку…" action alongside manual path entry.
- [x] Run native Tk directory/save dialogs in a separate process so their event
  loop runs on the main thread. Selection is returned through the protected UI
  API without registering or creating a project until the user saves the form.
- [x] Handle cancellation without clearing the current path; reject concurrent
  dialogs and preserve manual entry when native dialogs are unavailable.
- [x] Observe failing UI API/browser tests before implementation, then verify
  existing/new-folder selection, cancellation and session protection: 5 API tests
  passed and project browser acceptance passed at desktop and mobile sizes.
- [x] Run changed-file Ruff, mypy and JavaScript syntax checks.

The real macOS helper reached `Tk_ChooseDirectoryObjCmd` and AppKit's native
`NSSavePanel runModal` during a live API request. Computer Use could not attach
to the unbundled Python app, so native selection/cancellation was not automated;
the test helper was terminated after confirming native dialog execution.
Browser tests substitute only the OS chooser result; project saving stays real.
