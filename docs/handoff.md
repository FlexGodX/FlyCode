# FlyCode handoff brief

Authoritative roadmap: `docs/Plan.md` (§1–104). Operating rules: `AGENTS.md`.

## Task6 result freshness and reopened generations — 2026-10-16

The increment after `8cb1083` adds schema9 result pins: runtime location,
semantic revisions, approvals and rerun policy. `Integration.current/refresh`
checks installed and retained file objects, immutable artifacts, independent
reviews and every source verdict outside SQLite transactions. Tracker task/board/
agent reads and claim admission refresh completed results. Semantic contract
changes invalidate atomically; metadata/archive preserve valid evidence. Missing
legacy schema8 pins fail closed after restart. Historical attempts, actual spend,
user bytes and another workflow's active slot remain unchanged.

Owner `reopen` creates a new semantic generation on the same task and preserves
the original workflow deadline, spend and attempt limits. Only auxiliary reviews
matching the old record's exact contract and semantic generation retire; changed
owner requirements and the selected reopened review remain required. Retirement
is host-derived, distinct from reversible archive, and cannot be supplied through
task drafts/commands. Expired or exhausted standalone roots cannot capture the
only workflow slot after reopening.

A real second author generation passes a new approved baseline policy and fresh
review/integration checks. A separate `Integration.run(previous_result_id=...)`
path re-verifies an already applied result only when current source bytes/modes/
Git base remain compatible. It requires new independent review and fresh candidate/
source acceptance checks, writes no source delta and preserves old author attempts.
Its current check follows retained installation objects through the prior-result
chain. Owner pause, cancellation or revoked permission blocks completion, including
a historical child author paused after passing candidate checks. Replay binds the
prior result, fence, artifacts, reviews, approvals and reruns and spends nothing.

Task6 and the overall goal remain open: coordinator crash reconciliation, actual
multi-writer conflicts/resolution, the remaining stop/deadline/rerun matrix and
shared API/UI wiring. Task5 dispatcher/shared legacy endpoint admission and
Tasks7–8 remain open. No UI surface or scientific admission is added by this slice.
For the next recovery RED: host `run_pytest` keeps its Popen only in memory;
ToolCall/evidence contain no durable PGID/session/start identity. Restart must
retain uncertain ownership until actual group absence is proved. A saved PID
alone must not authorize signals or rollback because of PID reuse and spawn races.

Frozen-source validation:732 passed/8 skipped full default (558.12s),74 extracted
wheel delivery/application/runtime checks (238.28s),5 actual FlyWire checks
(5.84s), relevant Ruff and mypy23 passed. All130 packaged Python files and README
match the checkout; extracted CLI lists all ten controllers. Real Qwen repair
used3 model calls/26 tools. Its harness then hit the expected duplicate-command
key rejection; the negative report remains preserved. A new script continued the
same workflow with fresh keys: review/reverification passed in5.75s,20 additional
checks,0 new model calls, no source replacement, no refund or outstanding reserves.
Details, frozen identity, wheel hash and artifact archive:
[validation report](validation/2026-10-16-result-lifecycle.md).

## Task6 exact artifacts, host review and verified integration — 2026-10-16

The increment after `8395e5f` adds frozen `domain/results.py` contracts and
Store schema8: artifacts, reviews, integrations and durable execution lanes.
A stopped real worker's byte/mode delta becomes an immutable artifact only
after recorded group absence and settled model/tool calls. Scope violations
retain the raw result and an explicit blocker. Publication frees unused reserves
and the instance, keeps actual spending, and leaves the author awaiting review.
Pause/cancel/release now settle that already-stopped author without modifying
an instance that has since been assigned elsewhere.

`Reviews.run` uses a separate reviewer attempt/instance, exact input artifact
hash and an owner-approved four-stage oracle. This is actual registered-check
review, not a claimed semantic/model review. Every subprocess is charged through
`CheckExecutor` in the shared ledger. Exhausted/unstarted checks cannot approve;
owner cancellation after passing checks retains the evidence but produces an
inconclusive review and settles the cancelled attempt. Changing reruns on replay
is an idempotency conflict. Durable execution lanes prevent concurrent/replayed
worker, host-review and integration execution for one attempt, including restart.
They preserve unknown work; they do not automatically recover or replay it.

`Integration.run` binds exact reviews, author ownership and all workflow task
contracts; serializes an integration record; checks the merged copy; applies the
approved delta; and performs fresh registered source verification before atomic
canonical task/workflow success. No filesystem check or subprocess runs inside
the final SQL transaction. Other active attempts, unselected published root
assignments, changed source bases, artifact/contract changes and unresolved
resources block completion. Root aggregation cannot hide a current assignment.

`Application` uses native exchange/exclusive rename with no-follow directory
descriptors. The actual displaced objects stay outside the subject, preserving
concurrent replacements and late writes through open descriptors. Rollback uses
those objects rather than recreating stale baseline bytes; uncertain races retain
both versions for reconciliation. The persisted intent journal supports local
rollback after reconstruction. A final current-object check prevents passing
source tests from hiding changes to retained originals. No unsafe rename fallback
is allowed. These real filesystem checks ran on macOS; Linux uses renameat2 but
has not been executed here.

An actual terminal-channel race also appeared during negative testing: normal
control closure after an accepted worker result could be classified as runtime
Stop. A deterministic RPC/control-boundary test reproduced it. Terminal closure
now preserves the raw result, while fencing, cleanup errors and group absence
still gate publication.

Task6 remains open. Next: coordinator recovery/reconciliation after host crash;
current-result invalidation and reopen/archive audit; idempotent integration
lookup; actual two-writer conflict/combined-regression scenarios and canonical
resolution tasks; full cancellation/deadline/rerun matrix; shared API/UI wiring.
The completion path above is an internal host service, not a new UI workflow or
completion authority for model/browser input. Task5 dispatcher/shared legacy
endpoint admission and Tasks7–8 also remain open. No biological/scientific or
orchestration admission follows from these HTTP-fixture/process tests.

A fresh bounded live check also passed on the same frozen implementation
`ce5bf96e533bccb295ee4a79db2b209051223862bd5019fb5292f6c0379d8385`.
The loaded Qwen primary probe passed in19.99s (one request,8192 context).
Its separate actual canonical repair completed in49.01s:3 primary model calls,
5 author tools,4 independent review checks and17 integrator tools,26 tools total.
The workflow reached succeeded with current source acceptance, no outstanding
reserves/uncertainty, unchanged test files and preserved user-dirty binary bytes.
There was no scorer, fallback or model loading. This is one small repair smoke,
not a generalization, semantic-review or orchestration-admission claim. The old
Task5 timeout below remains a retained negative result of its earlier run.

Frozen-source full default suite:703 passed/8 skipped in394.79s. Relevant Ruff
and mypy pass (21 source files for mypy). An isolated pip build produced
`flycode-0.1.0-py3-none-any.whl`; all130 packaged Python files and README match
the checkout byte-for-byte. Extracted CLI lists all ten registered controllers.
Wheel SHA256:
`514d1a14b5322ac288eef004e146973cc601c4c3dd675e3e07042fbbfd1f3461`.
All48 extracted-package application/delivery/runtime checks pass in65.97s;
all5 opt-in real FlyWire checks pass in5.15s. Source identity remains unchanged.
Full JUnit/logs, wheel, live scripts, real workflow ledger, oracle, snapshots and
evidence are archived under
`~/.cache/flycode/validation/flycode-task6-validation-d603178d` (269 retained
files plus the checksum manifest). This is a historical measurement archive;
absolute subject/evidence references retain the original execution paths.

## Task5 model-selected communication — 2026-10-16

The increment after `5889867` connects the existing typed broker to actual
model-selected `send_message` actions. Canonical permissions accept this tool;
legacy `VALID_TOOLS` and defaults remain unchanged. Both explicit host routes
and task/profile permission are required. The primary request includes approved
recipient IDs and argument guidance; a request that cannot fit that optional
guidance emits `message_tool_context_deferred` and excludes the tool for that
decision while preserving an otherwise valid baseline request.

The child forwards a selected action over inherited RPC. `Messages.execute`
checks its closed arguments/routes, derives idempotency from the host-issued
action ID, and uses `Tools.begin` before publishing. A rejected broker operation
records a spent failed tool; it does not cancel unrelated execution or refund
spending. Stale ownership/budget gates still prevent effects. Replaying an
attempt/action after the host actor changes cannot debit or publish again.
`AttemptEnvironment` reuses normal observation/transition bookkeeping through
a narrow dispatch override, without a second debit or a legacy global tool.

Real two-worker HTTP-fixture execution selects a question and then an answer
from the actual received question envelope, preserving causation/correlation
and source bytes. The first version exposed only the question's omission-marker
ID: controller progress had filled the context allowance. A stronger test
requires the question text; substantive data now precedes progress in the same
bounded context. A further RED case showed that acknowledging a question lost
it before the next decision. `Messages.remembered` now retains accepted
substantive data within its original TTL, participant fences and artifact/read
permissions. Closed questions and losing answers are excluded. Repeated context
does not create another receipt or close another question.

