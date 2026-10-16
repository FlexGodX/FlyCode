# Result freshness and reopened generations — 2026-10-16

This Task6 increment follows `8cb1083`. Frozen implementation SHA256:
`a5524a1129e34b7728440ef59c8dab91bc666a324145b7ab77c67a65de025152`.
The environment and implementation fingerprints match before and after execution.
The overall specification-delivery goal remains open.

## Reproduced contracts

Real stopped workers produce immutable artifacts, separate reviewers execute
owner-registered pytest checks, and the host integrator checks candidate and source
before completion. These executions establish that:

- Source, untracked bytes, criteria, artifacts, evidence and displaced-original
  changes invalidate a completed result. Tracker and claim admission refresh it.
  Concurrent readers invalidate once; restart retains the canonical blocker.
- Metadata and reversible archive retain valid evidence. Historical attempts and
  spending are preserved; another workflow's active slot is unaffected.
- Owner reopen retains the task ID, deadline, budgets and attempt limits. Only
  exact old auxiliary-review generations retire. The selected reopened review and
  owner-revised requirements remain required. Draft/command inputs cannot forge
  host result fields. Schema8 records without freshness pins invalidate safely.
- A second real author generation requires newly approved checks and fresh
  review/integration. A compatible already applied result can instead undergo
  fresh review and source verification without another source application.
- Reverification checks the retained installation-object chain; stale reviews,
  changed source, owner cancellation and a historical child paused after candidate
  verification cannot yield completion. Replay does not consume another budget.

## Validation

| Check | Measured outcome |
|---|---|
| Full default pytest | 732 passed, 8 skipped; 558.12s |
| Extracted wheel: delivery, native application, worker runtime | 74 passed; 238.28s |
| Opt-in actual FlyWire graph | 5 passed; 5.84s |
| Relevant Ruff | Passed |
| Relevant mypy | Passed, 23 source files |
| Packaged source / README | All 130 Python files and README match |
| Extracted CLI | All ten registered controller names listed |

Wheel SHA256:
`60d06a8b61d78bab696f2329afcc688920473883c87c5635ffa79e5a4a8d6f8c`.

The explicit loaded-Qwen primary probe passed in 12.57s. Actual English-prompt
repair, independent review, integration and fresh source acceptance succeeded
using 3 model calls and 26 tools. The validation harness then reused an old enqueue
idempotency key, which was correctly rejected. That failed harness report is
retained, not relabeled as a passing whole run.

A separate continuation script continued the same stopped workflow with new command
keys, without rerunning the model or refunding spend. Reopened review and fresh
source verification passed in 5.75s using 20 more registered checks and zero
model calls. Final workflow: `succeeded`, 3 model calls / 46 tools total, zero
outstanding reservations or uncertain effects. The installed source inode,
unchanged owner tests and dirty binary bytes were preserved. The old integration
remains invalidated; a new current result owns completion. No model loading,
scorer or fallback was used. This is a small engineering smoke, not research or
orchestration admission.

JUnit, identity records, wheel, both live reports, scripts, SQLite ledger, source,
oracle and exact verification evidence are retained under
`~/.cache/flycode/validation/flycode-result-lifecycle-ug9nz4sh` with a checksum
manifest. This is a historical copy: absolute evidence references retain the
original execution location.

Coordinator crash reconciliation, multi-writer conflict/resolution scenarios,
the remaining stop/deadline/rerun matrix and canonical CLI/API/UI wiring remain
open. Registered host pytest process identity is not yet durable across crashes;
uncertain ownership must remain held rather than inferred safe from a saved PID.
