# Canonical Tasks interface — 2026-10-17

`/tasks` is a separate packaged page linked from both existing chat interfaces.
It uses the protected canonical Store APIs for a board, list, actual instance
projection, project/state/priority/search/archive filters, draft creation,
commands, literal comments and paginated history. Saving a draft explicitly
registers the selected server-known physical folder when necessary. It requires
no Git and starts no model, worker or project tool.

The interface uses FlyCode's existing visual tokens. Classic `/` and `/desktop`,
project groups with independent chats, native folder selection and projectless
chats remain available. Personal model defaults are untouched.

## Verification

The real Chromium scenario passes on composed main and from the extracted wheel.
It creates an English task in a non-Git folder, reloads its actual server ID,
checks blockers and zero execution, posts literal HTML text, observes an external
comment through authenticated SSE and opens more than50 history events. A later
comment preserves the loaded tail. A real stale-revision command is rejected;
only an explicit retry after refreshed state cancels the draft.

A delayed actual HTTP response held across closing/reopening the same task
cannot regress its priority or revision. A controlled valid blocked-task API
projection verifies that dependency-blocked tasks offer no unsupported retry;
that boundary case does not claim an actual blocked worker execution. Deliberate
loss of actual201 responses after draft/comment persistence verifies retries
produce one record each, using the same mutation key until confirmation. An
accepted comment retry clears its prior transport error. Escape closes dialogs
without submitting. Desktop,390px and320px screenshots have no horizontal
page overflow; the320px populated detail was visually inspected.

Existing Desktop acceptance (theme, models/settings, history, demo, Stop and
mobile), project acceptance (non-Git folder groups, multiple chats, picker
request/cancel flow) and two projectless chats also pass on composed main.
The project test controls the native-picker response at its UI boundary; it does
not claim a person operated an actual OS dialog in this run. Projectless model
availability/replies use a real local test HTTP server, not a provider inference.

Independent SQLite audit checks two actual tasks, one physical registration,
zero instances/attempts/workers/model_calls/tool_calls, one persisted literal
comment despite a lost response, original task cancelled at revision3/priority1,
second task planned at revision2/priority3, and unchanged owner bytes. Scope,
token and origin checks stay in the existing server API; the browser sends no
actor or permission authority. Fresh keyset projections follow SSE notifications;
delivery IDs remain strings and never become board-state deltas. Request
sequence guards reject stale detail/history responses.

All162 Python/static/browser/config bytes are frozen for final verification.
The wheel matches145 Python files,10 static assets and README metadata. Node
syntax, changed Python Ruff and diff whitespace checks pass. No non-interface
pytest, graph or real-model benchmark was run for this UI-only increment.
Parent worker176/native15 evidence belongs to its own checked source.

## Retained failures and limits

The first actual RED was absent Tasks navigation. Implementation failures retain
incorrect Fetch ok handling, QueueEvent fields, hidden loaded history, stale
same-task response and Desktop's ambiguous interface-switch selector. Later real
cases retain duplicate draft after lost reply and an uncleared transport error.
The unsupported blocked retry is a controlled frontend contract RED. Test-only
selector/unroute mistakes and the first patch's missing new-file metadata remain
separate from product failures. Sandbox socket preparation and a documentation
stdin encoding error are setup failures. Favicon404, deliberate network aborts
and the forced real409 are expected browser diagnostics; final pageerror lists
are empty. No failure outcome was removed.

This is a useful bounded tracker interface, not completion of Task8. Full
criteria/permission/budget editing, profile/tag/unassigned filters, unique host
leadership, canonical execution hosting, verification start/cancel/rerun/artifacts,
full review/evidence UX and neural admission remain open. Idle agents show the
available instance/profile IDs; no unavailable model/progress/ETA is invented.
There is no drag-only lifecycle change or manual successful-completion control.
Mutation retry keys last for the current page/intent; browser reload discards
unsaved form state. Stop after source removal still needs the separately tracked
durable owner scope before exposing canonical execution.

Main evidence root: `/private/tmp/flycode-tasks-main-validation-4pssh35w`.
Final main browser: `/private/tmp/flycode-tasks-main-accepted-retry`.
