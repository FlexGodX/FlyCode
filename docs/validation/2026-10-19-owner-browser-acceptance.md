# Owner browser acceptance — checked,2026-10-19

This follow-up closes two explicitly open checks from the
[Tasks controls report](2026-10-19-owner-tasks-controls.md). Application source is
unchanged from708051a. Only the browser fixture, test-only gate and progress/evidence
documents change. The exact184-file package map still matches the checked wheel
`f7569221c62460d7023dc746bea32ddd290a94466af2a90bbab1cb31589e2d32`; no wheel rebuild was necessary.

The real owner dialog now has a measured existing-child→new-prerequisite route.
The fixture registers a helper-only criterion, selects explicit writer/reviewer
permission/profile templates, and approves a new helper with no child dependency.
The existing child selects that helper via the new-branch control. The response
contains the server-allocated helper ID in the child's dependencies, and the actual
attempt ledger proves the helper runs first. Fresh independent review, resumed
parent, combined/applied checks and current integration pass. A later real owner
edit still revokes currentness and leaves historical downloads readable.

The race gate changes no production method and never substitutes a verdict:
actual Application._move installs the first source object; the fixture then waits
for an API reader to observe actual Completions.current=False. That reader holds
its original false outside SQLite while the real integrator finishes. Release
requires changed task/workflow revisions, result_id and succeeded workflow. The
browser must still observe current integration and task completion after release.
The first reader may be an SSE refresh or the timer GET; the test does not attribute
it to one specific request. Both sides assert no SQL transaction during the hold.

The retained baseline replaces only completions.py with its exact119176d bytes;
all other source and test bytes match. Each variant generates its own coherent
implementation fingerprint/evidence. Under the same controlled interleaving,
baseline publication is followed by proof revocation and the positive UI disappears.
The708051a CAS retains currentness, then correctly invalidates after a genuine owner
edit. This is a deterministic regression gate, not a simulated false/pass response.

| Boundary | Outcome | Duration | Browser launches |
|---|---|---:|---:|
| Exact published wheel, helper-first | pass | 96.72s | 4 |
| Retained pre-CAS source, gated helper-first | expected failure | 59.90s | 1 |
| Current source, gated helper-first | pass | 95.39s | 4 |
| Frozen combined source219, gated helper-first | pass | 96.36s | 4 |

Successful boundaries include the existing integrated-result, historical-material
and stale-result/Stop browser regressions. Failure stops before those regressions;
these durations have different work and are not a performance comparison. Native
routes each use7 tasks,8 attempts,4 model/49 tool calls plus1 separate admission
probe. Original1800s deadline, one active workflow,4-instance/64-task/3-attempt/
60-model/120-tool limits and20% verification reserve remain. At positive completion,
reservations are zero, uncertainty false and owner dirty/untracked bytes retained.
The subsequent negative source edit is deliberate. Counts and scenarios overlap.

The retained false observes task14/workflow32 before publication and16/33 after it
in the standalone current run; the combined run observes helper task5→7 and workflow
32→33. The baseline audit confirms integration_completed before task_completion_revoked
for the held proof. All raw markers, DB/event records, screenshots and failure output
are saved. Keyboard/1440/390/320 screenshots were inspected. Ruff2 test files and
JavaScript syntax1 pass. No full pytest, neural/research matrix, new live-model or
biological-performance check ran for this browser increment.

Evidence archive `/Users/Administrator/.cache/flycode/validation/flycode-owner-browser-acceptance-checked-2mt2gk2u`:
5941 regular files/43016522 bytes, 0 special/link entries skipped.
Manifest SHA256 `df6afc84f117dd9e6c99db97cc28750199697b5f1ad864b6e4cecb8c3c9b328a`.
[Machine-readable measurements](2026-10-19-owner-browser-acceptance.json).

The automatic coordinator-process/UI/retry/restart combined route remains open.
Read-only exploration found a next concrete UI gap: after a failed independent
review, the backend can retain a stable stopped checkpoint with remaining budget,
but its parent blocker becomes child_review_failed/child_failed while the form
requires waiting_children. A successful autonomous route offers only a transient
window; existing pause states do not satisfy replan guards. Verify the supported
negative-review checkpoint next; do not call these manual-tick fixtures automatic
coordinator acceptance. Controller/admission/neural/research/full/final gates remain.
