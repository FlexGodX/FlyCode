# Owner-approved rejected-review retries — checked checkpoint, 2026-10-19

Version4 `Pipelines.approve_parent(..., retry_policies=...)` now accepts explicit
writer/reviewer attempt caps and a bounded correction policy. Missing policy grants
no automatic retry. Each retry restores the exact rejected output, receives its
pinned observations and failed-case diagnostics through actual HTTP, obtains a new
fence, and requires fresh independent review plus combined/applied verification.
Previous spending, original deadline, permissions, criteria and max3 attempts are
retained. Parent preparation counts as an attempt: a parent permits one review
retry; an ordinary child writer permits up to two.

The failed artifact, negative review/execution and evidence remain immutable
prerequisites of later output. Removing either failed target bytes or negative
review evidence invalidates the final current result. Retry activation atomically
retires assignment/review authority, resets the reviewer, appends a typed receipt
and outbox event, and preserves every failure. Unknown effects prevent release.
Count/budget exhaustion saves a machine blocker without refunding spent budget.

## Verification

| Boundary | Result |
|---|---|
| Native retry feature file before final policy-instance guard |44 pass,366.51s|
| Final invalid-policy/unknown-outcome delta |11 pass,7.41s|
| Affected parent/v1/v2/v3, retained hashes, context/custody |54 pass,777.78s|
| Interoperability snapshot including current parallel brain |3 pass,186.07s|
| Complete retry file from extracted final wheel |46 pass,411.59s|
| Ruff / scoped mypy |12 targets /11 source files pass|
| Package / packaging inputs |173 exact files /177 unchanged inputs|
| Extracted CLI and UTF-8 README metadata |pass|

Counts overlap. Native44 and compatibility54 precede the final Pydantic instance
revalidation guard; wheel46 exercises the complete final retry file, including
that guard and unknown-outcome protection. Worker processes, registered pytest
checks and HTTP are real; model responses come from the loopback fixture. No
live-model intelligence, biological benefit or scientific admission is claimed.
Only feature-related tests were run.

A final-wheel SQLite audit verifies both child and parent retry routes: eight
attempts,4 model/39 tool calls, zero reservations/unknowns. Two child retries use
ten attempts,5 model/42 tools and retain both rejected artifacts/reviews. All four
audited workflows succeed after independent final integration. Owner dirty and
untracked bytes survive; source remains untouched until integration.

## Retained failures and corrections

[Development history](2026-10-19-review-retry-progress.md) and the adjacent JSON
retain38 measurement boundaries, genuine REDs, fixture errors, interrupted runs
and intermediate source/package variants. Important corrected defects:

- TaskSpec-only feedback was overwritten by canonical cortex construction.
  Required parent/retry data is now host-sourced into the actual outgoing request
  and checked again after the paid response before accepting candidates.
- Parent preparation and failed continuation could conflict on the same file.
  Latest failed output now replaces its own preparation delta over exact child
  input, while genuine prerequisite conflicts still block.
- A runtime-supplied recipe could replace owner retry limits. Exact persisted
  recipe comparison, stored binding/execution ownership and rerun pins now gate
  activation and all subsequent ownership checks.
- Exhaustion initially skipped current negative evidence and time checks. Both
  activation and terminal exhaustion now validate evidence outside SQL and repeat
  metadata/time/ownership checks under the transaction.
- Recursive episode task text and verbose console output overfilled required
  context. The prompt retains all nonempty observations, diagnostics and complete
  failed JUnit-case text; metrics, recursive objective text and empty collections
  are omitted. Oversized required context still blocks instead of clipping it.
- Actual prompt probes then showed a second overflow after the last successful
  edit: exhausted model budget still packed a request that could never be sent.
  The existing local budget stop now runs after ownership validation without
  building that unused prompt; local verification/stop routes retain their guards.
- Revocation during HTTP correctly discarded candidates but publication failure
  retained an unnecessary uncertain reservation. Exact stopped workers now settle
  known spending through the existing stop gate. Current pause/cancel intent is
  reread atomically; genuinely unsettled model/tool outcomes still retain custody.
- A copied Pydantic policy instance could bypass strict validation. New policy
  instances are revalidated before source preflight or queue mutation.

Independent read-only reviews found the authority, restoration, negative-proof
and pause defects above; the final narrow review found no further concrete issue.
This was a scoped correctness review, not a repository security scan.

## Source, archive and remaining work

Base:`bf68cae1badf32eb01f01adfebcd4e469933b468`. Final package source:
`/private/tmp/flycode-retries-final-package-source-hm87gw3z`.
Wheel SHA256:`c9821deb4d60f19bab6164921653c5b095154068c6fd78e68412b3e8be740995`.
The public package retains schema22 and excludes parallel brain/schema23–25
changes. A checked three-way merge preserves those changes in the shared checkout;
the local interoperability snapshot is separate from the public package boundary.

Archive:`/Users/Administrator/.cache/flycode/validation/flycode-retry-checked-wylntbol`;
59,429 regular files,363,858,200 bytes. Manifest SHA256:
`37afc3f03b55dcda2dc4beefd65552b90fa14dae2754dda2538469469e9a47a4`.
All inventory entries and hashes were checked. Copied custody identities retain
original absolute paths; this is audit history, not portable process proof.

This closes the explicit negative-review retry increment, not all Task4 or the
full delivery goal. Bounded graph replanning, common wait/review/result views and
aggregate/neural/research acceptance remain open. No full suite, graph/live-model,
UI or training rerun was performed for this feature. Earlier scientific criteria
remain unproven unless their own measured admission demonstrates otherwise.
