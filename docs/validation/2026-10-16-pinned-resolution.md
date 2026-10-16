# Pinned conflict resolution — 2026-10-16

Frozen implementation SHA256:
`81e9f038476ef78587e3165e1f8b842c0a9cc936c9e3d88a6cf93a2cddba2a83`.
This is a Task6 increment. Task6 and the full specification-delivery goal remain
open. It extends the [retained-conflict increment](2026-10-16-multi-writer-conflicts.md).

## Execution and completion

An owner revises and enqueues the exact planned resolution task, supplying its
criteria, permissions and budget. Before launching a worker, the host validates
both immutable inputs, their common source base, original contracts and exact
independent reviews. Admission records the resolution attempt and releases only
already stopped original authors. Their attempts remain interrupted; the old
integrator attempt remains failed. Original tasks remain required.

The existing `read_file` gains an explicit server artifact ID through a host RPC.
The host validates the whole approved input set, file identity, workflow/project,
read scopes and path before admission. One canonical tool call is charged before
reading. Invalid selectors, paths, pins and replay cannot silently fall back to
the workspace. Successful reads carry artifact, patch and file hashes into working
memory; workspace edits preserve this provenance. UTF-8 files up to 3,500 bytes are
supported. Binary, invalid UTF-8 and larger inputs fail explicitly.

The real system prompt describes the host extension and distinguishes immutable
versions from the current workspace text used by `edit_file.old_text`. Required
input metadata cannot disappear to fit context. Optional old facts, lessons and
logs can be compacted with an audit event; pinned observations, workspace reads
and failure diagnostics stay. If required data still cannot fit, no HTTP request
is made. Input integrity is checked again after HTTP before accepting candidates.

A fresh resolution artifact requires independent review. The integrator derives
its conflict lineage from server records and checks every original author,
stopped integrator, resolver and current integrator contract on both the merged
candidate and installed source. It applies only the new patch. Completion updates
tasks from fresh evidence without rewriting historical failed/interrupted attempts.
The tested two-author resolution requires five check sets in each phase: forty
registered checks and one apply operation, plus independent reviews and worker
tools. Dirty/untracked binary bytes survive.

## Compatible root revalidation

An explicit previous-result binding can revalidate an unchanged installed
resolution after root reopen. Its immutable inputs, retained source objects,
contracts and owner oracle hashes must still match. Each stored root generation
advances exactly once; other inherited semantic revisions stay fixed. The old
result remains invalidated.

A fresh resolver review and all five merged/source check sets are required again.
Original immutable-patch review tasks can be restored only from their exact
blocked prior-result bindings, with current evidence. Restoration happens in the
successful final transaction after metadata/fence/permission checks. Old attempts
stay unchanged. The case consumes another forty-four tools, retaining the original
deadline and spending. New review tasks fit under the resolver without bypassing
the root's eight-child history limit. Changed original evidence blocks this path
before spending or creating another integration.

## Frozen-source checks

| Check | Measured outcome |
|---|---|
| Complete default collection, three isolated groups | 786 passed, 8 skipped; 1,217.92s wall |
| Exact collected/result case IDs | All 794 covered once; no missing, extra or duplicate cases |
| Relevant Ruff | Passed |
| Mypy, domain/orchestration/verification and changed LLM contracts | Passed, 41 source files |
| Built wheel | All 131 Python files and README metadata match the checkout |
| Extracted-wheel scenarios added by this increment | 23 passed; 367.62s pytest / 368.73s launch wall |
| Actual FlyWire data | 5 passed; 10.08s pytest / 10.40s launch wall |
| Extracted CLI | Lists all ten controllers |
| Explicit primary protocol probe | First attempt times out at30.14s; separate retry passes at24.40s |
| Real English-prompt Qwen conflict resolution | Failed; 341.82s, 10 model calls / 22 tools |

Wheel SHA256:
`c3d3730cc184c767929e32d70eaf4ca84c39bd6bd9f5bcc10d1237a060f779e0`.
The failed first post-check driver and probe are retained separately. Only the
probe is retried; successful package/graph checks are not rerun or relabelled.

