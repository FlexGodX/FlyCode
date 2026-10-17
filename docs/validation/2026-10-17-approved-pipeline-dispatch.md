# Approved pipeline dispatch — 2026-10-17

Status: validated bounded internal delivery; aggregate Tasks5–8 remain open.

Source fingerprint:
`bab0a9d9cd07e13a32a32eec2b75cd0414bfa8f4b28659d36abf72a39cbfe417`.
Parent publication: `11e8db7b4521365311af644de79c259a072ca29c`.
Final evidence directory: `/private/tmp/flycode-pipeline-validation-1biujgpa`.
Publication archive destination (sealed after the checked push):
`~/.cache/flycode/validation/flycode-approved-pipeline-1biujgpa`.

## Implemented scope

The internal dispatcher can run a bounded, initially approved route with four
existing sibling/aggregate tasks: an aggregate root, one writer, one independent
reviewer and one integrator. Initial owner approval pins tasks, profiles, model
configuration, source bytes, oracle heads, workflow budget and rerun policy.
Subsequent runtime ticks select the stopped current writer publication and its
exact approved review. Runtime does not construct owner credentials or accept
arbitrary future artifact IDs from task text.

Schema14 adds immutable review/integration bindings, recipes and unique stage
receipts to the existing database. Input pins, semantic revision, enqueue state,
binding, receipt and outbox event commit together. An unchanged DAG keeps its
workflow semantic revision. Metadata-only edits remain compatible; changed
criteria, permissions, budget, model, source or oracle block prior authority.

Filesystem/evidence inspection occurs outside SQL; assignment rechecks current
canonical metadata inside the shared transaction. Existing `Reviews.run` and
`Integration.run` perform registered checks, custody, application and completion.
Unknown check effects retain ownership and spending even after physical stop.
No transaction spans inference or project subprocess execution.

Exact owner bindings also support fresh dispatched review and explicit
`previous_result_id` reintegration after root reopen. Producer provenance remains
immutable while the binding pins the current owner generation. Existing lineage,
source compatibility, dirty-byte preservation and current-verdict checks remain
in force.

## Retained development evidence

- Review/integration/worker dispatch: 43 passed in73.12s, followed by the added
  dispatched reopen case: 1 passed in20.15s. Legacy conflict/resolution and Store
  contracts: 49 passed in98.95s. These ran on the earlier isolated source.
- Recipe implementation: 62 passed in107.87s, including19 new recipe cases.
  Exact scoped mypy passed for48 source files; Ruff passed the seven recipe files.
- Final composition: 3 passed in38.09s, covering the actual zero-manual-claim
  subprocess/HTTP pipeline, dispatched reopen and a different runtime root.
- The different-root RED exposed an unhandled `pipeline_scope` exception. Moving
  recipe lookup into the existing guarded result path preserves the denial while
  returning a blocked dispatch with no storage or execution effects.
- Earlier REDs/failures remain in `tdd/`: missing binding API, overly restrictive
  reopened-producer pinning, missing initial oracle/budget guards, denied local
  HTTP setup and a test using `revise(title)` instead of `set_metadata(title)`.
  Setup/test API mistakes are recorded separately from feature REDs.

## Final validation

The complete939-ID default collection passed:931 passed/8 skipped in537.337s
wall time, each ID exactly once. All three process return codes were0; wall and
monotonic elapsed times differed by less than0.001s. Compose module affinity was
preserved. Scoped Ruff and mypy48 source files passed.

The extracted wheel passed87 exact interface/runtime cases in147.486s. All138
Python files match the frozen source byte for byte, and package metadata contains
the frozen README. Extracted CLI lists10 brains; actual FlyWire smoke passed5
cases in5.317s. Wheel SHA256:
`0edc62fa17f49b345be4090a10f186e5ef532c2eabebba421b8ecf28a46a0b47`.

The preregistered English actual-model pipeline passed in58.348s. Qwen produced
one immutable writer artifact; subsequent runtime ticks performed independent
review and integration. Four tasks reached `succeeded`, all three attempts were
physically stopped and successful, two unique stage receipts remained, and a
fresh `Integration.current` check accepted the result. There were zero manual
claims and zero owner commands after initial approval. The source now contains
`answer = 1`; the binary user file and README are unchanged.

Workflow spending is3 model calls/34 tools, with zero reservation and no uncertain
outcome. Writer tools retain three failed `run_tests`, one successful edit and one
passing test invocation. The remaining29 operations are4 review checks and25
integration operations, including24 registered checks and one application. The
repeated failing tests remain charged overhead; this is not an efficiency gain.
All baseline expected failures and rerun outcomes remain in actual evidence.

The compact explicit primary probe passed in24.208s without loading or fallback.
The four completed model calls including the probe used5,105 known tokens
(writer4,358; probe747); there are no active/unknown model outcomes. Independent
audit verified exact test IDs, wheel/source/README bytes, current source evidence,
attempt binding IDs, runtime materialization actors, the six initial owner
commands, original budgets and stage uniqueness. No source/environment bytes
changed during full/package/graph/model measurements.

The live protocol allows one explicit compact primary probe, no loading/fallback,
at most8 writer model calls/12 writer tools,4 review checks and25 integration
operations within the original60/120/30-minute workflow limits and20% verification
reserve. Successful acceptance requires three stopped successful attempts, two
unique stage receipts, a current integrated verdict, unchanged user dirty bytes
and no unknown expenditure or reservation. The evidence retains actual failures
and all charged calls.

## Remaining work

This route is restricted to one writer/reviewer/integrator. General multi-writer
recipes, automatic retry/replan/reopen, quiescent child wait, manager recovery and
shared CLI/API/UI remain open. Exact manually approved multi-writer integration
and explicit reopen bindings retain their separate existing acceptance.

Shared legacy/canonical source-write admission still needs implementation.
The legacy `RunManager` writes registered folders directly and has separate run
history; endpoint exclusion serializes HTTP rather than source mutation.
Coordinator/apply journals protect bytes and freshness, but this slice does not
claim to exclude simultaneous legacy edits during canonical integration. The
source-admission/custody bridge is required before shared execution is exposed.

This is engineering verification, not a new efficacy benchmark. No trained
checkpoint is activated, no biological topology changes, and no orchestration
admission or scientific promotion is granted. The standalone296-English-run
feedback/training experiment and its rejected checkpoints remain documented in
`2026-10-16-feedback-benchmark.md`. Aggregate delivery Tasks5–8 remain open.