Read-only exploration then identified a post-inference race: a sender could
stop or a message expire while HTTP was blocked, and an existing historical
receipt let the influenced candidates escape. Four actual blocked-HTTP RED
cases reproduced pending/received × expiry/revoked sender. `accept_context`
now rechecks included dependencies and acknowledges consumption atomically
after HTTP. Invalidated candidates are discarded with `message_context_stale`
and a metadata-only event; actual model spending remains recorded. Reinspection
found no concrete bypass. No receipt is rewritten as current authority.

All84 broker/context/tool/process message checks pass in44.79s. The earlier full
collection664 passed/8 skipped in336.38s before the accepted-context additions.
A second collection668 passed/8 skipped/1 failed in444.72s:
`test_validation_binds_real_shadow_data_to_controller_task_and_model_scope`
correctly rejected source changes made during the suite. Its retained report
has `source_unchanged=false`, start fingerprint
`43665ebf15aa131374998206d6299b7bdb7e4194cfa58ca13c60814dec1c1621`
and final fingerprint
`8dd8f3e84712f066b7919e30ad14f2a1096a09e0a15dad6c30c2ef9c14d7041c`.
The guard was preserved; the final suite runs after all source edits finish.
Relevant Ruff and mypy14 source files pass. The release wheel's Python sources
and README were compared byte-for-byte with the checkout before extraction.
All225 orchestration/local-cortex/process-cleanup checks passed outside the
checkout in126.35s; extracted CLI `brains` lists all ten registered backends.
Wheel SHA256:
`e37387ec79279778cd911b214ceb38fdb5818b0a958f04ae32987efbdd07df07`.
Final full default suite:673 passed/8 skipped in381.62s with unchanged
implementation fingerprint
`8dd8f3e84712f066b7919e30ad14f2a1096a09e0a15dad6c30c2ef9c14d7041c`.

Live-model limitation: the already loaded `qwen/qwen3.5-9b` reported8192 context,
but its one explicit primary compatibility probe returned `APITimeoutError`
after30.11s. No task-generation request followed, no fallback/model load or blind
retry was attempted, and no successful live-model communication is claimed.
The negative report is retained at
`~/.cache/flycode/experiments/2026-10-16-message-tools-live/run-4zpk9oqd/report.json`.
HTTP-fixture/process checks do not establish biological or orchestration
admission. Child-wait dispatch, shared legacy/probe endpoint admission,
generated-artifact review/integration/completion and connected UI/API remain
open; Task5 and the overall delivery goal are incomplete.

## Task5 addressed messages — 2026-10-16

`Messages.send/pending/acknowledge/journal` use the common Store. Schema6 adds
messages, attempt-keyed batches, outbox/inbox and open questions without changing
existing assignment/spending. Envelopes schema1 derive workflow/task/sender and
recipient attempt from host authority, use explicit max3 recipients,16KiB UTF-8,
TTL≤300s, inherited expiration, hops≤4 and max200 deliveries per workflow.
Host actor changes cannot duplicate an attempt's idempotent send. All eight
message types have closed schemas. Deliverable artifact ID/hash links must be
approved inputs of both task contracts; obsolete links from revoked senders remain
audit data and cannot authorize paths, artifact fetches or message delivery.

Pending is repeatable until receipt. Inbox, first correlated-answer closure and
event/outbox commit atomically. Outbox failure rolls back the entire operation.
Expired/revoked messages remain audit-only; both parent participants bind to their
original attempt, so an answer cannot reach a replacement using the same instance.
Causal replies resolve their original recipient attempt even after it becomes idle;
contract/scope changes on either participant preserve only audit data.
A stale first broadcast target does not invalidate a valid peer's question. A late
answer survives later contract/scope changes as audit-only data, including obsolete
artifact references; it never becomes context for a current attempt.
`cancel`, handoff/task requests and review recommendations remain data, without
queue commands, permission grants or completion authority.

Explicit host-approved routes pass real controller activity over worker RPC;
`AttemptCortex` adds labeled, bounded excerpts with sender/attempt/task revision
and contract hash. Successful actual inference acknowledges only included
messages. Local verification, errors and omitted messages do not acknowledge.
Large entries cannot starve small ones. The existing primary client now shares
its actual candidate-prompt builder with context preflight, so messages or their
omission marker cannot overflow an otherwise valid baseline request. Deferred
context and blocked notifications emit metadata-only events; a stopped peer or
exhausted message budget does not abort the agent or reset execution spending.

A real two-worker check holds the first in its actual pytest tool while its
controller message appears in the second's actual HTTP generation request;
source bytes remain unchanged. This is process/HTTP-fixture evidence, not a
live-model/research or integrated-completion claim. Model-selected question/
answer tools, child-wait dispatch, dispatcher/UI/API wiring, legacy/probe endpoint
admission, generated-artifact registry and exact review/integration/completion
remain open. The owned-process checkpoint is `681aa5f`; Task5 is still open.

The final late-contract run exposed a real macOS cleanup race in
`test_real_worker_runs_agent_in_private_copy_without_claiming_completion`:
639 passed/8 skipped/1 failed. Permission denial occurred before the leader was
waitable; it then became a zombie during group-absence polling. The original
one-time reap left the durable worker uncertain. A deterministic owned-process
regression reproduced it; cleanup now reaps the owned leader throughout polling
and still independently requires complete group absence. Persistent permission
denial never proves exit. The subsequent collection641 passed/8 skipped in316.62s;
that collection predates the final two historical-recipient cases. All54 message
cases now pass with original-attempt routing.

Final verification: full default suite643 passed/8 skipped in324.54s. All195
queue/message/local-cortex/process-cleanup cases passed from the extracted wheel
outside the checkout in102.37s; extracted CLI `brains` passed. Relevant Ruff and
mypy15 source files pass. The preceding broader queue/verification/local-cortex
increment suite238 passed in107.03s before those last two cases. Wheel SHA256:
`89d7557a738dbc8681e50c846d884e416ff7d7690fda62a6e5b16a907e994542`.
No fresh live-model, biological admission or browser acceptance is inferred.

## Task5 owned worker and private workspace — 2026-10-16

`Runtime.launch` now executes the actual `run_agent` in a host-owned POSIX
process group and an independent checkout. `Workspaces.prepare` observably
reuses shared snapshot capture, preserving dirty/untracked bytes, modes and
selected-folder Git inventory. The original Git directory is never shared;
private baseline commits disable hooks, filters and encoding normalization.
Baseline/manifest tampering and conflicting concurrent registration are checked.
Schema5 adds workspace/worker records without resetting prior spending.

Provider clients stay in the parent, in a separate RPC thread; the child has
its own scoped Store connection for gated tools. A separate supervisor renews
heartbeat10s/lease30s and stops owned descendants independently of blocked HTTP.
The child's watchdog starts before heavy imports and kills its own group on
control EOF/timeout. Frames are bounded4MiB JSON with checked request sequences;
these are private worker RPC, not ORC addressed agent messages. A launch barrier
prevents admission before both parent threads exist. Partial socket/home/process/
thread failures close allocated resources and release only proven unstarted work.
Readiness, not process creation, records the actual start. Missing biological
graph is an explicit startup failure, never a synthetic substitute.

Stop/pause retains endpoint ownership until the real HTTP outcome arrives; late
responses cannot restore a fence. Unknown pytest effects retain reconciliation
even after group absence. Failure to open the supervisor database stops the
owned process without releasing durable ownership; recovery/expiry must reconcile
it. Client cleanup errors cannot strand supervision. Fresh candidate IDs allow
legitimate repeated local-verification generations without replaying tool effects.
Shared process cleanup now reaps its owned zombie leader before independently
checking group absence on macOS; permission failure alone remains insufficient.

Raw `run-result.json` and its hash are retained outside the input project. Normal
results remain reconciling with `review_artifact_pending`: they are not immutable
patch review, integrated verification or canonical completion. This adapter
supports trusted local POSIX projects with full-read permission and local file/
pytest tools. Untrusted, Git/service profiles and detached resources remain
unsupported. Dispatcher, addressed messages, legacy/probe shared admission,
exact patch/review/integration and UI/API wiring are still open. Task5 and the
overall goal remain active. The previous tool-effect checkpoint is `4ad3344`.

Verification: full default suite588 passed/8 skipped in293.69s. All119 queue,
workspace/runtime and process-cleanup cases passed from the extracted wheel
outside the checkout in67.91s; extracted CLI `brains` passed. Relevant Ruff and
mypy13 source files pass. The process tests include actual HTTP blocked during
Stop/heartbeat, pytest descendants, control EOF, recovery, missing graph and
injected allocation/thread/client-cleanup/database-open failures. Wheel SHA256:
`06cacbf4f3ed3b4872af3a41c4b225dadf8a372ffe312d7c42222c7723cfd40e`.
No new live-model, biological admission or browser acceptance is inferred from
HTTP-fixture and process tests.

## Task5 canonical tool effects — 2026-10-16

