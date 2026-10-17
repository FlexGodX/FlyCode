# Shared source admission — 2026-10-17

Status: validated bounded engineering delivery; aggregate Tasks5–8 remain open.
Source fingerprint:
`e09ab693b05a7ada96f6675118a3e8e25ca76d7f5f2828f24be6340ce52b61e9`.
Parent publication: `3872024145c853054b4e88f20829ff8ece79ce34`.
Final evidence: `/private/tmp/flycode-source-serial-web-validation-9ippln27`.
Archive destination, sealed after the checked push:
`~/.cache/flycode/validation/flycode-shared-source-9ippln27`.

## Implemented scope

Schema15 adds a typed source-claim ledger to the existing SQLite Store. Legacy
chats and canonical integration exclude concurrent ownership of equal, nested
and symlink-aliased folders across separate managers/connections. Independent
folders may proceed. All executors must use the same `FLYCODE_STATE_DATABASE`.
Claims bind physical root/ancestor identities, exact coordinator objects,
process receipts and the actual legacy run or integration owner.

Filesystem inspection and process execution stay outside SQL transactions.
Atomic admission checks overlap and persists ownership; settlement independently
locks the coordinator and every exact stopped-process receipt, validates linked
outcomes and uses revision CAS. Neither a lease timeout nor an `interrupted`
label proves stopped ownership. Unknown model/external effects, missing receipts
and unfinished pre-upgrade history retain a durable blocker.

Legacy project-code tools and Git observations use `tool-custody-v1`, immutable
hashed requests, bounded output and a pre-spawn callback that persists the owned
process reference. Stop cancels owned groups, including descendants. Actual run
IDs own model transport rows, so late usage remains charged after Stop. Startup
checks all unfinished history, independently of the UI's50-chat window. Demo
materialization claims its private parent because it legitimately recreates the
repository inode. History remains separate from canonical tasks/verdicts.

Integration acquires a source claim before recapturing its baseline and guards
every apply/rollback move. Native source/recovery roots are identity-checked and
pinned by descriptors; parent traversal and creation stay relative to those
roots. Replacing a pathname cannot redirect the move into a replacement folder.
After descriptor pinning, concurrent user relocation may still permit an effect
on that original physical directory. This is not hostile-filesystem isolation.
Snapshot Git metadata disables configured fsmonitor and filters.

## Retained failures

The first full972-ID run retained961 pass/3 failures/8 skips. Always forwarding
the optional `on_prepare` keyword broke existing positional custody wrappers.
Conditional forwarding fixed it; original3 RED and related13 GREEN are retained.

Corrected source438e passed964/8 skips,167 extracted-wheel tests, CLI/graph and
real pipeline/Stop. It was rejected for publication after review reproduced
wrong-root writes between the final guard and native rename/mkdir. Direct,
nested, parent-creation and post-pin traversal REDs led to the descriptor-bound
correction. Main source/atomic/rollback checks then passed38 cases47.33s; Ruff
and mypy53 passed. Denied local HTTP setup (30 pass/8 setup errors) is recorded
separately from feature REDs.

The first978-ID run on the final source retained969 pass/1 failure/8 skips. The
unchanged learning UI observer expired at15s while competing with parallel CPU
groups; its fixture cancelled a pending step8 test. No model was called. Audit
confirmed two released source claims and29 stopped receipts. The unchanged
exact case alone passed, with its two runs8.21/8.24s. The final preregistered full
run executes three non-web groups in parallel and then all53 web tests serially.
All original assertions/deadlines remain. This resource schedule is disclosed;
neither prior failure nor the earlier passing source is discarded.

## Final verification

Every registered978 ID ran exactly once:970 passed/8 expected opt-in skips in
642.303s. Four process return codes were0; stage ordering, Compose affinity and
wall/monotonic time are independently checked. The five opt-in FlyWire cases
were separately enabled and passed in5.365s. Full graph/environment and dual-model
opt-in skips are not relabelled as passed. Changed-file Ruff and scoped mypy53
passed.

The extracted wheel passed191 exact cases in332.160s, including the earlier
learning observer case. All142 Python files match frozen source; README metadata
and both README copies match measured bytes. Extracted CLI lists10 brains.
Wheel SHA256:
`4401355f38f7c7f6eede707e25fd5b4b0dec8e60784297dc7a8bf9b13463dc38`.

The preregistered English Qwen pipeline passed in66.532s: one stopped writer,
reviewer and integrator, four succeeded tasks, two stage receipts and a fresh
current integrated verdict. It used3 model calls/34 tools, zero manual claims
and zero owner commands after approval. User binary/README bytes were preserved.
Failed checks and all charged tool operations remain in the journal; this toy
engineering smoke does not establish an efficacy improvement.

An explicit bounded primary probe passed (12.403s process time), without loading
or fallback. The real legacy Stop/source-exclusion smoke passed in21.495s. Stop
returned in0.002014s; a competing manager remained blocked until the actual late
primary request completed, retaining1,333 tokens. User source bytes stayed
unchanged, no canonical task records were created, and subsequent source
admission succeeded only after confirmed settlement.

Independent audit verified all five actual model outcomes, no unknown usage or
reservations, three released claims and27 exact stopped-process receipts. It
also checked current source acceptance, command/receipt actors, original budgets,
all227 measurement-file hashes, protocol/registration hashes and unchanged
environment. Raw reports retain every earlier full outcome and actual failure.

## Remaining work

Unknown Docker/database/remote effects require trusted reconciliation; there is
no forced unlock or hostile sandbox claim. General multi-writer routes,
automatic retry/replan, child wait, broader manager recovery and shared
UI/CLI/API are still open. Canonical project registry/API development remains
isolated and is not part of this publication. Biological topology, checkpoints
and defaults are unchanged; no neural orchestration or scientific admission is
granted. The separate296-English-run feedback benchmark and rejected trained
checkpoints remain in `2026-10-16-feedback-benchmark.md`.
