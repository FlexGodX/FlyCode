# Owner-approved graph revisions — checked,2026-10-19

`Pipelines.replan` lets an owner revise objectives/dependencies of existing
unfinished version4 child writers while the parent is suspended. At most two
new graph versions are admitted. Original task IDs, criteria, permissions,
budgets, all past spending, max3 attempts and the original deadline survive.
The owner supplies an exact workflow revision and every task revision. A worker
or message cannot grant authority. New tasks/branches and controller proposals
remain outside this increment; Task4 as a whole and the aggregate goal stay open.

The original pipelines row is unchanged. Typed append-only graph receipts pin
base/previous hashes, owner snapshots, exact physical stage receipts, carried
completions and rejected review proofs. New stage generations shadow retired
reviews without rewriting their prior bytes. Every execution reader uses the
same effective grant. Changed writers get new fenced bindings and start from
the original source copy, followed by independent review and combined/applied
verification. Parent checkpoint authority stays original. New artifacts pin
graph lineage, so losing a failed artifact/evidence invalidates final completion.
Replanning requires settled spending and stopped ownership; pause/cancel,
expired deadline, stale revisions, foreign/cyclic edges, changed criteria/access/
budgets, current writers and uncertain effects fail closed. Source/oracle/model/
evidence checks run outside SQLite transactions; final activation is SQL-only CAS.

## Checked outcomes

- Native30 pass150.26s, guards27 pass132.58s; native second graph1 pass54.71s,
  concurrent-owner/actual-HTTP revocation2 pass9.35s.
- Retry compatibility46 pass404.87s; old V1/V2/V3/parent routes28 pass363.09s.
- Complete frozen extracted-wheel33 pass314.42s (runner314.82s). A later appended
  carried-failed-review scenario passes against the same extracted wheel1 in
  92.92s. Thus all34 current feature cases execute against the final package;
  the original33 test source and the appended delta have separate frozen inputs.
- Combined current parallel-brain source2 pass186.39s in a frozen copy. This
  checks interoperability only; that source is excluded from the public package.
- Ruff11 targets and scoped mypy10 targets pass. The initial recursive mypy
  invocation reported22 errors in11 unchanged modules; full mypy is not green.
- Wheel174 files match checked source, UTF-8 README metadata and extracted CLI
  help pass; all307 frozen packaging/test inputs remain unchanged. SHA-256:
  `619259dd8dd4e318b0085fd3581a4d8de0b0819214101de467f0471242483289`.

Counts overlap and early boundaries precede later guards/formatting. Do not sum
these into unique tests or claim an unchanged source across all runs. The adjacent
JSON records exact commands, roots, source/input fingerprints, JUnit counts and
logs for15 native/delta boundaries plus the complete wheel run. The ledger audit
reads actual registered tables: single-child replan has8 attempts/4 model/39 tools;
two graph versions and carried failed-review retry each have10/5/52. All three
workflow states are succeeded with zero model/tool reservations and no uncertainty.
Model responses are loopback fixtures; workers and registered pytest processes
are real. This is not a live-model, UI, neural or scientific acceptance claim.

## Failures and corrections retained

The first native route failed on missing replan authority. Exact negative-review
execution tampering and model admission reads under the writer lock each had a
separate RED; existing RetryCheckpoint metadata/evidence now guards preflight,
final SQL activation and retained graph proofs. A real old-format retained
full-model hash failed actual review; absent `graph_input` now joins the precise
legacy fallback. Initial two-child fixture setup called refresh_runtime without
its required model_ref; no feature result was inferred, and the unnecessary
metadata mutation was removed before the actual two-child run.

The reviewer also found two namespace issues: a carried old failed review must
be retired in the new view, and a second graph must prefer a newer physical
overlay when carrying completion/review. Both have real full-route checks.
A deliberate one-line regression disabling retirement reproduced pipeline_changed
before the green extracted-wheel delta; this is a controlled regression probe,
not an initial feature RED. Simultaneous preflights publish exactly one graph;
outbox failure rolls back tasks/bindings/receipts/events, restart/lost reply replay
without a second spend. Evidence revoked during real HTTP consumes the model call
but discards the edit and settles custody/reservations after confirmed stop.

Preparation assertions (nonexistent license input, wrong changed-file count,
pytest directory prefix) stopped their dependent action and did not modify shared
code. No full pytest, live LM/graph smoke, UI, training or research promotion was
run for this backend increment. Parallel MaleCNS/schema changes remain preserved
in the shared checkout and excluded from the checked public tree.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-graph-replan-checked-1tiz0318`;42483 regular files,
362828607 bytes. Manifest SHA-256:
`239bfdb8ea95667f6e17f5ec491651125048f748e8d33ce5e4aa70294f43c4a9`. Inventory and every regular-file hash were checked.
Custody records retain their original paths/identities; copied evidence is not
portable execution authority. Publication identity is recorded separately after push.