`AttemptEnvironment` gates the actual `SoftwareEnvironment.step` before counters
or effects, including locally selected pytest. `Tools` atomically records one
debit/call/event/outbox; schema3 upgrades schema1/2 and retains assignments and
spending. One active/uncertain tool per attempt is enforced across connections.
Duplicate action IDs cannot replay an effect. Arguments are represented by a
hash; contents/stdout are not added to canonical tool events. Normal failed
results consume budget, while exceptions and pytest timeout retain uncertainty.
Recovery/lease fencing marks open calls uncertain. Unknown calls block ownership
release even after spending acknowledgement; late trusted host acknowledgement
is audit only and never restores the old fence. Stop is local control and needs
no extra model/tool debit.

File guards enforce normalized read/write scopes, reject protected case variants,
symlinks/hardlinks/special files and unknown arguments. Local pytest rejects option/
path escape/service injection and clamps timeout to the workflow deadline.
This adapter requires exactly `read_scopes=('.',)` until filtered observations
exist; local file tools/pytest are supported, Git/service tools are blocked.
It is for a trusted host-supplied copy, not a hostile-code sandbox. Arbitrary test
code still has normal host privileges. Owned worker supervision, child-process
confirmation, automatic private-copy lifecycle, messages and review/completion
remain open. Do not acknowledge unknown tool outcome based only on worker text.

The actual `run_agent` copied-project smoke now records both a model request and
one file read in the common store. A separate real pytest test exhausts the tool
budget and confirms reset cannot refund it; local Stop still works. Projects
above200 files now truncate the observation instead of failing validation; the
explicit list tool still returns all files. The first full-suite attempt exposed
a duplicate test basename; it was renamed to `test_attempt_tools.py`, without
changing pytest's import mode.

Verification: full default collection558 passed/8 skipped in260.21s; its collection
predates the final case-variant test, which passed in the latest40 tool/resource
cases (21.49s) and in the extracted-wheel57 cases (22.05s). The broader initial
queue/environment/agent integration set126 passed in51.04s before that last case.
Changed-file Ruff and mypy5 source files pass. Extracted CLI `brains` passes.
Wheel SHA256: `e9c84b85951804e1dc4800e8254e62dade8e1c560cf90ea4b8519ff5a761ace8`.
No fresh live-model, biological-graph or browser acceptance is inferred. Task5
remains open; the previous cortex/capacity increment is published in `a334154`.

## Task5 canonical cortex and loaded capacity — 2026-10-16

`AttemptCortex` binds the existing `run_agent`/`LMStudioCortex` to canonical
objective, acceptance criteria, instructions and tool names. Every actual primary
request uses `Resources`; local reset never refunds spending. An HTTP-fixture
smoke executes one real FSM-controlled file read in a copied project and records
actual request/token usage. It is not a repaired-task or verified-completion test.

Explicit native metadata preflight pins loaded-instance ID, context length and
configuration fingerprint. Missing/invalid/unloaded metadata sends no inference;
changed loaded configuration clears measured protocol roles and invalidates old
profiles. Requests use the actual loaded instance, including opaque aliases.
Profile registration is HTTP-free and rejects requested context above capacity.
A conservative UTF-8-byte/output guard rejects oversized primary input before
HTTP/debit; exact tokenizer/template accounting and context assembly remain open.
The adapter also rejects a surrounding SQLite transaction before metadata HTTP.

Verification: full default suite534 passed/8 skipped in251.28s; catalogue/resource
suite28 passed in15.27s; the earlier broader increment suite133 passed before
the final eight cases were added. Changed-file Ruff and mypy6 source files pass.
The same28 catalogue/resource cases passed against the extracted wheel outside
the checkout (15.55s), as did CLI `brains`. Wheel SHA256:
`f62c24ac62dcebf647af571bd0039e04d7b904fe15b984a303f8ea299f1a0d32`.
No new live-model, graph or browser acceptance is claimed. Supervisor/process
ownership, per-tool scopes/budgets, messages, isolated writer lifecycle and exact
review/integration/completion remain open. Legacy/probe endpoint admission is
still separate; this increment does not serialize every existing UI model call.

## Scoped snapshot inventory — 2026-10-16

Fixed the confirmed shared `verification.snapshot.capture` subfolder defect:
Git status now uses the selected folder pathspec and paths are normalized using
Git's working-tree prefix. NUL-delimited rename pairs preserve whitespace and
Unicode; moves across the project boundary retain only the selected side.
Sibling paths and the trailing empty record are omitted. Tests cover modified/
untracked files, internal rename, inbound/outbound moves and repository-root
rename, while asserting source Git status and dirty/untracked bytes stay intact.
This fixes the scoped inventory prerequisite; isolated writer lifecycle,
patch/review/integration and verified completion remain open Task6 work.

Verification: full default suite520 passed/8 skipped in243.41s; verification
suite64 passed in16.53s. Changed-file Ruff/mypy pass. Five new real-Git cases
also passed against the extracted wheel; SHA256:
`2f4fc5713988aa36dbf85166f82acdee47e434ecc16120dc90a4c798b1c42489`.
The earlier model-transport checkpoint is `41016ba`; no supervisor was started
and no new live-model/research/UI acceptance is inferred from these checks.

## Task5 model transport — 2026-10-16

`orchestration.resources.Resources` atomically admits a pinned profile's request,
debits one model call from its reservation and records a canonical event/outbox
in the same SQLite database. Schema2 adds `model_calls` and a unique active/
uncertain endpoint index; schema1 is accepted/upgraded. `execute` calls actual
HTTP after committing. Wait is cancellable before admission with no debit.
HTTP status errors spend a call; timeout/connection loss retains the endpoint.
Recovery/lease expiry records open transports as uncertain rather than replaying.
Confirmed real responses after fencing are audited but not returned to the worker.
Unknown transport ownership blocks `confirm_stopped`, even when spending was
acknowledged. Available token usage is retained; invalid usage fails without
leaving known-finished HTTP running. No prompts, response bodies or credentials
are stored in these events. The acknowledgement API is trusted host code, not
an authority field accepted from agent messages or HTTP JSON.

`OpenAICompatibleLLM` and `JevStyleDecisionClient` accept optional host request
executors at the actual SDK boundary; counters advance after admission. Both
primary generation and remote consolidation are covered. `make_lmstudio_cortex`
passes separate primary/scorer executors and rejects a partially guarded pair.
Legacy clients with no executor keep their behavior. These hooks are not yet
wired to a dispatcher, UI or probes, so Task5 remains open. In particular, do
not claim all endpoint activity is serialized until legacy/probe wiring exists.

Next runtime prerequisites: persistent model binding/capacity admission across
worker processes; owned process supervision/heartbeat; per-tool argument guards;
message delivery; isolated writer copies and actual completion/review. The shared
snapshot API is `verification.snapshot.capture(root, destination, ref=SubjectRef)`.
Exploration reproduced a real subfolder inventory leak; the fix and checks are
recorded above. Reuse the now-scoped shared capture for worker workspaces.
`evidence.atomic_json` can retain raw
RunResult, but its legacy observation success is not canonical verified completion.

Verification: full default suite515 passed/8 skipped in238.99s; targeted
queue/LLM/tracker suite109 passed in40.08s. Changed-file Ruff and mypy13 source
files pass. A broader `llm/` Ruff scan also found three existing out-of-scope
findings: RUF022 in `llm/__init__.py`, C408/RUF046 in `candidate_generator.py`.
They were not changed. Thirteen transport tests and CLI `brains` passed against
the extracted wheel outside the checkout. Wheel SHA256:
`df5d8480306e0795ae6b183fb4d6db2f027610dd146d195ec37fa59fc6bba9dc`.
No fresh live-model, biological research admission or UI acceptance is claimed.

## Task5 lease/deadline foundation — 2026-10-16

`Store.heartbeat` extends only current, unexpired ownership to now+30s, without
resetting spending or the workflow deadline. `Store.expire` moves expired leases
to reconciling, revokes fencing, retains instance/reservations and marks spending
uncertain. A healthy heartbeat cannot override the 30-minute workflow deadline.
Deadline moves the workflow to incomplete but retains its active root slot until
all owned attempts have confirmed stop; user cancellation remains cancelling then
cancelled. Repeated sweeps and another SQLite connection cannot resurrect the lease.
These are store operations; a live supervisor calling heartbeat every10s and
actual process-stop proof are still missing. Task5 is not complete.

Verification: 51 targeted tests passed; full default suite502 passed/8 skipped
in233.33s. Relevant Ruff/mypy pass. Five new lease/deadline cases passed against
the extracted wheel outside the checkout; wheel SHA256:
`2d82891f6ab8fb2c4c9eee5fc0af3b51929b8e7e29bd7c5b5245ff96e395b774`.
No new live LM, graph admission or browser acceptance is claimed.

