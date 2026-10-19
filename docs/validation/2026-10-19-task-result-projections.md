# Shared task result projections — 2026-10-19

The checked increment exposes task completion separately from workflow integration
through TrackerService, existing CLI/HTTP and Tasks board/list/detail/agents.
It adds presentation DTOs only: no saved domain shape or schema migration.
Parent wait, child proof, exact input hashes, retry reset/limit, graph revision,
review/evidence IDs, last action and current blockers come from the same Store.
Metadata/cursor are captured in a short transaction; physical evidence checks
run afterwards through Completions.current and Integration.current. A changed
card/revision cannot retain current=true. Every card rechecks integration; an
older successful card cannot lend currentness to a later evidence read.

Historical continuation checkpoints stay in lineage after resume; they no longer
appear as active waits. Completed/current=false has an explicit stale blocker
and orange summary, including integration-only tasks. Pipeline-owned planned
stages do not offer manual enqueue; ordinary drafts retain their existing flow.
Heartbeat is excluded from last_action, graph events are included, and the new
action summary omits private event data. Existing last_event remains compatible.

## Final verification boundary

Parent `1f6161b11a9b33ba24a5a108e09002e148748986`. The final extracted wheel B is
`/private/tmp/flycode-taskviews-final-wheel-output/flycode-0.1.0-py3-none-any.whl`, SHA-256 `b2835f08b49c16904c9370c8878861039d7647df16a0f5b3a9d98b3617a511cb`.
All176 source/static files match the staged package boundary; all312 frozen
source/test/README inputs are unchanged after execution. The UTF-8 README metadata
and extracted CLI were checked. Final feature collection is exactly13, no skips:
**13 passed219.11s**; existing API/CLI/integration compatibility **28 passed15.14s**.
Scoped Ruff4 and mypy3 (`--follow-imports=silent`) pass. Initial wheel A also passed
13 in236.88s but is retained, superseded by the historical-wait correction.

Final real browser stages: pending, stopped parent wait, verified independent
child, integrated result and invalidated source. Each checks desktop1440/390/320,
keyboard Enter/Escape, no horizontal overflow and literal result text. A real
pause command from the stale screen returns the Continue action within3s, then
HTTP confirms paused=true. Five stage logs and screenshots are under
`/private/tmp/flycode-taskviews-final-wheel-cases/test_public_cli_http_and_optio0`.
Before image lacked a result label; final stale320 shows task/workflow checks
stale and no historical child wait. Stop320 shows confirmed process stop.
A separately labelled controlled HTTP response covers completed/current=false
rendering; it is not reported as the native evidence-loss race.

The old Tasks browser route passes4.38s, including classic/desktop links,
drafts/comments, keyset pagination, stale response handling, keyboard/mobile,
cancel and HTML literal text; attempts/model/tool rows all remain0. The first
round passed3.97s. Browser pageerror checks pass; expected deliberate aborted
requests/coordinator409 and favicon404 remain in retained logs. No claim of
zero console/network errors is made.

Frozen parallel-brain combined source:199 files, initial three native routes
pass44.79s and final wait-fix route passes34.66s. This is interoperability evidence,
not biological performance or acceptance of uncommitted brain changes.

## Real accounting and negative cases

The final public route made3 completed attempt model calls plus1 separate probe,
36 completed tool calls and6 attempts (5 succeeded,1 interrupted parent prep).
Attempt spent is3model/36tool with0reserved/unknown; verification reserves0.
It deliberately edits owner source after successful integration, so persisted
workflow ends incomplete and integration invalidated/result_changed, with the
root subsequently paused. The ledger retains60model/120tool/4instances/64tasks/
3attempts/1800s and20% verification defaults. Other tests deliberately retain
historical completed SQL with lost evidence while the view denies currentness;
the heartbeat fixture is a metadata assignment and may retain reservations.
These fixtures are not relabelled as universally successful or reserve-free.

Actual negative cases cover source/checkpoint loss, later evidence deletion,
foreign scope, event beyond cursor, owner revision during physical inspection,
reviewer artifact lineage, stopped-child gate and saved retry reset. Existing
HTTP compatibility covers token/origin and commands. No new input API was added.

## Retained execution boundaries

