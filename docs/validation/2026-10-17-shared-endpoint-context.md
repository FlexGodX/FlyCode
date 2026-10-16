# Shared endpoint admission and context redundancy — 2026-10-17

Frozen implementation SHA256:
`c35a4b8557f074f8072a4eb0beaecada6a2b3822fa87847ad9d7260f9a4f142e`.
This increment continues Tasks5/6. It does not complete the dispatcher, canonical
UI, orchestration research or the specification-delivery goal. Earlier failures
remain in the [diagnostic report](2026-10-17-worker-diagnostics.md).

## Shared admission

Canonical calls, single/dual cortex, direct primary/scorer clients and explicit
probes use one existing SQLite `model_calls` ownership index. Schema10 allows a
separately typed host call with no fabricated workflow/attempt. Schema9 migration
preserves row order, calls, spending, events and live/uncertain ownership; an
injected migration failure rolls back the table, version and unique index.

Every participant must use the same configured database through
`FLYCODE_STATE_DATABASE` (default `~/.local/share/flycode/state.sqlite`). Separate
arbitrary database files do not coordinate. Configured endpoint URI identity is
retained when the SDK normalizes its URL. The server registry still owns browser
model references; this does not introduce browser-selected endpoints.

Admission commits before actual HTTP and holds no transaction during inference.
Waiting emits one wait notification; cancellation before admission sends nothing
and increments no request counter. Independent file tools remain responsive.
Unknown transport outcomes retain ownership across restart until an exact trusted
host acknowledgment. Canonical scope/fence methods reject host-call IDs. A known
late response after Stop retains usage and releases its finished transport without
returning candidates. No browser acknowledgment or automatic uncertain-call
release is added.

A busy explicit probe sends no HTTP, consumes no probe counter and cannot mark a
model unsupported. The API returns409 while preserving session-token/origin
checks. Finite nonnegative wait limits are validated before database/HTTP effects.
Explicit `decision_model=None` still creates no scorer; missing/default selection
and host-provided canonical executors retain their existing behavior.

## Context change from measured feedback

The canonical host explicitly marks that the objective is already present in its
required task text. The renderer then omits only the duplicate observation task;
task ID and all other observation fields remain. Standalone callers retain their
distinct observation task by default.

Pinned-input contexts retain one copy of each exact fact string, in latest
occurrence order. Distinct file versions, artifact IDs/provenance and errors stay.
Only the context copy changes; working memory and canonical action history do not.
Duplicate counts are audited separately from optional discarded facts. A unique
oversized protected fact still blocks before HTTP; input tampering after HTTP
still rejects candidates while preserving actual spend.

## Checks on the frozen source

| Check | Outcome |
|---|---|
| Full default collection, three isolated groups | 822 passed,8 skipped;1,137.53s wall |
| Independent collected/result IDs | All830 covered once; no missing/extra/duplicate cases |
| Combined targeted transport/context cases | 49 passed;40.67s |
| Independent endpoint checkout before transfer | 118 passed;58.19s |
| Extracted-wheel transport/LLM/context cases | 84 passed;73.51s pytest |
| Actual FlyWire checks | 5 passed;10.18s pytest |
| Relevant Ruff / main-checkout mypy | Passed /45 source files passed |
| Wheel | All132 Python files match checkout/frozen copy; README metadata matches |
| Extracted CLI | Lists ten controllers |
| Explicit primary protocol probe | Passed25.505s; no loading/fallback |
| Real English conflict-resolution case | Failed684.198s;22 model calls/35 tools |

Wheel SHA256:
`057dc27ba11cf06e08299b644eaddb944309b96a8c063a51d6ed527a7c19e441`.
The relevant mypy result is not a claim that the whole repository is type-clean.
Separate clean-copy import-following checks retain22 existing diagnostics in11
unchanged files on the published baseline; scoped checks pass. Their logs are
retained alongside the final main-checkout check.

RED/GREEN evidence covers real concurrent canonical versus primary/scorer/probe
HTTP, cancellation, persistent unknown ownership and exact acknowledgment,
migration rollback, configured-URI identity and authenticated API capacity.
Context REDs observe the objective twice and the same protected fact21 times in
actual HTTP. Final checks require one objective/fact, every unique version and
error, unchanged input objects and pre-HTTP rejection of excessive unique data.

## Actual model outcome

The English two-author conflict case is registered before execution with the
same global60-model/120-tool/30-minute limits and20% verification reserve. The
probe and canonical attempts use the same database. Size-only instrumentation
delegates unchanged to the actual message renderer and records at most512 samples;
it does not change candidates or add HTTP calls. Both authors produced
independently approved artifacts, preserving source0 and dirty binary bytes.
The resolver consumed its16-call allocation reading the first artifact16 times;
it never read the other required input. Its published artifact has no changes
and still contains answer0. The independent registered check rejects this before
resolver review or final integration. This is not verified completion.

There are61 renderer size samples,55 for the resolver, with at most one copy of
each exact fact. Samples include context-fit/compaction attempts, not only actual
HTTP payloads; pre-compaction renders can exceed the guard. No
`artifact_context_blocked` event occurs and all22 actual workflow model calls
finish with known outcomes. The probe is a separate completed host call in the
same database. No remaining reservations or unknown requests survive the case.
The unchanged source/environment identity is confirmed after execution.

The owner then cancels the incomplete workflow after rechecking all worker groups
are absent. Only the stopped resolver attempt changes from `awaiting_review` to
`cancelled`; all other attempt records and every artifact remain unchanged.
Spending stays22 models/35 tools, source answer0 and dirty binary bytes survive.
No deadline or budget reset, further model retry or positive verdict is created.

Removing redundancy fixes the measured context defect but does not establish
successful resolution or an efficiency gain. Executed reads alone cannot identify
whether missing alternative candidates or controller selection caused the loop;
candidate proposals/scores are not retained by this runtime. Register that
observation before another diagnostic experiment rather than attributing the
failure to neural weights or training against unobserved alternatives.

Raw protocols, source/environment identities, JUnit, package, context measurements
and model history are retained in
`~/.cache/flycode/validation/flycode-shared-context-fuaam3ks`.

## Remaining scope

The dispatcher still needs candidate-specific atomic remaining reservations,
owner-approved execution bindings and confirmed-quiescent child waiting/checkpoint.
Standalone-review recovery is being checked in a separate copy; it is not part of
this source identity or full run. General changed-contract/new-writer reopen,
remaining stop/deadline/rerun cases, canonical CLI/API/UI and the orchestration
encoder/admission/replay/intervention matrix remain open. The historical296-run
feedback benchmark and rejected trained checkpoints are unchanged.