Next runtime reuse: `verification.runner._terminate(Popen)` confirms absence of
an owned POSIX process group created with `start_new_session=True`. Ordinary
agent tool subprocesses inherit the worker group, but nested verification creates
another session and must be tracked separately. Docker daemon resources and
detached processes escape that group; block unsupported resource profiles until
ownership tracking exists. Never kill a user-owned server or trust a recovered
numeric PID as proof of ownership. The detailed Task5 notes are in the delivery plan.

## Canonical queue foundation — 2026-10-16

Task4 now has shared Pydantic workflow/task/profile/instance/attempt contracts,
one SQLite store and tracker commands/projections. Claims commit ownership,
reservation, sequenced event and outbox together. Unique active task/instance
indexes, `BEGIN IMMEDIATE`, scoped idempotency and monotonic fencing cover two
connections, stale revisions and replay. Profile admission pins an explicitly
probed server model descriptor revision; discovery and catalogue lock contention
cannot trigger HTTP or qualify a claim. Context capacity remains Task5.

Drafts are inert; enqueue requires objective, acceptance, approved permissions
and budget. Graph checks include aggregate-parent completion edges. Limits are
one active root, four active attempts, 64 tasks, depth three/eight children,
three attempts, 60 model/120 tool calls and 30 minutes from first confirmed start.
Twenty percent remains available for review. Actual first starts, rather than
assignments, advance separate root/internal every-eighth aging counters.
Pause/retry keep spending and deadlines. Recovery retains ownership/reservations,
marks spending uncertain and requires host reconciliation before release.
Tracker shows assigned/running separately and retains terminal attempt history.

This is the persistence foundation, not a live team executor. Next: Task5 actual
`run_agent` adapter, endpoint resource pool, messages, heartbeat and process-stop
proof; then Task6 isolated writer artifacts/review/integration/completion. No
manual command can manufacture `succeeded` or bypass the pending completion gate.
HTTP principals must derive `AccessScope` from host authority, never request JSON.
Current interfaces continue using the existing single-agent runtime.

Verification: full default suite 494 passed/8 skipped in 234.64s; final targeted
queue/catalogue/tracker suite 46 passed in 19.29s, including three tests added
after full-suite collection. Relevant Ruff and mypy (nine source files) pass.
Wheel SHA256 `7ddbf6d0680976a7758caf2863801e7f59acefa9e7b623adba1d3aefbe716547`:
extracted-package SQLite create/restart/tracker and CLI `brains` passed outside
the checkout. Eight opt-in tests remain skipped; no fresh live LM, graph research
admission or browser acceptance is claimed for this backend increment.

## Feedback benchmark and rejected training candidates — 2026-10-16

English-prompt `benchmarks/feedback-v1` is executed on runtime `694d42d`:
200 frozen baseline + 40 primary trained + 40 exploratory refined + 10 teacher
and 6 real Qwen runs = 296 agent runs. Original tests/case IDs were independently
checked, with 306 audits and four pristine negative controls. All raw evidence,
repositories, telemetry and ten numeric checkpoints remain under
`~/.cache/flycode/experiments/2026-10-16-feedback-v1`; public outcome/feedback/
manifest hashes are in `benchmarks/feedback-v1/results.json`. Recipe and report:
`benchmarks/feedback-v1/README.md`, `docs/validation/2026-10-16-feedback-benchmark.md`.

Functional teacher (90 actions) improves imitation 44.4%→88.9% but regresses toy
completion 14/20→10/20. Separately registered refinement on ten successful Full
Fly bias episodes (50 actions) reaches 100% imitation and 20/20 toy completion,
but heuristic held-out remains 0/20. Real single-Qwen transfer: frozen 2/2,
primary trained 1/2, refined 0/2. Three failures exhaust 8-call budgets; no provider
errors. Entire live phase: 585.54s, 34 requests, 45 tools, sequential model events.
The refined helper case repeatedly tests without editing. Both training candidates
are rejected as live replacements; defaults stay unchanged. No further held-out
tuning was done. Preserve this negative result, not just the toy improvement.

Real graph source checksums/fingerprint and all ten checkpoint mappings/mode
readouts/topology were verified; only tool readout and bounded gain/leak change.
Identity now includes training and telemetry, reproduced RED then fixed. Relevant
research/training tests: 38 passed; opt-in real graph tests: 5 passed; Ruff/mypy passed.
This is four known synthetic families, not matched compute or §§90–99 evidence.
The orchestration goal remains active: Task4 persistence is delivered above;
this standalone benchmark does not implement or admit ORC19/24.

## Live single/dual model acceptance — 2026-10-16

On runtime `18913d5`, a real CLI `--no-decision-model` repair completed in 134.65s
wall time with 3 Qwen requests, 6 tools, no scorer construction and no model errors.
The explicit opt-in dual test passed (1 test/93.60s; 94.43s wrapper wall time):
missing `decision_model` preserved the Jev default, with 3 Qwen + 3 Jev requests,
nonempty decision scores, real repair and local verify/STOP. Each cortex's observed
model events were sequential. These are single-instance checks, not proof of a
shared multi-agent endpoint pool.

Both produced projects then passed the new owner-approved baseline negative control,
target, full fixture regression and integration plan, exact add/subtract case IDs,
unchanged original tests and current artifact checks. Public metadata/raw metrics:
`docs/validation/2026-10-16-model-selection.json`. Retained external bundles:
`~/.local/share/flycode/validation/model-selection-20261016-299afcda9b7b`.
The relocated verdicts were checked current. Different prompts/budgets/cache states
make the durations unsuitable for performance comparison; no research/orchestration
admission is inferred. Earlier 5s timeout remains recorded below.

Tasks1–3a are delivered for their supported scope. Task4 persistence is recorded
above; shared runtime/resource/review/completion remains next. Seven biological-graph
cases remain opt-in/unrun in this increment; a fresh graph/research acceptance is
still required on the final runtime.

## Trusted acceptance plans — 2026-10-16

`AcceptanceContract` maps required criteria to exact check/case IDs. The external
`OracleRegistry` stores immutable owner-approved test bytes and semantic versions;
agent proposals append optional checks only. `PlanBuilder` binds staged snapshots
and subject refs; `VerificationEngine.verify_plan` runs baseline/target/regression/
integration with real subprocesses, graph dependencies and isolated oracle overlays.
Standalone `verify` remains `registered-suite`; acceptance has a distinct scope,
criterion coverage and check outcomes. Raw negative-control failures remain raw.
New owner approval, runtime/input changes and evidence tampering invalidate current
results. JUnit and observer outcomes now agree as well as case identities.

Verification: full suite 452 passed, 8 skipped (188.54s); targeted verification/
identity62 passed. Ruff/mypy passed. Final wheel SHA256:
`95612fb177e7b42b8134bac24111cf107429d2981c6bba1755b74529272e5c3a`.
Extracted-package CLI pass/empty exit codes and a real staged acceptance plan
passed outside the checkout. Seven graph-dependent and one dual-LM opt-in tests
remain skipped in the default suite; they do not close live/research acceptance. This delivers the supported trusted-local pytest
path; hostile sandbox, other runners, durable recovery and workflow completion
remain later work. Next: canonical SQLite WorkItem/Profile/Instance/Attempt queue
(Task4), followed by runtime/review/integration and its acceptance gate.

Cleanup audit: one additional real subprocess test returned `PermissionError`
during owned-group cleanup after pytest exited successfully; 20 instrumented
repetitions did not reproduce it. The failure was retained as inconclusive.
Signal-edge regressions now require confirmed absence after a permission race and
SIGKILL; persistent permission failures remain interrupted/unconfirmed. Optional
checks cannot bypass this global cleanup gate. This does not establish a kernel
cause or hostile-process isolation.

## Searchable session journal — 2026-10-16

The existing Journal tab now projects canonical run events with text search,
source/error filters, lazy JSON details, bounded rows, event/step IDs and live
follow. Both classic and desktop shells use the same implementation. A new local
`GET /api/runs/{id}/journal` streams a fixed recorded prefix as JSONL outside the
writer lock; filters never change the exported history. Event schemas and training
telemetry remain unchanged. Upstream source analysis and scope are recorded in
`docs/specs/session-journal.md`; execution plan is `2026-10-16-session-journal.md`.

Verification: all 44 interface API tests passed. Both browser surfaces cover
export/restart, real repair, SSE replay, filtering, mobile, pagination, duplicate
events, keyboard focus and HTML text safety. The staged journal-only snapshot
passed 21 API tests and browser acceptance. Classic and desktop regressions also
passed. Ruff/mypy and JavaScript syntax passed; unrelated suites were skipped.
This UI addition does not close verification/orchestration roadmap phases.

## Optional desktop workspace — 2026-10-16

`/desktop` adds a separate presentation based on measured layout/tokens from
the installed Codex renderer. `/` retains the classic interface. Both use the
existing runtime, projects, run history, model preferences and event stream.
The desktop shell has light/dark themes, collapsible navigation, keyboard
controls and an optional inspector. No orchestration phase is closed by this UI.

Local projects may register existing folders or create new ones without Git.
The native chooser runs in a separate Python/Tk process. Projectless chats use
explicit `project=null` and separate empty directories; omitted project retains
the legacy API demo default. See `docs/specs/desktop-interface.md` and the two
UI plans dated 2026-10-16.

