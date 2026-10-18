# Rejected-review retry: unpublished progress, 2026-10-19

This is the **historical incomplete development checkpoint**. The final checked
result is recorded in [the retry report](2026-10-19-review-retries.md). At the
initial stop, released HEAD was
`bf68cae1badf32eb01f01adfebcd4e469933b468` (checked parent continuation).
The full §§100–104 delivery goal remains open. The separate MaleCNS agent's
working-tree changes have been preserved and excluded from these measurements.

Development source: `/private/tmp/flycode-retries-dev-20261019`.

Durable recovery bundle:
`/Users/Administrator/.cache/flycode/validation/flycode-retry-unpublished-ymoaqfjl`.
It contains 1,029 verified regular files / 6,565,776 bytes: private source/test,
measurement logs/XML/source hashes, latest three failed cases and the handoff.
Manifest SHA-256:
`b5d86c91bd50c70077daef61b88224570b31d1e27ca4f09682beba806e1cae2a`.
The archive is frozen; copied custody identities remain tied to original paths.
This preserves evidence and does not establish a passing verification.

Machine-readable boundary inventory with source hashes, counts and failures:
`/private/tmp/flycode-retries-progress-20261019.json`.
The private source is a Git archive of the released commit plus this increment;
at that initial checkpoint, production source had not been copied over the shared tree.
At that checkpoint only the plan annex and this report remained in the shared
tree; the new test was kept with its private implementation. After explicit owner
authorization, diagnosed fixes were verified and ported through a checked three-way
merge that preserves concurrent brain changes. The final report supersedes the
remaining-work list below while retaining these earlier failures.

## Implemented privately

Explicit v4 `RetryPolicy` and immutable `RetryCheckpoint`, owner-approved
writer/reviewer attempt caps, cumulative spending, new fences, retained failed
artifacts/reviews, exact reviewer reset/binding lineage and current successful
review selection. Canonical cortex passes host-pinned prior observations and
review feedback into actual HTTP requests and rechecks it after response.
Latest failed parent output replaces its own preparation delta during restore.
Missing policy grants no retries; v1/v2/v3 request semantics are unchanged.

Negative-proof checks bind author, reviewer, project, workflow, contract,
execution, rerun count, stored review binding and exact evidence. Runtime cannot
replace the saved owner policy. Exhaustion requires current negative evidence
and time authority; count/budget exhaustion persists a machine reason with no
refund. SQL transactions perform metadata checks only.

## Checked boundaries (counts overlap)

- `policy-green`: 1 pass / 1 failure, 14.90s. Saved-policy check passed; parent
  context still failed. `root-context-green`: parent route passed, 33.75s,
  after removing recursive `episode.task` and redundant environment metrics.
- `proof-red`: seven real failures for mutated execution/author ownership.
  `restore-proof-green`: eight pass, 59.87s, including those fixes and a parent
  retry that edits the same file as preparation.
- `exhaust-guards-red`: three real failures (missing evidence, deadline,
  clock rollback). Corrected boundary: three pass, 21.27s.
- `budget-red`: real missing durable blocker. `budget-atomic-green`: three
  pass, 12.07s, for budget exhaustion, two connections and outbox rollback/
  restart/lost reply. Actual debits and reserved totals are asserted.
- `caps-checks`: eleven pass, 12.62s, for explicit/automatic reservation caps
  and inert invalid owner policies before source preflight.
- `lineage-checks`: five pass / three failures, 31.17s. Retained historical
  review hashes passed. Two negative-fixture setup errors (owned task cannot
  be revised without release; verdict uses `inconclusive`, not `blocked`)
  were corrected without weakening production ownership.
- `recheck-red`: one pass / one failure, 10.78s. Exhaustion's missing SQL
  execution recheck was fixed; later `feedback-evidence-green` includes both
  passing recheck cases and corrected authority negatives.
- `feedback-evidence-green`: eight pass / two failures, 82.95s. The full child
  route receives actual failure diagnostics; deleting either old review
  evidence or failed target bytes invalidates the final current result.
  Both parent retries exceed the existing context guard when failure output
  is included.
- `three-attempts`: one pass, 40.95s. Two approved child retries produce three
  author attempts, retain both rejected outputs and finish fresh review plus
  integration with cumulative spending and no fourth attempt.
- `binding-red`: one real failure, 4.20s, for a changed persisted review binding.
  Exact stored-binding comparison was added afterwards, not yet reverified.
- `junit-feedback-green`: **three failures**, 25.05s. Replacing verbose stdout
  with complete failed-case JUnit text still does not fit either parent retry.
  Evidence deleted during a real HTTP response discards the candidate and
  retains its paid call, but leaves an uncertain reservation (0 models/1 tool).
- Ruff passed for 12 targets before the last stored-binding comparison;
  scoped mypy passed for 11 source files before later feedback/binding edits.
  No final lint/type/package/native aggregate pass is claimed.

Final feature-only `final-native` run was deliberately interrupted after the
stop rule was reached: exit2, one pass, 48.23s. No final package, compatibility,
full-suite, live-model, graph, UI, commit or push was performed for this increment.
Owned test processes were stopped and no matching final-run process remained.
The final static review found no additional concrete authority/lineage defects;
it did not validate runtime outcomes or close the observed failures.

## Continuation

AGENTS.md §6 required stopping after two failed corrections of one issue.
The owner subsequently explicitly authorized continuing in this session.
The frozen archive above retains the pre-authorization checkpoint. Work resumed
with actual prompt-size diagnostics rather than another speculative reduction.
Do not mark the full goal complete or call this increment released until its
final verification and publication are recorded.

1. Measure the actual composed parent prompt by field against the conservative
   context guard. Preserve prior observations, exact failed-case diagnostics,
   pinned inputs and current authority. Avoid removing required evidence to
   obtain a green test or increasing fixture capacity without a stated contract.
2. Diagnose late evidence revocation through real model-call completion, worker
   settlement, artifact publication and reservation release. Preserve the known
   paid HTTP outcome and reject effects; resolve only demonstrably settled cost.
3. Reverify stored-binding and exhaustion rechecks, then all retry tests and
   directly affected parent/v1-v3/cortex/review/hash consumers. Keep immutable
   source boundaries and all failures.
4. Finish independent read-only review, package/CLI and README RU/EN/status/
   evidence before a checked commit/push. Port with a three-way merge of only
   owned changes, preserving concurrent brain code. Replanning and the remaining
   delivery phases are separate outstanding requirements.
