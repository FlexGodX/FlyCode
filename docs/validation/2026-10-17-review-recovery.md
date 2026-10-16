# Standalone review recovery — 2026-10-17

## Change and boundary

A registered standalone reviewer now records its immutable artifact, patch hash,
oracle, fence and coordinator lock identity before checks start. Schema11 adds
`ReviewExecution` without replacing the existing queue or model ownership ledger.
The final review and completed execution commit together. Runtime-only
`Reviews.reconcile` excludes a live coordinator, checks custody of every local
check process and retains unknown external effects until trusted acknowledgment.
An interrupted execution cannot be replayed and recovery never creates approval.
Legacy unfinished review lanes without custody proof remain blocked.

This is trusted-local crash recovery, not a hostile-code sandbox or a complete
dispatcher. Task5/6 aggregate acceptance and Task7/8 remain open.

## Frozen verification

Implementation fingerprint:
`9242001f5d007fd1fbab8f5a2ffd2a106dc8864e705b08f3085680ce963dc3bb`.
Archive: `~/.cache/flycode/validation/flycode-review-verified-wsavojze`.
Python3.12.13, macOS27 arm64; source/environment identities match before/after.

REDs reproduced premature release of a live coordinator, missing crash recovery,
a two-connection settlement race and release of an unfinished schema10 legacy
review. Seven initial targeted cases,57 broader queue/migration/review cases and
two real crash/custody-lock cases pass. Actual started checks retain unknown
effects until acknowledgment; acknowledging spend cannot bypass held custody.

| Verification | Measured result |
| --- | --- |
| Full default collection, all835 exact IDs once |827 passed,8 skipped;664.197s |
| Extracted wheel selected checks |58 passed;115.62s pytest |
| Real FlyWire graph checks |5 passed;10.00s pytest |
| Relevant Ruff / mypy | pass /45 source files pass |
| Extracted CLI |10 controllers listed |
| Wheel Python files / README metadata |132 exact files / match |
| Explicit Qwen primary probe | first try passed;26.254s |
| English canonical delivery smoke | passed;95.860s,3 model calls,26 tools |

Wheel SHA256:
`3e36b383155a686030d6c6206feebdab12426a45d580bd5fd7148f7ab27a9dfd`.
The three full-suite processes keep the entire fixed-Compose-project module in
one process; collection/result Counters prove no missing or duplicated IDs.

The first full run is retained separately in
`~/.cache/flycode/validation/flycode-review-custody-04nf6627`:
825 passed,8 skipped,2 failures in582.553s. Both failures were functional
FastAPI/Postgres parametrizations split between processes. Their Compose configs
share global project `phase4` despite distinct ports. Both pass when serialized
on unchanged source (2 passed,2 default opt-in skips;125.00s). Original Docker
stderr was not retained by those tests and a filtered historical daemon query
returned no events, so the precise daemon error remains unproven. No assertion,
source or timeout was weakened; the second full run schedules that module as one
unit. Full failure traces/configs remain in `prior-full-failure-audit.json`.

An automatic approval review rejected a proposed parent-PID SIGSTOP test because
it could suspend pytest/the agent and prevent cleanup. That command never ran.
The replacement holds the exact custody lock, sends no parent signals and passes
both real-process cases. The rejection and replacement are retained in the
archive; no approval or unsafe workaround was needed.

## Actual model regression smoke

The protocol and English objective were recorded before execution:
“Fix src/example.py so answer equals 1. Preserve tests, README and user files.
Run the test suite and verify the result.” Model: `qwen/qwen3.5-9b`, functional
controller, seed0; no scorer, automatic loading or fallback.

Workflow `b1ac1fe653384de18e4aea6d690bdc25` reaches current verified completion.
The source becomes answer1 and owner tests/dirty binary remain intact. The exact
review binding is completed; repeating reconciliation is idempotent and spends
nothing. All20 registered review/integration checks have natural whole-group
stop receipts. Cleanup and physical worker stop are confirmed; reservations are
zero, no calls are uncertain and the workflow is inactive/succeeded. Limits stay
60 model calls,120 tools,30min,4 instances,64 tasks,3 attempts,20% verification
reserve. Claims remain harness-driven: this does not prove a runtime dispatcher.

This separate simple regression smoke does not replace or pass the earlier
failed pinned-conflict resolution, which still stands at22 model calls/35 tools.

## Retrospective feedback for the next regression

The prior model server retained16 responses correlated uniquely to the failed
resolver calls by timestamps, exact SDK token usage and approved artifact IDs.
All16 propose both valid pinned reads, and both survive the actual parser/safety
filter. In the unchanged source both reads share one fingerprint, equal base
scores and a common per-tool neural bias. Repeating successful zero-utility reads
also retains discount1. Stable ties therefore expose a concrete selection defect.
`prior-pin-provider-feedback.json` retains normalized candidates, response hashes
and the structural witness, excluding rationale and request/header contents.

This is retrospective engineering evidence, not a complete neural-state replay,
a fresh benchmark result or scientific promotion. Next: RED tests for separate
immutable-input identity and useful-outcome habituation, minimal correction,
then a newly registered real conflict case. The historical296-run benchmark and
10 trained checkpoints stay unchanged; the trained candidates remain rejected.