Verification: 17 interface API tests passed; desktop/390/320 browser scenarios
covered real demo repair, Stop, history/changes, themes and keyboard. Classic,
project and projectless-chat browser acceptance also passed. The staged UI-only
snapshot independently passed 17 API tests and desktop browser acceptance.
Changed-file Ruff/mypy and JavaScript syntax passed. No broad suite was run for
this UI task; the verification foundation remains separate work.

## Specification delivery — 2026-10-16

Active goal: implement §§100–103 using
`docs/superpowers/plans/2026-10-16-specification-delivery.md` and §104.
The goal contains the same eight delivery stages. Scientific claims remain gated.

First increment: optional scorer (`decision_model=null`, CLI `--no-decision-model`),
server catalogue and explicit one-request role probes, model dialog plus execution
settings button immediately to its right. Missing scorer field preserves dual default.
History inspection no longer overwrites next-run controls; personal model choice persists.
Runtime model timeout is now configurable from execution settings.

Verification before this increment's commit: 371 passed, 8 skipped (151.89s).
Changed-file Ruff and mypy passed. Visual checks at desktop, 390px and 320px:
model/settings targets44px, correct order, no horizontal overflow. Actual Qwen
compatibility probe timed out at30s; no live single-Qwen repair acceptance claimed.
Deterministic HTTP tests cover both model protocols and absence of scorer requests.
Existing scoped rollout evidence is stale after core source changes; regenerate after
implementation freeze rather than relabelling old evidence.

Second increment: `verification/` implements trusted-local snapshots (bytes/modes,
base/inventory, installed environment and runtime hash), a registered pytest runner,
independent collection/case observation + JUnit reconciliation, deterministic verdicts,
bounded redacted artifacts/checksums and `flycode verify --repo PATH`. All rerun outcomes
are retained; failure/skips/xfail/xpass/flaky, timeout/cancel and empty/incomplete reports
cannot become a false pass. Process-group cleanup includes children after pytest exits.
Untrusted execution is blocked. Git metadata files/directories are excluded, Git discovery
is confined and source index/status preservation is tested on a real worktree.

Verification: 423 passed, 8 skipped (171.43s); targeted verification/identity33 passed;
Ruff/mypy passed. A read-only audit found four defects; regression tests reproduced them
before correction, and the auditor independently reran the fixes (4+6 passed).
The live UI single-Qwen smoke made one primary request, no scorer request and ended
incomplete on its5s timeout. Stop prevented further work in a separate run; both model
and settings controls were disabled while active. History with a legacy pair left the
personal single-model choice intact. These are smoke outcomes, not successful live repair.

The trusted criteria/check/case mapping and staged pytest plan are now implemented
above. Next: canonical SQLite contracts/queue Task4; standalone suite verification
is not a task/workflow completion gate. No ORC/TRK implementation or new scientific admission exists.

The baseline MVP covers Phase 4 (§78). Phase 5 (§79) now runs the same tasks
with a verified, sparse biological graph. Hermes in the roadmap means FlyCode
itself: connected agents are hosted here and orchestrated by the controller.

## Proposed task tracker (2026-10-16)

`docs/Plan.md` §103 incorporates `docs/specs/task-tracker.md` v1.0. Proposed
Phases 26–29 cover task creation/backlog/board, atomic task requests and assignment,
live agent→task visibility, handoff/recovery and verification-backed completion.
TRK-01..11 map to TT-01..16. Reuse canonical WorkItem/AgentAttempt and the ORC
store/dispatcher; do not implement a second queue or equate profile names with
running instances. Multiple root tasks remain visible with one active workflow
in the first version. This is specification-only work; no tracker runtime or
new API was implemented.

## Proposed model selection and settings split (2026-10-16)

`docs/Plan.md` §102 incorporates `docs/specs/model-selection.md` v1.0, including
single Qwen without Jev, optional primary/scorer roles and future compatible models
through the same extensible catalogue. The composer has one model-selection
button and a separate connectome/steps/execution-settings button immediately to
its right. MOD-01..07 map to MS-01..12. The user explicitly requested specification
only; no nullable-decider support or UI implementation was performed. Model mode
must become part of admission identity rather than reuse dual-model evidence.

## Proposed verification module (2026-10-16)

`docs/Plan.md` §101 incorporates `docs/specs/testing-module.md` v1.0: proposed
snapshot/plan contracts, isolated runners, deterministic verdicts and immutable
evidence for project correctness, orchestration reliability and research.
TST-01..15 map to TV-01..17 and proposed Phases 21–25. Phases 21–22 support
single-agent execution first and must precede acceptance of multi-agent phase 17;
the numbering does not create a dependency on completing all of Phases 15–20.
This is documentation only. Existing pytest/tool/research results do not certify
this new module; `run_tests`, completion gates and admission keep their current
runtime semantics until an explicit, tested migration is implemented.

## Proposed next scope: multi-agent orchestration (2026-10-16)

`docs/Plan.md` §100 incorporates `docs/specs/connectome-orchestration.md` v1.0.
This is a specification-only delivery; no orchestration runtime or new API exists
yet. Proposed Phases 15–20 cover profiles/DAG/scheduling, communication and shared
budgets, isolated patch review/integration, neural shadow, scoped influence, and
the team interface. ORC-01..13 requirements map to AC-01..14 acceptance scenarios.
Existing Qwen/Jev model routing and single-agent admission must not be relabelled
as multi-agent execution or used to authorize it. The previous implementation
goal remains completed; implementing this new specification is separate work.

## Latest increment: guarded rollout and browser configuration (2026-10-16)

Plan: `docs/superpowers/plans/2026-10-15-validated-rollout.md`.
The implementation goal is fulfilled: Tasks 1–7 are implemented, verified and
published to public `FlexGodX/FlyCode` (`7d46629`, `456d444`). Research acceptance
gaps remain open separately from this software delivery.

- Phase 13: candidate-level corrections at 0/.1/.25/.5/1, bounded by
  `.2 * influence`, only inside an allowlist of safe diagnostics. Never promote
  edits, SQL, tests or STOP. Invalid scores, duplicate IDs and observer errors
  preserve the active choice. Failed actual interventions/regressions latch
  influence off for the rest of the episode. Tool effects are not undone.
- Phase 14: `--autonomous` requires matching evidence and uses guarded hard
  ranking; regression or failure of a hard choice different from bias switches
  subsequent cortex contexts and selection to bias. Legacy research hard mode
  remains available separately. Safety/budgets/verified completion remain.
- Admission is recomputed from complete paired raw trajectories. Guard telemetry
  must exist on every autonomous step; relabeling raw hard runs is insufficient.
  Nonzero-level promotion requires actual interventions. Local-model admission
  binds exact instruction text as well as controller/graph/checkpoint/model/
  seed/fixture/budgets. No arbitrary-repository admission is currently available.
- Browser: local prepared full/reduced, trained and distilled controllers;
  observer, discrete influence, admission, seed/budgets and Functional learning
  profiles. Artifact IDs resolve on the server under `FLYCODE_ARTIFACT_ROOT`
  (default `~/.cache/flycode`); escaping symlinks and memory traversal reject
  before workers start. Load active memory before wrapping, save atomically,
  freeze observers and close owned controllers. Old history uses safe defaults.
- Activity explains active/proposed/executed choices, influence, learning and
  fallbacks; raw details are collapsed. Connection status no longer dumps every
  installed model ID into the mobile dialog. Verified at 1280px and 390px with
  no page/dialog horizontal overflow. Real browser demos completed: zero-
  influence full observer + learning in 9 steps, guarded full autonomy in 5.

Measured results: `docs/research-results/2026-10-16-rollout.json`.
36/36 influence-level toy runs completed; zero interventions, so promotion above
.1 rejected. Final separate comparison: baseline 12/12 and observed 12/12 at
9 steps; guarded full 6/6 at 5 steps, no regressions/rollbacks. This is two
teacher toys × three seeds with heuristic candidates, not a general advantage.
Current evidence/admissions: `~/.cache/flycode/experiments/2026-10-16-final-rollout/`.
Admission becomes stale whenever fingerprinted runtime code changes.

The stronger helper revision-2 model study is finished: Functional 0/2, full
1/2 (boundary fix, 8 steps); neither solved observable helper delegation within
24 model calls. Source changed during that exploratory run, so preserve its
outcomes but never use it for admission or a stable-source causal comparison.
The original numeric-only helper study remains labelled separately.

Final verification of this increment: 359 passed, 8 skipped (161.92s); separate
real-connectome suite 5 passed (7.38s); current browser API suite 23 passed
(38.69s). Focused Ruff passed and mypy passed on 19 changed/runtime modules.
The wheel was built in an isolated build environment, its new modules/static
assets inspected and its extracted CLI executed. UI restarted idle on 8790;
existing history remained readable and a new guarded demo completed in 5 steps
with an explicit admission event. CLI telemetry also saves `run.admission.json`.
Browser acceptance was performed through the app browser; the development
Playwright script was updated but not executed in this session.

