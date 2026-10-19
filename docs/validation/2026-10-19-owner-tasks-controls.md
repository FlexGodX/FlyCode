# Tasks owner controls — checked,2026-10-19

The separate execution-plan dialog uses the published `owner-command-v1` API.
Preview shows canonical tasks/blockers, permissions, budgets and registered checks.
Explicit preparation chooses writers, current binding IDs and controller. Approval
selects independent reviewers, integrator, registered criteria and optional bounded
retry budgets, and requires confirmation. Stopped-parent replan edits child goals/
dependencies or adds explicit writer/reviewer pairs from saved permission/profile
templates. The server still allocates IDs, resolves folders/oracles and checks all
revisions and limits. Existing screens, comments and historical downloads remain.

Pending intent/revisions/key are saved before POST. Lost or partial replies offer
explicit exact replay across reload. Delayed callbacks cannot overwrite a newer
window; settling an older request unlocks a reopened view of the same workflow.
Stop is seeded from the card before preview/catalogue returns. Both Stop buttons
share the existing canonical pending state and update across dialog replacement.
Graph edits are offered at the parent-wait checkpoint, with final server guards.

Two native failures required backend fixes. Catalogue iteration now exhausts its
SQLite cursor before physical profile checks and short SQL validation. The old
cursor could retain a read snapshot across a concurrent owner write; the observed
failure was `database is locked` at `BEGIN IMMEDIATE` (the exact SQLite error code
was not captured). Completion refresh now pins task/workflow metadata before its
physical check and rechecks both before invalidation. Previously a delayed negative
read could revoke a proof after successful integration publication: the saved audit
has `integration_completed`, then proof revocation379ms later, then dependent/result
invalidation. No fingerprint or acceptance check was relaxed. A separately gated
two-connection reproduction remains useful; the native browser route reproduced
the failure before the fix and passes after it, including a real negative edit.

Checked extracted wheel: preview2.54s, commands4.53s,
controlled recovery3.51s, lifecycle103.92s.
The lifecycle includes three existing browser regressions: integrated result cards,
historical material downloads/late scope denial, and stale result/Stop. Seven
browser launches total; these are browser scenarios, not seven pytest cases.
Frozen combined source219 files: commands4.80s,
recovery3.61s, lifecycle109.86s, including
three old regressions (six launches). Counts and scenarios overlap. Add/remove/add keeps unique stable branch captions and dependency labels.

Native English delivery in both final boundaries:7 tasks,8 attempts,4 model calls/
49 tool calls, plus one separate admission probe. Actual OS workers and registered
pytest checks perform parent preparation→stopped checkpoint→changed child goal+
new dependent branch→fresh independent reviews→parent continuation→combined/applied
verification. Original deadline/spending/limits and dirty/untracked bytes remain;
no reservations or uncertainty at current completion. The test then explicitly
changes owner bytes: HTTP/UI revoke currentness, while historical files remain
available. This fixture drives real Dispatcher ticks; it does not establish the
separate automatic coordinator-process/UI/retry/restart combined acceptance.

Keyboard and1440/390/320 screenshots were inspected. Explicit approval is disabled
until confirmation. Stop stays usable during delayed preparation and read loading,
and pending Stop survives closing/reopening the dialog. Controlled partial/scope/
download responses test rendering separately; they are not native partial failure
or security proof. The backend partial/restart checks remain in the prior report.

Ruff3 files, scoped mypy2, JavaScript syntax3, extracted CLI, UTF-8 wheel README and
exact package map184/unchanged inputs328 pass. README RU/EN are included
in the frozen input map. Wheel SHA256 `f7569221c62460d7023dc746bea32ddd290a94466af2a90bbab1cb31589e2d32`.
No full pytest, neural/research matrix or live-LM/graph smoke ran for this UI increment.

All early RED/failure boundaries remain in the archive: missing controls, inaccessible
label, missing/loading Stop, split Stop busy state, stale-dialog unlock, missing new
prerequisite choice, premature graph controls, the native catalogue lock and late
proof revocation. Harness failures include a concurrent preview observation, a
collapsed dependency disclosure, an incorrect expected status string and the first
wheel regression's wrong task identity; its cleanup cancellation is also retained.
The regression fixture correction kept application bytes unchanged. A subsequent
branch-caption RED/fix has its own final checked wheel; a rejected counter-shadowing
preparation was corrected before any measurement.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-owner-tasks-controls-checked-0fz64f11`;22597 regular files/269119156 bytes,
0 special/link entries skipped. Manifest SHA256
`7203e55ac0f433c0d5c5e3b699eb2d9423516f5a46b2155b1be85aabd21ee55e`. [Machine-readable measurements](2026-10-19-owner-tasks-controls.json).

Still open: actual UI existing-child→new-prerequisite execution (the alias control
is exercised; the native route adds a branch depending on an existing child), gated
stale-negative race, automatic coordinator combined acceptance, controller/admission/
neural/research and aggregate full/final gates. Biological performance is unproven.