The full run uses an isolated configured `FLYCODE_ARTIFACT_ROOT`; explicit
catalogue fixtures still inspect actual test artifacts. Default external cache
size is not a controlled test input. Its source identity and normalized package
environment match the frozen starting identity. The full-run receipt records a
wall/monotonic difference below 0.005s.

## Final real-model outcome

Both real writers produced the assigned values1 and2 and received independent
approvals. Conflict registration preserved both inputs and left the source at0.
The resolver completed two host-routed reads, but both selected the same artifact
ID. Its next edit failed; the worker subsequently exited with code1 and no
published artifact. The harness reports `not_found: artifact`, not a passing
resolution. The canonical record does not contain a conclusive worker traceback,
so the cause of that process exit remains unproven.

The last actual HTTP request ran from1792165511.794538 to1792165553.698348 and
completed with usage after the worker assignment had been revoked. Its late
response was retained for audit and not returned to the worker. All ten model
calls remain charged; no reservation or unknown transport remains. There was no
automatic model loading, fallback, refund or source edit. A subsequent continuation
observed the original30-minute workflow deadline already elapsed; no same-workflow
retry was launched and no new workflow was created to reset its budget.

The final verifier distinguishes passing automated/package/graph checks from this
failed model acceptance (`checks_passed=true`, `real_resolution_passed=false`).
The successful deterministic HTTP/subprocess cases establish the implemented
route and integration contracts; they do not establish reliable Qwen conflict
resolution. Preserve this distinction before further prompt/controller changes.

## Failures retained as feedback

The original full-path test fails against the retained published baseline because
the worker cannot read pinned inputs. The lifecycle test initially fails with
`incomplete_workflow` after reopen. Both failures and subsequent successful
targeted checks remain in the archives.

Actual Qwen feedback exposed two context defects. The first resolver repeatedly
read the workspace and then hit the context budget. Adding the system tool
contract and optional history compaction exposed a second defect: compaction
could discard a workspace read and an edit failure, causing repeated invalid
edits. Separate REDs demonstrate the missing tool contract and lost workspace
fact. The final compaction test checks the actual HTTP request, required facts,
unchanged input objects and pre-HTTP rejection when required data is too large.

Earlier real-model failures remain intact. One explicit primary probe timed out;
a later bounded probe succeeded. One harness stopped its resolver after 240s
while a request was in flight. The resource stayed uncertain and retained its
reservation. Trusted local reconciliation used process-group absence, provider
cancellation logs and native idle/empty-queue state. Truncated provider logs did
not establish exact token usage, so usage stayed unknown and spent calls were
not refunded. A same-workflow retry preserved the deadline and budget history but
failed to read either input. These are failures, not successful completions or
automatic recovery claims.

The first concurrent test measurements include supervisor startup timeouts,
message/UI observation timeouts and an expired idle integrator in the capacity
fixture. That fixture now maintains the claimed integrator lease while awaiting
other reviews. An initial sequential full run reports 779 passed, 8 skipped,
3 failed and 4 errors. macOS power logs confirm a 951s sleep interval during a
check; the canonical late result is correctly fenced and cannot pass. All seven
affected cases pass in the final complete same-source run. Earlier reports are
retained rather than merged into a passing verdict.

## Remaining scope

This trusted-local macOS path does not provide a hostile-code sandbox, general
large/binary artifact editing, automatic external-effect recovery or arbitrary
changed-contract/new-writer reopening. Standalone-review recovery, the remaining
stop/deadline/rerun matrix, shared legacy endpoint admission and the dispatcher
remain open. A dispatcher also needs atomic candidate-specific reservations,
owner-approved durable execution bindings and child-wait/checkpoint handling.

Canonical CLI/API/UI wiring, bounded discovery of a growing real artifact cache,
neural orchestration shadow/replay, its separate admission and the required
experimental/intervention matrix remain open. The separate historical
[296-run feedback benchmark](2026-10-16-feedback-benchmark.md) is unchanged;
trained candidates remain rejected. No scientific or orchestration promotion is
inferred from these engineering checks.

Final-source evidence and executable harnesses:
`~/.cache/flycode/validation/flycode-resolution-verified-3j8qzx7g`.
Prior failures and source snapshots remain under the sibling archives
`flycode-resolution-x6_fxh_7` and `flycode-resolution-final-3dqlarew`.