Still open empirically: independent connectomes; matched compute/state/training
budgets; faster adaptation; broad unseen repositories; paired Qwen/Jev rollout
admission; useful actual diagnostic interventions and evidence for later levels.

## Earlier increment: broader evaluation and scoped admission (2026-10-15)

Active goal: implement `docs/superpowers/plans/2026-10-15-validated-rollout.md`.
At that earlier revision Tasks 1–3 had software implementations; later delivery is documented above.
The overall goal remains active. No general research advantage is claimed.

- Two additional synthetic fixtures: `search_helper_bug` and
  `boundary_regression_bug`. They are separate from teacher toys, not unseen
  real repositories. Helper revision 2 tests observable delegation to the shared
  conversion function; division inline cannot pass. The initial numeric-only
  revision missed that requirement and its model results are labelled as such.
- Benchmark supports actual local Qwen/Jev with explicit endpoint/model/call
  budgets. The local endpoint is resolved when the configuration is read,
  preventing environment changes from silently relabeling an experiment.
- `ControllerSpec.lesion_fraction` runs real tasks with zeroed existing neural
  gains. Parent files and structural topology are retained; report fingerprints
  distinguish the actual damaged graph from its parent.
- `adapt --config docs/examples/adaptation.json --output PATH` retains one
  Functional brain per seed/learning condition while resetting repositories,
  cortex and episodic memory. Records policy hashes, guard state, first/last
  repetition costs and task success; failures stay in denominators.
- Extending coverage found a real baseline bug: FSM referenced a missing
  recovery stage. Added the stage and skipped it after passed verification;
  regression and recovery-exit tests pass. All 24 FSM cases were rerun, not
  just the failed cases. Earlier raw failures remain in their source reports.
- Benchmark now records active/observer identities, completed/source-unchanged
  flags, implementation and trajectory checksums. `shadow-analysis` preserves
  error/missing recommendation coverage and rejects inconsistent traces.
- `validate-rollout` and `research.validation.authorize` recompute admission
  from raw report/metrics/trajectories. Require >=3 seeds, >=2 fixtures, complete
  paired coverage, clean verified outcomes, no extra target tool calls, matching
  scope and >=30 observer recommendations for the first bounded step (.1).
  Code/task/controller/graph/checkpoint/model/budget mismatches reject admission.
  This is compatibility evidence, not authentication or a general safety proof.
- Admission and autonomous execution are different deliverables: the latter and
  nonzero influence are not wired into agent/CLI/UI yet. Legacy research hard
  control retains its previous meaning. Browser changes have not started.

Actual measurements, available in `docs/research-results/2026-10-15-expanded-evaluation.json`:

```text
Expanded heuristic comparison: 192 case results (168 non-FSM plus 24 corrected
  FSM reruns), 3 seeds x 4 synthetic fixtures x 2 controls x 8 controllers.
  Teacher toys: FSM/Functional/full/trained/lesioned/reduced/student each 12/12;
  generic RNN 0/12. Additional synthetic tasks: every heuristic variant 0/12.
  Params/hidden states/training budgets are NOT matched; no superiority claim.
10% real-graph lesion: no task completion change on this limited suite.
Repeated-family runner: 36/36 repairs, both conditions 9 steps; learning
  accepted 160 updates and rolled back 2. Faster adaptation remains unproved.
Initial local-model pilot: Functional search passed numeric tests in 8 steps
  but did not reuse the helper; boundary failed at 24 model requests. Full
  search failed at 24 requests; boundary passed in 11 steps. Seed 0 only.
  Stronger helper contract revision 2 was measured separately; see latest limitations above.
Actual full-graph observer pilot: 36 paired/mode runs, 108 recommendations,
  100% coverage, 0 observer errors, agreement 70.37%, mean observer 10.21 ms.
  Narrow bounded and hard-mode admission checks passed on the two teacher toys;
  actual authorize() calls confirmed matching seed-0 configurations.
Full pytest: 310 passed, 8 skipped in 112.68s.
Real-connectome acceptance: 5 passed in 8.37s. Wheel build succeeded.
Focused Ruff/mypy on research/CLI/FSM and new tests passed.
Pre-existing fixture-module findings: I001, F401, RUF012, F811 remain.
```

Current experiment root: `~/.cache/flycode/experiments/2026-10-15-rollout/`.
`expanded-comparison/` retains pre-fix FSM errors; `fsm-recovery-fixed/` holds
all corrected FSM runs. `adaptation/`, `heldout-local/` (old helper checks),
`heldout-local-v2/` (stronger delegation checks) and `shadow-validation/` contain
raw evidence. Changing implementation invalidates earlier admission files;
regenerate after later rollout changes. Raw experiment repositories/weights are
not published. New commands and examples are documented in RU/EN README.

## Earlier increment: adaptation, evaluation and shadow mode (2026-10-15)

Implementation plan: `docs/superpowers/plans/2026-10-15-adaptation-evaluation.md`.
Portable measurements: `docs/research-results/2026-10-15-adaptation.json`.

- `research/benchmark.py` implements isolated toy fixture/controller/checkpoint
  comparisons, explicit budgets, multiple seeds and controls, durable JSON
  configuration, per-case metrics/errors, controller/cost descriptions and
  actual trajectories. `benchmark --config docs/examples/benchmark.json
  --output PATH` runs Functional/FSM/RNN baselines. Compute is **not matched**.
- Research-only `TournamentCortex` preserves production proposals and adds
  read-only run_tests/git_diff alternatives during EXECUTE. Production cortex
  and brain priors are unchanged. Phase 6 now passes a limited task-level
  acceptance on this explicitly named probe, not on general repositories.
- v2 distillation uses 49 quadratic features and 784 readout parameters; v1
  25-feature/400-parameter checkpoints remain supported. Validation replays
  actual Functional Fly state and outcome feedback on held-out whole episodes.
  Mode arbitration stays unchanged. Default .1 RMSE/.9 agreement gates remain.
- Phase 10 learning uses actual executed transitions, including final STOP.
  Task reset preserves projection, MB credits and RPE estimates; transient
  compass, novelty, habituation and reward state reset. `clear_learning()` is
  the explicit wipe. `--learn` enables learning; `--learning-memory PATH`
  atomically loads/saves validated JSON. Keep the same seed/projection.
- Bounds: learning rates <= .1, update norm <= .5, credit magnitudes <= 2,
  RPE values <= 10, memory/replay bounded to 1024 patterns/128 samples.
  Detached snapshots and replay error/policy-drift checks roll back harmful
  updates; nonfinite limits are rejected before policy mutation. Guard counters
  are per instance; MB lifetime update counts and replay are persisted.
- `lesion` zeros input and existing incident gains for sampled neurons without
  deleting CSR entries; `sensitivity` varies bounded dynamics parameters.
  Both replay recorded trajectories with fresh matched initialization and
  report pooled-state/bias changes. Counterfactual task success is unmeasured.
- `run --shadow-brain full --telemetry-dir PATH` wraps the active controller
  with a zero-influence observer. Optional `--shadow-connectome-path` and
  `--shadow-checkpoint` select its artifact. Same candidates/safety mask;
  action choice and model routing come entirely from the active controller.
  `TrainingStep.shadow_decision` records agreement, expected-risk difference,
  observer latency/errors; no counterfactual success/call estimate is invented.
  Observer runtime failures do not halt active execution. Observer learning is
  disabled. This implements Phase 12's CLI mechanism and initial telemetry.
- Research, online memory and shadow controls are CLI-only. The browser still
  exposes Functional Fly and frozen FlyWire. No UI source changed here.

Actual measurements with FlyWire v783 (fingerprint below):

```text
Constrained teacher: six successful full/tournament bias episodes, seeds 0–2,
  30 executed steps. Separately trained checkpoint for each initialization.
Standard heuristic comparison: frozen=12/12, trained=12/12, mean 5 steps.
Tournament probe: frozen=10/12, trained=12/12; mean steps 6.667 -> 5.
  Difference is seed 0 hard control: 0/2 -> 2/2. Seeds 1/2 show no advantage.
  Same 15-step/60-tool budgets; topology and graph fingerprint unchanged.
Distillation v2: one train / one held-out episode; held-out RMSE .0167944,
  top-tool agreement 1.0, accepted under unchanged default gates.
  Student/original Functional Fly: each 12/12 actual repairs, 9 steps each.
Online repeated families: 36 actual runs, all successful in 9 steps.
  Learning seeds 0/1/2: accepted updates 53/54/53, rollbacks 1/0/1.
  No faster task-level adaptation demonstrated (Phase 10 acceptance open).
10% lesion: 13,926 neurons; active edges 2,191,750; structural edges unchanged
  at 2,700,513; pooled state RMSE .00227947, bias RMSE .163497, agreement .6.
Gain sensitivity: .6/.9/1.2; baseline 1.2 reproduces scores exactly.
Real full-graph shadow vs active-only: both toys succeed in 9 steps; identical
  executed actions, agreement 12/18, observer mean ~7.1 ms per decision.
Persistent-memory CLI across separate runs: both repairs succeed, 17 retained
  accepted updates and 8 replay entries; also works with a shadow observer.
```

