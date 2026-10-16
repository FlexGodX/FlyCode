# Worker diagnostics and snapshot pytest boundaries — 2026-10-17

Frozen implementation SHA256:
`21a7005501b5413ffe22591073b7571971cae6f15ad9be65bc6ba56bc2f04ad2`.
This checked increment continues Tasks3/5/6; it does not complete Task6 or the
specification-delivery goal. The preceding real-model failure remains in the
[pinned-resolution report](2026-10-16-pinned-resolution.md).

## Implemented boundary

Every worker continuously drains stderr while retaining at most65,536 bytes in
`worker-stderr.log`, outside its repository copy. The terminal canonical event
contains the relative path, SHA256, observed/retained byte counts and truncation
flag, rather than raw diagnostic text. Partial launch and continuous output must
still allow confirmed process-group shutdown and responsive Stop. This adds
observability; a captured file is not proof of a particular failure cause.

Registered verification selects a captured root pytest configuration and supplies
explicit `-c`, `--rootdir` and `--confcutdir`. Without a captured configuration it
uses an empty host configuration outside the subject. Parent configuration and
conftest files cannot change registered IDs or the captured test scope. The seven
root configuration formats are checked on pytest9.1.1; native TOML cases require
pytest9. Malformed captured configuration still fails through actual pytest.
Criteria, node IDs, timing, process fencing and flaky policy are not weakened.

## Measured checks

| Check | Outcome |
|---|---|
| Full default collection in three isolated groups | 804 passed, 8 skipped;1,128.04s wall |
| Independent collected/result ID comparison | All812 exact IDs covered once |
| Worker subprocess cases | 23 passed;28.290s pytest |
| Verification/acceptance plus delivery scenarios | 64 passed;116.839s pytest |
| Extracted-wheel runtime/verification/acceptance | 85 passed;93.574s pytest |
| Actual FlyWire checks | 5 passed;9.826s pytest |
| Relevant Ruff / mypy | Passed /41 source files passed |
| Wheel identity | All131 Python files match checkout and frozen copy; README metadata matches |
| Extracted CLI | Lists ten controllers |
| Explicit primary protocol probe | First timeout30.142s; separate retry passed23.195s |
| English Qwen conflict-resolution case | Failed460.094s;15 models/28 tools |
| Same-workflow resolver retry | Failed109.689s; cumulative18 models/32 tools |

Wheel SHA256:
`a623a3f8572265a9dcbe4c71404ebc4f5bf35dd4b7e0e6a4496aa2155b968211`.
Normalized source/environment identities match before and after measurement.
No automatic model loading or fallback was used. Both failed model outcomes stay
separate from the passing automated checks.

RED evidence includes missing terminal stderr metadata, an unbounded-output
worker, ancestor configuration influencing registered IDs, and malformed empty
TOML values incorrectly treated as absent configuration. Corresponding real
subprocess checks pass on the final frozen source.

## Retained failed full run

The preceding diagnostic-only source produced733 passed/8 skipped/12 failures/
45 errors in458.69s. The validation harness mistakenly copied a helper named
`setup.py` above active test sandboxes. Pytest then selected the archive ancestor
as its root and changed registered IDs. The helper, all57 failing traces and the
configuration audit are retained in
`~/.cache/flycode/validation/flycode-worker-diagnostics-lm76b8wj`.
The failure motivated the production configuration-boundary regression tests;
it is not attributed to worker stderr, sleep or model quality. The final full
run uses safe helper names and the explicit snapshot boundary.

## Actual model feedback and settlement

Both writers produced their assigned values and received independent reviews;
their conflict preserved both artifacts and the source value0. The resolver
repeated a read and eventually hit the host's `artifact_context_budget` guard.
The same-workflow retry clarified both exact artifact IDs in English, preserving
the budget, deadline and prior attempts. It stopped on the same guard. No new
resolution artifact, fresh review or verified completion was obtained.

The retry registered size-only instrumentation before execution. It delegated
unchanged to the original message renderer, recorded23 samples and restored the
method afterwards; it did not alter payloads or add HTTP calls. The final sample
contains6,682 UTF-8 message bytes plus a1,536 output allowance against an8,192
context limit. This is a conservative byte-based guard, not a measured tokenizer
count. The611-byte objective appears in both the canonical task and observation;
the same38-byte protected file fact appears three times. Optional logs had already
been removed. Exact duplicate removal is therefore a concrete next engineering
experiment, not evidence that a controller retrain or prompt change will succeed.

Both runs confirm source preservation and zero remaining reservations/unknown
calls. The owner subsequently cancelled the incomplete workflow after all
attempts were terminal. The settlement preserves18 spent model calls/32 tools,
all attempt history, source value0 and dirty binary bytes. No budget or deadline
was reset. Captured stderr for the controlled host abort was empty and shutdown
used SIGTERM; this is not the earlier unexplained exit1.

Raw protocols, logs, SQL history, JUnit, wheel, source/environment identities,
retry samples and settlement are retained in
`~/.cache/flycode/validation/flycode-worker-verified-oo2vngei`.

## Remaining work

Reliable real-model conflict resolution, lossless context compaction, general
changed-contract/new-writer reopen, standalone-review recovery and the remaining
stop/deadline/rerun matrix remain open. Shared endpoint admission is being checked
separately before integration; dispatcher, orchestration experiments and canonical
CLI/API/UI also remain open. The296-run feedback benchmark and rejected trained
checkpoints are unchanged; these engineering cases do not justify promotion.
