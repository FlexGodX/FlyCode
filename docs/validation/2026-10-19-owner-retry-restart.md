# Owner retry/replan/restart — checked,2026-10-19

An explicit owner replan can now recover the same child after its automatically
approved retry is exhausted, provided its original attempt/budget/deadline limits
leave capacity. Previously Retries._fail correctly kept the last author failed
and released its pointer, but Replans only admitted unfinished published output or
planned/ready writers. The new path is confined to current retry_exhausted/retry_budget
failures. It resolves the retained review binding, stopped failed author, exact
artifact and publication event/sequence, then checks every existing review/hash/
contract/custody requirement. A unique matching pipeline_retry_blocked event is
required. Pending admission requires the current failed task's exact fence,
semantic revision and contract. Final CAS rechecks metadata. Historical readers
retain the original failed attempt; no failure is rewritten as interrupted or pass.
Generic Pipelines._artifact and normal completion guards are unchanged.

A separate measured race was fixed in Launch.finish: after reaping its owned
guardian and closing its inherited lock FD, it retries only BlockingIOError for
at most5s. Each attempt re-enters the unchanged stopped validator (regular file,
exact device/inode/ref, terminal state and stop confirmation). Other errors and
persistent contention still fail closed; global stopped/recovery stays nonblocking.
The test-only owned daemon gate uses a real independent stopped-reader lock,
never a fake receipt, result or PID signal. With unchanged custody the released-lock
browser case fails12.83s despite a real exit0; with the fix the same flow completes.
Held-lock assertions require >=5s, still locked at refusal, incomplete cleanup
invocation with no exit code/stop proof, uncertain ledger and retained tool reserve.
The owned reader eventually releases after7s. Raw stopped receipt/JUnit success
alone never grants a pass. No schema, DTO or retained-record hash changed.

The actual browser attaches the real detached coordinator, prepares both writers,
and explicitly approves one automatic retry at writer1model/1tool and reviewer0model/
4tools. Lifetime child limits are3model/3tools and reviewer12tools. English controlled
HTTP candidates edit0→-1, then the actual retry receives exact failed bytes/case
feedback and edits-1→-2. Independent registered pytest rejects both. The stable
checkpoint has5 stopped attempts,3 workflow model calls/7tools, no reserved or
uncertain resource, and original source bytes. No manual Dispatcher.tick/claim or
synthetic worker result drives the scenario.

The launching application closes while its daemon remains attachable. The test
then asks that exact daemon to stop through an inherited private control and EOF,
verifies its saved session stopped, and launches a fresh application/control. The
browser fetches a new bootstrap token and explicitly attaches again: registration
is unchanged, session differs and generation advances2→3. Attempts, spending and
deadline remain. Saved PIDs never authorize signals. This is a quiescent graceful
restart; in-flight crash/reconciliation is not newly measured here.

At the stopped checkpoint the owner pauses/resumes the root, revises the same
child objective, adds a writer/reviewer pair and selects that new branch as the
child's prerequisite. Server IDs appear in the actual dependencies, and attempt
order proves the helper runs before the third and final child attempt. Before
admission the fixture masks only the retained retry-stop event's review ID: actual
HTTP returns409 retry_changed without a new attempt or spending. The original raw
DB event is saved before mutation, restored, and the browser explicitly replays
the identical saved POST/revisions/key. No automatic fallback or substituted
positive verdict is used. The helper, third child, fresh review, parent continuation
and combined/applied verification are all executed by the real coordinator.

| Final boundary | Result | Seconds | Browser launches |
|---|---|---:|---:|
| Same wheel, transient terminal-receipt contention | pass | 63.25 | 1 |
| Same wheel, held beyond deadline | expected refusal | 19.08 | 1 |
| Extracted184-file wheel, previous no-policy replan/pause | pass | 64.55 | 1 |
| Same wheel, exhausted retry + restart + helper + exact replay | pass | 126.19 | 1 |
| Frozen merged221 source, same combined case | pass | 145.40 | 1 |

Combined routes each have7tasks/12attempts/6 workflow model calls/55tools, plus1
separate initial admission probe. The same writer and reviewer have exactly three
attempts, the two earlier negative reviews/artifacts remain, and the second
author stays failed. Original1800s deadline, dirty/untracked bytes, one active
workflow,4instance/64task/3attempt/60model/120tool bounds and20% verification reserve
remain. Positive completion has zero reserve and uncertainty false. UI/API show
current completion and applied integration; a later real owner-byte edit revokes
both. Previous no-policy route remains5tasks/8attempts/5model/39tools plus1 probe.
Counts overlap; durations are not performance comparisons.

Keyboard approval/replan/replay, root pause/resume and1440/390/320 screenshots are
checked. Ruff5 Python files, mypy3 source files, JS syntax1, exact package-map,
UTF8 README metadata and extracted owner CLI help pass. Only feature interface/
browser scenarios ran; actual product verification naturally executes registered
pytest. No full source suite, new real-LM, neural training or scientific result is
claimed. Frozen merged source retains the parallel brain work; public package
includes only this checked increment.

Retained failures/limits:

- Published3698b31 reaches the actual stopped checkpoint/restart then rejects the
  third-attempt replan (browser RED23.10s). The first corrected native
  route passes115.43s before final harness-only refinements.
- First wheel's previous no-policy case passes73.15s; its combined case and the
  first merged run exceed the old90s observation bound during final registered
  checks. Their full logs show host_close followed by revoked evidence/failed
  integration during owned cleanup; the wheel cleanup also reports an in-flight
  ASGI cancellation. Neither run counts as pass. All handles were terminal before
  rerunning. Combined observation is now180s with a300s browser runner bound,
  within the unchanged1800s product deadline; no outcome/cost/currentness assertion
  was relaxed. The wheel bytes are identical before/after this test-only change.
- A later default wheel run fails99.00s with integration verification_failed
  already recorded before the observer timeout. Its actual regression process
  completed0, with passed case/JUnit and durable stopped receipt, while the
  runner reports unavailable BlockingIOError and incomplete observer termination.
  This is distinct from the preceding observer-bound failures. A transient
  terminal-lock race is consistent with the exact code/evidence; no traceback
  identifies the other lockholder. The deterministic held-reader RED reproduces
  this class, and bounded retry plus the held-deadline negative are checked here.
- The new runner initially set passed before teardown; read-only review found it,
  and passed now requires successful owned app/service/Store cleanup. Original
  masked-event bytes are separately saved so failed cases remain self-contained.
- Harness import ordering/non-launching preflight and test-helper lint failures were retained and corrected before the final frozen measurements.

Archive `/Users/Administrator/.cache/flycode/validation/flycode-owner-retry-restart-checked-udh6adq5`: 20253 regular files/201381876bytes,
0 special/link entries skipped. Manifest SHA256
`471f378509d0543e52f04e3d7290003a3975350fcd37b88696667e4c70f05fbd`. Wheel SHA256 `b582f700501238009250cd83bb06a12679a60a8c24df92128f30c9d331008b33`.
[Machine-readable result](2026-10-19-owner-retry-restart.json).

Automatic request/message avalanche and aggregate in-flight Stop acceptance remain
open, as do controller/admission/neural/research/full/final delivery gates. This
route does not establish biological benefit or close the whole specification.