Artifacts stay under `~/.cache/flycode/experiments/2026-10-15-adaptation/`.
The accepted student is in `2026-10-15-constrained/student-v2`; the previous
rejected student/report remains in `student`. Full experiment repositories and
trajectories/checkpoints are not published. No matched-compute superiority,
unseen-repository generalization or general LLM improvement is established.

Final verification:

```text
Full pytest: 291 passed, 8 skipped in 76.16s.
FLYCODE_TEST_CONNECTOME=1 pytest tests/e2e/test_real_connectome.py: 5 passed.
  Includes actual training-task improvement with unchanged real topology.
Default skips: five real-graph, two real-graph Docker, one local-model opt-in.
Targeted Ruff: passed except pre-existing RUF046 in mushroom_body.py:118.
Mypy on eight new research/adaptation/student/shadow modules: passed.
Built wheel includes new research/learning/shadow modules; no biological data.
Local UI refreshed at 127.0.0.1:8790; page and history API return HTTP 200.
```

The earlier Docker and local Qwen/Jev acceptances below were not rerun against
this increment; ordinary Docker acceptance is included in the full suite.

## Earlier increment: real FlyWire controller

- `--brain full` uses `FullConnectomeBackend`, not the legacy random projection.
  Install `.[research]`, then `flycode connectome download` and
  `flycode connectome prepare --source ~/.cache/flycode/flywire-783`.
- Official FlyWire v783 source: https://zenodo.org/records/10676866, CC-BY-4.0.
  The two source files and prepared graph stay outside Git under that cache.
  Source MD5 checksums are verified before preprocessing.
- The prepared graph contains 139,255 proofread neurons and 2,700,513 directed
  edges after summing neuropil duplicates and applying a five-synapse threshold.
  Fingerprint: `7adea43f9f823703c8116a6321a78adfdc9aac9c14c80e04bfa6d991f8c6712f`.
  Topology: `00d1a94bd95f39f6fde20a6890cc680e538270c5620eadc53a89837d683b644a`.
- CSR recurrence uses A[target, source], three leaky tanh substeps, modeled
  transmitter signs and normalized incoming weights. These dynamics, seeded
  input/readout and explicit workflow prior are engineering approximations;
  they are not measured software-solving behavior of a biological brain.
- All activity remains inside the backend; JSON events expose 64 pooled values
  plus provenance and counts. The browser settings offer Functional Fly/FlyWire.
  Missing or corrupt data fails explicitly without a synthetic fallback.

Verified on 2026-10-15:

```text
Full pytest: 246 passed, 7 skipped in 68.77s.
Real graph toy repairs: 4 passed (two fixtures × bias/hard).
Real graph Docker/PostgreSQL repairs: 2 passed (bias/hard), 46.03s.
Actual browser FlyWire + local Qwen/Jev: verified success, 8 steps, 86.40s;
  Qwen=5 and Jev=5 actual requests, no model errors.
20 warmed controller steps: mean 6.31 ms, measured array storage 30.17 MiB.
Changed Python files: targeted Ruff passed; new graph modules: mypy passed.
```

The seven default skips are four explicit real-graph tests, two real-graph
Docker cases and one local-model test. The corresponding real runs above were
executed separately. No comparative advantage has been established.

## Earlier increment: offline training and smaller circuits

- `training/{trajectories,checkpoint,constrained}.py`: executed JSONL episodes,
  reset boundaries, dataset fingerprint, bounded gain/leak search and ridge tool
  readout. `train` is operational; `run --brain full --checkpoint PATH` loads
  the result. Adjacency and its fingerprint stay unchanged. Checkpoints bind
  parameter checksums, graph fingerprint and output vocabulary; nonfinite,
  overflowing and mismatched arrays are rejected. Reset retains fitted weights.
- `connectome reduce --source PATH --neurons 4096 --output PATH` extracts a
  connected induced circuit with existing weights and original neuron IDs.
  Input mappings are sliced from the parent, not regenerated; readout/dynamics
  are inherited. `--checkpoint` optionally selects a trained parent.
  `run --brain reduced --connectome-path PATH` loads the bundled checkpoint.
- `distill --trajectories PATH --teacher-checkpoint PATH --output PATH` fits
  only tool biases in a 400-parameter Functional Fly readout. Mode arbitration
  is retained. Whole episodes are held out; default gates are RMSE <= .1 and
  top-tool agreement >= .9. Failed candidates are saved but cannot be enabled.
  Accepted candidates load with `run --brain functional --checkpoint PATH`.
- Fixed Functional Fly's unconditional MB update when learning is disabled.
  This is necessary for frozen comparisons; it does not complete Phase 10.
- The new research workflows are CLI-only. The browser still offers Functional
  Fly and the frozen full graph. Model weights and experiment data stay outside
  Git. Benchmark/lesion/sensitivity were still stubs in this earlier increment.

Measured with the actual v783 artifact on 2026-10-15:

```text
Teacher data: two successful Functional Fly toy episodes, 18 executed steps.
Full graph: tool imitation accuracy .4444 -> .7778, MSE .07805 -> .01311.
Selected dynamics: gain=1.2, leak=.5; topology/fingerprint unchanged.
Actual task comparison: frozen and trained each solve 4/4 (two toys x two
  control modes), five steps each. No task-completion improvement.
Reduced graph: 4,096 neurons, 12,031 edges; 4/4 repairs, five steps each.
  Mean of 30 warmed steps: full 7.106 ms / reduced .117 ms.
  Array storage: full 30.172 MiB / reduced .379 MiB.
  Fingerprint: 162b8a20ba930cd15d9f02ab2355b48935431e86b69a7e9cd92beb8c86cd8ab5.
Distillation: one training episode, one held out; validation RMSE .02550,
  top-tool agreement .8889. Default .9 agreement gate failed as expected.
```

Artifacts: `~/.cache/flycode/experiments/2026-10-15-constrained/` contains
trajectories, checkpoint/training.json, student/distillation.json and
task-comparison.json. `2026-10-15-reduced/validation.json` contains the smaller
graph's actual runs and timing. Toy coverage is narrow; this is not evidence
of generalization, matched-compute superiority or biological cognition.

Final verification for this increment:

```text
Full pytest: 262 passed, 7 skipped in 77.28s.
Targeted Ruff: passed; mypy on eight new research/reduction modules: passed.
Built wheel includes training, reduced backend and UI; no biological data or
  checkpoint weights are bundled.
Actual CLI: trained full/import and reduced/arithmetic repairs both succeed
  with verification, five steps each.
Local UI restarted on 127.0.0.1:8790; completed history replay verified.
```

## Earlier increment: local browser workspace

Implementation plan: `docs/superpowers/plans/2026-10-15-agent-interface.md`.

- Start `flycode ui --workspace . --port 8790`, then open the loopback URL.
  FastAPI serves packaged HTML/CSS/JavaScript; no Node runtime/build is needed.
- Russian task workspace: history/search, task composer, model settings,
  live activity timeline, candidate rationale/scores, expandable tool results,
  applied patch view, raw event journal and responsive inspector.
- Controller/model/tool events are emitted before and after actual work.
  Diagram highlights represent real component calls; confidence/risk come from
  NeuralState. Action rationales are not hidden model chain of thought.
- One background worker executes the existing agent. The web event loop stays
  responsive during blocking model/tool requests. Stop prevents new actions;
  an in-flight call is allowed to finish, including skipping Jev after a stop
  during Qwen inference. Already applied edits are preserved.
- Project paths are scoped to `--workspace`; fixtures always use fresh per-run
  directories. Foreign origins/hosts and mutations without a session token
  are rejected. Dynamic output uses text nodes and a strict local CSP.
- Persistent records/events default to `~/.local/share/flycode/ui`; override
  with `--data-dir`. SSE supports cursor replay and reconnect. On restart,
  unfinished records become interrupted. The 50 most recently saved records
  are loaded; on-disk history is retained. Browser output strings are clipped
  at 16,000 characters; regular execution telemetry is retained separately.
- `src/flycode/web/{app,runs}.py` owns the web service; `static/` owns the UI.
  Optional callbacks in `agent.py` and `llm/lmstudio.py` preserve CLI callers.

Verified on 2026-10-15:

```text
Browser acceptance: demo repair, patch view, replay on reload, history,
  literal untrusted task text, no page errors, 390px layout and inspector.
Actual browser task with local Qwen/Jev: verified success, 9 steps, 113.00s;
  Qwen=6 and Jev=6 requests, no model errors.
Full pytest: 226 passed, 1 skipped in 66.58s.
  (The opt-in LM Studio test is skipped; the actual browser model run above passed.)
Targeted Ruff and web module mypy: passed.
Built wheel includes the server and all four static assets.
```

Browser acceptance is development-only: install Playwright outside the project
(or in your development environment) and ensure Google Chrome is available.
Start an isolated UI with `--data-dir /tmp/flycode-ui-test`, then run:

