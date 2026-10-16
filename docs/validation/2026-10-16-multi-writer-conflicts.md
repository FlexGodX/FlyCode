# Reviewed multi-writer conflicts — 2026-10-16

This Task6 increment follows `084e114`. Frozen implementation SHA256:
`92f957b5a0f425c8903ee6caab1f28ee3b6aa95120481126e4ccb0020acf4345`.
The specification-delivery goal and Task6 remain open.

## Behavior

Two real isolated workers can publish patches from the same source snapshot.
Each exact patch requires independent owner-registered verification. Compatible
patches pass merged and source checks before canonical completion; the tested
two-author integration consumes 25 verification/application tool calls.
Individually passing patches whose combination fails cannot reach source apply.
Dirty/untracked user bytes survive both outcomes.

Overlapping reviewed patches retain a canonical conflict with both immutable
artifact hashes, review IDs, source snapshot, contracts and conflict paths.
The host checks current ownership/fence, exact reviews and owner approvals,
permissions and every changed path before recording it. Multiple artifacts of
the same author task are rejected instead of silently collapsing assignments.

Conflict, one planned resolution draft, execution admission, integrator stop
settlement and events commit atomically. The draft has pinned inputs and base,
but no acceptance, permissions or budget. Owner approval remains required for
enqueue. No owner scope is fabricated. The original stopped author attempts
remain awaiting review; their tasks and requirements remain required.
The integrator's actual spend remains recorded and unused reservation is freed.
Replay after restart returns the same audit record without another draft or
model call. A conflict record is not a passing/current completion verdict.

If graph capacity blocks creation, both patches and the specific blocker remain
durable. The executed capacity case reaches the eight-child limit. Transaction
failure after stop settlement rolls everything back, and two SQLite connections
cannot duplicate the conflict or draft. Changed artifact/evidence, invalid
approval, foreign scope and stale fence cannot create the draft.

## Real model checks

An explicit loaded-Qwen primary probe passed in 12.36s. A fresh English-prompt
repair, independent review, integration and source acceptance passed in 54.01s
using 3 model calls and 26 tools. A separate two-writer English-prompt scenario
produced answer=1 and answer=2 from the same answer=0 base, independently approved
both patches and retained one conflict plus inert draft in 100.31s. It spent
6 model calls and 18 tools with no outstanding reservation or uncertainty.
The source remains answer=0 and the dirty binary is unchanged. No automatic
model loading, scorer or fallback was used. These are small engineering checks,
not a scientific or orchestration admission.

## Frozen-source validation

| Check | Measured outcome |
|---|---|
| Full default collection, three isolated shards | 763 passed, 8 skipped; 803.42s wall |
| Exact collected/result case IDs | All 771 covered once; no missing, extra or duplicate cases |
| Extracted package: delivery/store/plans/acceptance | 135 passed; 714.18s |
| New paths repeated on final wheel | 12 passed; 141.27s |
| Opt-in actual FlyWire graph | 5 passed; 10.77s |
| Relevant Ruff | Passed |
| Mypy, domain/orchestration/verification | Passed, 39 source files |
| Final package/CLI | All 131 Python files match; all ten controllers listed |

Final wheel SHA256:
`2e4cfe48844db321fa5e615e0c7370e586dd45233ca1b97effffda95dcf38fb1`.
The first extracted test package was built before the README update; final
wheel Python bytes match that tested package and the frozen checkout exactly.
The final wheel contains the updated README and also passed the 12 new scenarios
and CLI check directly. These 12 repeat cases are not added to the 135 distinct
package cases. Implementation and JSON-normalized environment identities match
before and after all measurements. Raw reports, source snapshots, ledgers,
negative baseline and the full coverage comparison remain in the evidence archive.

## Retained failures and remaining scope

The new conflict test fails against an external exact copy of `084e114` with
the old overlap error; its raw report, baseline source and negative result are
retained. The invalid-integrator-approval test also failed before adding current
owner-oracle resolution to conflict admission. An initial focused capacity test
was inconclusive during editing; the subsequent frozen focused runs passed.
The replay assertion was corrected to compare canonical JSON, because native
environment package tuples become JSON lists after durable loading.

The first wheel build without isolation failed because the runtime environment
does not install hatchling. Isolated builds succeeded without changing installed
runtime packages. A broad Ruff invocation reported the pre-existing unsorted
`domain/__init__.py::__all__` (RUF022); relevant-file Ruff passes. The unrelated
export list is unchanged.

Execution of the resolution draft remains open: a resolver still needs bounded
reads of both pinned versions, owner-approved rights and budget, a new isolated
patch, fresh independent review and merged/source checks satisfying every
original requirement. Original interrupted/failed attempts must not be marked
successful, and original tasks must not be archived out of the completion gate.
Standalone-review recovery, the remaining stop/deadline/rerun matrix, dispatcher,
shared legacy admission, canonical CLI/API/UI wiring and Tasks7–8 remain open.
These process/filesystem checks ran on macOS with trusted local code.

Evidence and executable harnesses are retained under
`~/.cache/flycode/validation/flycode-multi-writer-xvt6yk27`.
