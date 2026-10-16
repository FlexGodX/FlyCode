# Coordinator custody and interrupted integration — 2026-10-16

This Task6 increment follows `37b7f53`. Final frozen implementation SHA256:
`9994d779d3ae8922afc0c00326f4ffaed413e4fddd40717531e010ad0d8a1585`.
The implementation and measured environment match before and after execution.
The overall specification-delivery goal remains open.

## Verified behavior

Registered host pytest uses an independent supervisor, inherited exclusive lock,
private admission/stop channel and durable versioned process receipt. The canonical
ToolCall binds the expected process reference before GO. Coordinator death closes
the channel; the supervisor stops and reaps the owned group before releasing
custody. An actual sleeping child, including one that ignores SIGTERM, cannot
outlive confirmed cleanup. A saved PID never authorizes recovery signals.

`Integration.reconcile` excludes a live coordinator and holds every process lock
through rollback and final state changes. Missing/replaced lock objects, missing
receipts and a substituted receipt from another real check cannot establish stop.
The runner retains its expected reference independently of report bytes; raw JSON
never restores authority. Passed cases survive an unconfirmed cleanup as evidence,
but cannot produce a passing verdict or release uncertain resources.

A started check cancelled by its owner, killed at the output bound, or terminated
with surviving descendants retains unknown effects. Even a passing pytest leader
with exit code 0 does not complete the invocation when its remaining group was forcibly
stopped. Natural malformed reports without process interruption remain known
non-passing outcomes. Pre-admission fencing cannot execute subject code.

Real applied-verification coordinator crashes retain source ownership and actual
spending until a trusted host independently reconciles the fixture's external
effect. Group absence alone is insufficient. After that explicit reconciliation,
object-preserving rollback restores actual late writes through old descriptors.
Owner replacement of an installed file yields rollback_conflict, preserving both
versions and the assignment; a generic stop acknowledgment cannot bypass it.
Completed rollback replay preserves subsequent owner edits. An atomic initial
reread prevents recovery from resurrecting an assignment another host already
stopped. No inference or filesystem rollback runs inside SQLite transactions.

## Final validation

| Check | Measured outcome |
|---|---|
| Full pytest collection, three isolated shards | 751 passed, 8 skipped; 517.36s wall |
| Extracted wheel: delivery/application/runtime/custody/acceptance | 139 passed; 444.12s |
| Opt-in actual FlyWire graph | 5 passed; 6.33s |
| Relevant Ruff | Passed |
| Relevant mypy | Passed, 33 source files |
| Packaged source / README | All 131 Python files and README match |
| Extracted CLI | All ten registered controllers listed |

Final wheel SHA256:
`e7c1d3a4909aaf358a3b134675e74f7e865a0f1614956acf8c579d11d641bcf4`.

The final loaded-Qwen primary probe passed in 12.64s.
A fresh English-prompt repair, independent review, integration and current source
acceptance passed in 55.19s using 3 model calls and 26 tools.
All 20 registered host process receipts confirm natural completion and group stop.
Owner tests and dirty binary bytes survive. Final workflow is succeeded, with no
outstanding reservation or uncertainty. No model loading, scorer or fallback.
This small engineering smoke does not establish scientific or orchestration
admission.

## Retained failures and limits

The initial full run on implementation
`668a6d3c12ff6a74ac37c63b55bfff99317f87ad15009dd9f9414cf9e2e2867e`
returned 747 passed, 8 skipped, 1 failed. Its parent-only _terminate fault injection
no longer reached the separate supervisor. The replacement tests use real receipt
loss, actual lock replacement and another completed check's receipt. All three
reproduced the raw-receipt trust defect before correction. A separate real child
reproduced false complete after its passing leader exited; that RED is retained.
Earlier output-limit and stop-acknowledgment race REDs also drove corrections.
The next sequential full run on the final implementation returned 750 passed,
8 skipped, 1 failed in 1068.62s: an old background-child test still expected pass
after forced descendant termination. Its expectation now preserves the passing
case but requires an inconclusive verdict, stopped group, interruption reason
and no late write. Only this test changed; implementation/wheel/model evidence
remain the same version. A fresh full collection then ran in three isolated
shards; exact case IDs and coverage match the prior full collection with no
duplicates or omitted tests. The extracted package also ran the 30-case updated
verification module, in addition to the 109-case delivery/custody/acceptance set.
The initial 90-pass extracted wheel, initial live smoke and initial graph report
remain historical measurements, not final-version validation.

Artifacts, scripts, SQLite ledgers, exact source snapshots, receipts, raw evidence,
negative reports and final reports live under:
`~/.cache/flycode/validation/flycode-coordinator-custody-iob0j_5q`.
The final measurement set is its `final/` directory. The checksum manifest covers
retained regular files except generated Python/cache files. External fixture effects
were reconciled by the test harness using its own known processes and counter;
there is no generic automatic proof for arbitrary external effects.

All ten saved feedback-benchmark checkpoints also pass the existing numeric
loader on this implementation: checksums, shapes and graph/output vocabulary
match. This is artifact compatibility, not a rerun of the 296-case benchmark
or a new deployment admission. Its measured training/transfer results remain
in `docs/validation/2026-10-16-feedback-benchmark.md`.

These actual process/filesystem checks ran on macOS. Unsupported custody stays
blocked. This is a trusted-local protocol, not a hostile-code sandbox or a
machine-wide reboot recovery claim. Standalone host-review recovery, multi-writer
conflict/resolution, the remaining deadline/rerun matrix and canonical CLI/API/UI
wiring remain open, alongside Task5 dispatcher/shared admission and Tasks7–8.