```bash
FLYCODE_PLAYWRIGHT=/path/to/node_modules/playwright \
  node tests/browser/agent-ui.cjs
```

It defaults to `http://127.0.0.1:8790`; override with `FLYCODE_UI_URL`.
Screenshots default to `/tmp/flycode-ui-screenshots`.

## Earlier increment: local Qwen + Jev

Implementation plan: `docs/superpowers/plans/2026-10-15-lmstudio-dual-model.md`.

- `--llm lmstudio` selects `qwen/qwen3.5-9b` for candidate generation and
  `jev-style-qwen3.5-2b-decision` for native single-token option scoring.
- Both exact IDs were verified through the local LM Studio API and both models
  were loaded. No weights were downloaded, changed or trained.
- Routing consumes CortexContext.neural_state. Blocked states skip inference;
  verified completion and simple non-Compose VERIFY steps are local; confidence
  >= 0.8 and hard control skip Jev. Safe candidate scoring is advisory, and the
  existing safety mask and brain tournament retain final control.
- Qwen uses structured JSON, a 1536-token response cap and disabled reasoning
  output. Jev uses the installed v1 protocol with one token and top-20 logprobs.
  Missing/nonfinite decision distributions produce an explicit diagnostic and
  preserve Qwen proposals. Qwen errors stop without an invented repair.
- Requests have no automatic retries and a per-run shared call budget. Failed
  requests count. The local default timeout is 120 seconds: on this host a
  366-token edit proposal took about 58 seconds at roughly 7 tokens/second.
- Configure `--base-url`, `--model`, `--decision-model`, `--request-timeout`,
  `--max-model-calls`. Optional authentication is `LMSTUDIO_API_KEY`; the endpoint
  can be set with `LMSTUDIO_BASE_URL`. There is no cloud fallback or inherited
  proxy routing for local inference.
- Per-model counts/errors are recorded in RunMetrics, and each executed step
  includes a model_routing trace. Consolidation makes no additional request.
- The SDK floor is now `openai>=1.61` for the transport and request interfaces.

Verified on 2026-10-15:

```text
python -m pytest -q -> 206 passed, 1 skipped in 89.52s
  (the single skip is the explicit opt-in local-model acceptance)
FLYCODE_TEST_LMSTUDIO=1 python -m pytest tests/e2e/test_lmstudio.py -q
  -> 1 passed in 148.09s
Real CLI toy repair -> success=True, 6 steps; Qwen=3 calls, Jev=3 calls;
  no model errors; post-edit tests and STOP required no inference
Targeted Ruff on new routing/clients/CLI/telemetry/tests -> all checks passed
Repository-wide Ruff -> 57 existing findings
mypy src/flycode --ignore-missing-imports -> 20 errors in the same 8 files
```

Local inference and the wiring are verified. No improvement over the baseline
has been established. The separate real-graph extension above satisfies Phase 5.

## Phase status

| Phase | Plan | State |
|---|---|---|
| 0 SoftwareGym | §74 | Implemented and tested |
| 1 Autonomous agent | §75 | Toy repositories repaired end to end |
| 2 Baselines | §76 | Mock, FSM, random reservoir, generic RNN available |
| 3 Functional Fly | §77 | Implemented and tested |
| 4 Environment autonomy | §78 | Real autonomous Docker/Postgres acceptance passes in bias and hard control |
| 5 Real connectome | §79 | Accepted: real FlyWire graph passes toy and Docker tasks in both control modes |
| 6 Constrained training | §80 | Limited acceptance: tournament probe 10/12 frozen -> 12/12 trained across 3 seeds; difference at seed 0 |
| 7 Reduced connectome | §81 | Limited acceptance passed: same toy repairs with much lower compute |
| 8 Distillation | §82 | Limited acceptance: v2 real-controller replay RMSE .01679, agreement 1.0 on one held-out episode |
| 9 Candidate Tournament | §83 | Implemented: existing safety-masked hard/bias tournament tests pass |
| 10 Online learning | §84 | Bounded persistent updates, replay/rollback tested; faster task adaptation unproven |
| 11 Causal science | §85 | Lesion/sensitivity replay and multi-seed toy benchmarks; cross-connectome and matched compute open |
| 12 Shadow | §86 | Zero-influence CLI observer and initial telemetry; broader collection open |
| 13 Bounded influence | §87 | Not implemented; existing bias flag does not constitute validated rollout |
| 14 Autonomous Fly | §88 | Not implemented; gated by benchmark validation |

## Run and verify

Use Python 3.12 and install the dependencies declared in `pyproject.toml`.
The local `.venv` now includes the dependencies needed by the Phase 4 checks,
plus Ruff and mypy. Environment heuristics use `PyYAML.safe_load`.

```bash
source .venv/bin/activate
python -m pytest -q
python -m pytest tests/e2e/test_environment_autonomy.py -q
PYTHONPATH=src python -m flycode.cli run \
  --repo /tmp/flycode-environment --fixture fastapi_postgres_app \
  --task "Fix this FastAPI application and get it fully running" \
  --brain functional --control bias --max-steps 60 --max-tool-calls 60
```

The demo requires Docker/Compose and a free host port 8000. Its Postgres is
internal to Compose, so host port 5432 need not be free. Fixture destinations
are recreated. The CLI leaves the repaired service running; stop it with
`docker compose --project-directory /tmp/flycode-environment down`.
The acceptance tests choose a free HTTP port and clean up their own containers.
On this machine Colima is running and the installed Compose plugin is linked
under `~/.docker/cli-plugins/`; other projects' containers were left running.

## Phase 4 implementation

- The offline cortex reads repository files and derives narrow repairs for
  documented ports, migration paths, environment references, routes and the
  demonstrated missing SQLAlchemy import. It never reads fixture defect metadata.
  General repairs still require `--llm openai` and an API key.
- Tests and Alembic accept a Compose `service` argument, preserving the runtime
  dependencies and environment defined by the target application.
- Compose `up` rebuilds and waits for health; `ps` parses actual container states.
  Command success alone does not imply a running stack. HTTP requires a 2xx
  response and failures include the URL and status.
- Completion requires fresh passing tests and every declared infrastructure
  check: running stack, PostgreSQL, migration at head, health and API URLs.
  Edits and stack lifecycle actions invalidate verification. Premature STOP
  is rejected and cannot report verified completion.
- Successful diagnostic output reaches working memory. Edited file snapshots
  are discarded so the cortex does not repeatedly apply a stale repair.
- Infrastructure errors clear on recovery; HTTP errors are tracked per URL.
  Ordinary tool-usage failures do not poison environment health.
- Environment-variable reference fixes preserve credential values and require
  verification; actual credential edits and replacement-based bypasses remain
  blocked by the safety mask.
- The fixture retains its intended defects. Unintended invalid logging setup
  was removed, and the DB no longer publishes a host port.

## Acceptance evidence

The real `run_agent` acceptance passed for both `BIAS_CONTROL` and
`HARD_CONTROL`. Each run repairs the initially broken fixture, checks Postgres,
executes the migration, passes both application tests, checks `/health` and
`/users`, and reaches STOP. A separate SQL query verifies Alembic revision `001`.
The toy CLI smoke test also passes (`success=True`, 9 steps).

Phase 4 baseline verification (2026-10-15):

```text
python -m pytest -q -> 166 passed in 55.01s; no skips
Ruff on the new environment module, environment tools, readiness checks,
Phase 4 acceptance and associated new tests -> all checks passed
Ruff across src/tests -> 58 remaining findings
mypy src/flycode --ignore-missing-imports -> 20 errors in 8 files
```

Repository-wide static checks are not clean. Remaining findings include unused
imports, duplicate definitions, import sorting, existing model/category typing,
brain state typing and the hard/bias tournament result type. They were not
silenced or folded into an unrelated cleanup. Raw diagnostics from this session
are in `/tmp/flycode-ruff.log` and `/tmp/flycode-mypy.log`.


## Remaining work after this increment

- Phase 6/8: more varied held-out trajectories, unseen repositories and actual
  local-LLM comparisons; current acceptance covers narrow toy experiments.
- Phase 10: demonstrate faster end-to-end adaptation; current repeated-family
  result is a null finding. Existing unit reversal/prediction tests are not
  substitutes for task-level improvement.
- Phase 11: cross-connectome datasets, matched state/parameter/training budgets,
  lesions with actual counterfactual task executions and delayed-reward tests.
- Phase 12: broader recorded shadow episodes and analysis; counterfactual
  success/tool-call deltas stay unmeasured.
- Phase 13: explicit low-risk rollout levels, bounded influence and rollback
  validation. Phase 14 remains gated by benchmark evidence.
- UI selection for trained/reduced/shadow controllers and learned memory.
- Stressors declared on TaskSpec are not wired into the loop: partial
  observations, delayed rewards, context reversal and noisy feedback.
- Pre-existing duplicate toy fixture/backend test definitions and repository-wide
  Ruff/mypy findings remain; no unrelated cleanup was performed.