Each native boundary retains exact args, input hashes, full output, JUnit and
SQLite/workspace/evidence. Most import root/src; compat and historical-wait import
wheel A, final-compat imports wheel B as recorded in the JSON. Counts overlap.

| Boundary | Exit | Cases | JUnit seconds | Meaning |
|---|---:|---:|---:|---|
| cache-red | 1 | 1 | 29.284 | Real cross-card stale integration cache after deletion of child evidence. |
| combined | 0 | 3 | 44.786 | Frozen parallel-brain source, three native routes; interoperability only. |
| compat | 0 | 28 | 14.525 | Initial extracted wheel A; 28 existing API/CLI/integration cases. |
| consistency-green | 0 | 3 | 11.474 | Three native snapshot/revision/reviewer cases. |
| enqueue-ui-red | 0 | 1 | 61.259 | Passed; observed child was ready, so this attempt did not reproduce planned-child enqueue. Later planned-ui-red supplies real RED. |
| final-combined | 0 | 1 | 34.662 | Final wait correction over the same frozen brain snapshot; one actual native route. |
| final-compat | 0 | 28 | 15.138 | Final extracted wheel B; 28 existing API/CLI/integration cases. |
| first-green | 0 | 2 | 62.353 | Early two-case projection implementation with legacy fixture; replaced by canonical registered Host fixture in final cases. |
| gate-red-corrected | 1 | 1 | 1.024 | Real missing pipeline phase blocker; corrected fixture expectation to actual planned state. |
| gate-red | 1 | 1 | 1.009 | Fixture expected ready while actual canonical child was planned; not feature RED by itself. |
| gates-green | 0 | 2 | 4.880 | Saved retry reset and actual pipeline phase blocker pass. |
| historical-wait-red | 1 | 1 | 28.300 | Initial wheel A retains historical child wait after parent resume and result invalidation; final B corrects it. |
| history-red | 1 | 2 | 2.376 | Real missing graph event in task history plus test helper error: Store has confirm_started, no begin. |
| planned-ui-red | 1 | 1 | 3.550 | Actual planned child improperly offered manual enqueue; reproduced before root preparation. |
| red | 1 | 1 | 1.685 | Initial missing wait projection. |
| retry-red | 1 | 1 | 4.306 | Saved reset not represented before next retry writer; expected retry 1, shown 0. |
| snapshot-green | 1 | 3 | 6.333 | Snapshot fix passes; heartbeat test expected task_created but actual last action workflow_created. Two other checks pass. |
| snapshot-red | 1 | 2 | 6.207 | Later comment escaped page cursor and reviewer lacked actual artifact/evidence. |
| summary-red | 1 | 1 | 1.162 | New last_action summary copied private binding paths; fixed by empty summary data while retaining old last_event. |
| ui-green | 0 | 5 | 139.151 | Intermediate five-case feature run and four real browser states; superseded by final thirteen-case package. |
| ui-red | 1 | 1 | 9.808 | Real browser lacked required result summary. |

The independent read review found the stale cross-card cache and missing stale
blocker/display; both were fixed and checked. Native RED is retained for the
cache and planned enqueue, as well as the final historical-wait correction.
Fixture mistakes are identified in the table rather than counted as feature RED.
Preparation failure is separate: no-build-isolation lacked hatchling; isolated
build dependencies fixed it without altering the shared venv. Full traces are
in flycode-taskviews-build-unavailable.log/json and wheel-build logs. One launcher
preparation used a relative venv path from a private cwd and failed; its log is
retained, no tests ran. Corrected absolute-venv preparation preceded the real run.

## Open acceptance

Safe result artifact drilldown, public owner recipe/replan adapters and combined
Task4 retry/replan/avalanche/restart/Stop acceptance remain open. Task5 aggregate,
controller proposals/admission, neural/research matrix and final full verification
remain open. No full suite, live LM/graph smoke, training or scientific acceptance
is claimed. Parallel brain/source/schema edits remain outside the public package.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-task-results-checked-esf1_5or`; 26714 regular files,
284049320 bytes. Manifest SHA-256 `5f6ab9b76789eeaa9da13e465bdafc6fe616427be98af5a74a8d905df9d8b921`.
Source and destination regular-file hashes plus inventory were verified. Original
custody paths/identities are retained; the copy is not portable execution authority.
Publication identity follows in a separate record after push.
