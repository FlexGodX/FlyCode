# New owner-approved graph branches — checked,2026-10-19

`Pipelines.replan(..., new_branches=None)` now atomically adds implementation/
review pairs while the parent is suspended. `BranchAddition` requires explicit
TaskDrafts, matching approved criteria and a pinned existing writer template;
source/runtime/brain/seed cannot be supplied as arbitrary host paths/settings.
The server allocates canonical UUIDs and short IDs in the final owner transaction.
Request-local `new:<index>` references connect changed existing and new writers;
all references become server IDs before any task/binding is persisted. New
reviewers extend the existing integrator dependencies; root criteria, previous
required tasks and their spending/attempts/deadline remain unchanged. The owner
must fund integration before execution; new branches never increase its budget.

All profile/project/tool/scope/criteria/template/source/oracle checks precede
activation. Profile/template snapshots and the complete old workflow/task state
are compared again inside SQL; counters, tasks, bindings, integrator semantics,
graph receipt and outbox events commit or roll back together. Source, model and
oracle inspection stay outside the writer lock. Immutable graph receipt shape
is unchanged; absent/empty additions keep the old idempotency request shape.

## Evidence

- Final frozen extracted-wheel36 pass324.15s. All36 cases are real subprocess/
  registered pytest/loopback HTTP routes or denial checks; none skip.
- Native guards30 pass201.31s; concurrent owners and limits3 pass17.33s;
  existing-to-new alias1 pass46.61s, new-to-new chain1 pass97.25s,
  required-failure preservation1 pass8.38s and bounded request1 pass1.89s.
- Existing replan34 pass297.59s. This boundary precedes the final tuple-size
  guard; the final wheel covers that guard. Counts overlap and are not summed.
- Frozen combined current parallel-brain source197 files:3 routes pass208.76s.
  This establishes interoperability only and is excluded from the public package.
- Actual retained g1/g2 receipt readers validate original hashes on published
  fixtures under both clean and combined code; environment currentness remains
  a separate verification gate.
- Ruff4/scoped mypy3 targets pass; no full type-check claim. Wheel175 source/
  static files match checked bytes, UTF-8 README metadata and extracted CLI help
  pass, and309 frozen packaging/test inputs remain unchanged. Wheel SHA-256:
  `7aef43fc199d2e000fdf6182f95bf6c2e8d5e007c64f3cf39c99da2ffc6b17ef`.

Final wheel ledgers: carried-completion and existing-to-new helper routes each
finish with8 attempts/4 model/49 tools; the two-new-branch chain uses10/5/62.
All succeeded with zero reservations/unknown outcomes. A successful independent
new repair leaves the prior failed child required: workflow stays running with
child_review_failed,5 attempts/3 model/9 tools and zero reservations/unknowns.
Its source remains unapplied. Thus a new ID cannot erase the old obligation.

## Retained failures and limits

Initial RED was the missing new_branches argument. The first complete attempt
reached integration but exhausted the original25-tool integrator grant; it was
not increased or refunded. Successful fixtures approve35 or45 before the original
workflow starts. One negative test expected the wrong exception: OracleRegistry
wraps missing files as ValueError; that test was corrected after reading the
actual method. Its run had18 pass/1 fail and an intentional invalid-model
serialization warning. Strict serialization now rejects mutated DTOs cleanly.
A no-policy negative fixture accidentally allowed2 model calls in one attempt
while its response helper assumed one call per attempt; its HTTP assertion and
resource_busy evidence are retained. The corrected fixture explicitly approves
one call before the workflow, without a retry policy.

False/zero/empty-string additions initially masqueraded as absent and had3 REDs;
only None/empty arrays now mean no new branches. An oversized request reached
preflight in a separate RED; the parser now bounds it before graph admission.
Cycles, changed authority during preflight, rollback/restart/replay, unique IDs,
threaded concurrent additions and real new-profile/create_file execution pass.
Small configured boundaries max_tasks6/max_children5 exercise history counting
and rollback; this is not a literal64-task stress claim. Shared hard64tasks,
8children/3depth/31pairs, max3 attempts and2 graph revisions remain enforced.

The adjacent JSON records14 frozen native/delta boundaries plus the final wheel,
commands, source/input fingerprints, logs and real database ledgers. Do not infer
one unchanged source across all early runs. No full pytest, live LM/graph smoke,
UI, training, neural influence or scientific promotion was run. Controller replan
proposals, combined avalanche/aggregate acceptance, shared result/command views
and full goal acceptance remain open. Parallel brain/schema changes are preserved
in the shared checkout and excluded from this public source package.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-new-branches-checked-gzvm1d9w`;40595 regular files,
288823546 bytes. Manifest SHA-256:
`3e633c65ca9b94a55ae7edf9f7f3f088239c16240d2ba0cf85bd1fd941a26544`. Inventory and every regular-file hash were checked.
Custody records retain their original paths/identities; copied evidence is not
portable execution authority. Publication identity is recorded separately after push.
