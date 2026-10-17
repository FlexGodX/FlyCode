# Owner-bound worker dispatch — 2026-10-17

The canonical queue now selects a globally eligible task/instance pair and starts
its existing Runtime without a manual per-attempt claim. Owner-approved frozen
source bindings persist in the same SQLite database (schema12). This is a worker
slice of delivery Task5, not automatic review/integration or full DAG completion.

## Authority and scheduling

`Dispatcher.bind` captures the exact approved non-Git folder, task/workflow
semantic stamps, contract, runtime root, brain and seed. Registration is inert.
Replacement requires the current binding ID and task revision; historical records
and command receipts remain immutable. Replaying an approval after folder removal
returns its saved record; a new tick still blocks on the unavailable source.

`tick` checks source bytes outside SQL, then `Store.dispatch_bound` rechecks current
binding identity, contracts and every existing eligibility rule in the assignment
transaction. Ranking spans all compatible instances, with candidate-specific
remaining task/global/development reservations, unchanged baseline and eighth-start
aging. An assigned but not yet started attempt blocks another admission. Assignment,
reservation, event and replay receipt commit together; no inference runs in SQL.

The source is captured again before spawn. A changed source or owner pause/cancel
between claim and spawn cannot produce another writer; a known unstarted attempt
settles without spending. An admitted or uncertain worker retains existing custody
and reconciliation protections. All original limits/deadline and20% verification
reserve remain in force. Existing explicit `claim` callers remain supported.

## Verification

Final frozen implementation:
`299775468f46bcef4bb465e13b6d6edc6427f9a4d0c877fcc22cff7bc85e98fe`.

- Full default collection: **886 passed,8 skipped**,894 exact IDs once across three
  processes;715.153s wall, wall/monotonic difference0.00495s. Fixed Compose-module
  affinity is preserved.
- Extracted wheel:40 focused checks passed84.46s, including actual worker dispatch,
  catalogue protocol negatives and prior review/integration regressions.
- Real FlyWire:5 opt-in checks passed. Changed-file Ruff and normal mypy48 pass;
  extracted CLI lists10 backends. All134 Python files and Russian README metadata
  match wheel/source; both README files stayed frozen.
- Wheel SHA256:
  `dcd2f3654f7a7efcd5b50453b260c20b47af9d08847beff89bca461c094d927b`.
- Capacity sample:64 tasks,165 synthetic zero-effect historical attempts; one
  `claim_next` took0.0120s and409 SELECTs. This is a local queue sample, not an
  inference benchmark, dispatcher throughput result or SLA.

TDD evidence retains the missing-dispatch8 failures, two missing-source failures,
six probe-count failures and two pre-spawn stop failures before their fixes.
The addressed runtime/store/tool/catalogue set passes152 cases before the final
replay correction; the final dispatcher/peer-stop set passes21 in12.53s. Test-draft
failures (insufficient context, missing required context fields/report variable)
are retained separately from production defects. The first full frozen source
`f0b04e9` has884 passes/8 skips/1 failure: an existing peer-stop test wrapper did
not forward the new optional workspace keyword. The wrapper now forwards it while
still cancelling and confirming the real peer; the complete final rerun is green.

## Actual Qwen execution

The explicit built-in primary probe requests one structured candidate; ordinary
candidate generation retains its previous default and timeout. Empty, malformed
and unsupported replies still fail compatibility. The new probe succeeds with
one completed call,747 actual tokens and no loading/fallback. The two preceding
30s timeouts on source `c8cf462` remain failed historical trials, not successes.
This single new observation does not establish a general latency improvement.

English request:

> Set the answer variable in src/example.py to exactly 1. Preserve all tests, README and user files. Run the tests to verify your change.

The functional worker is selected/launched through `Dispatcher.tick`, with **zero
manual claims**. One attempt produces exact private `answer = 1` bytes, while owner
`answer = 0` and binary dirty bytes remain unchanged. The81.14s case uses3 actual
model calls/4,261 tokens and5 tools: two failed test runs, read, edit and a passing
test run. The repeated unchanged test is retained as observed overhead. Including
probe:4 completed calls/5,008 tokens; no running or uncertain call remains.

The process group is confirmed stopped, reservations are zero and replay creates
no second attempt. The engineering smoke ends at a private artifact and is then
cancelled without changing its3-model/5-tool spend or deadline. It has zero review
or integration records and **does not claim current verified workflow completion**.
No checkpoint is trained/promoted and no neural advantage is inferred.

## Remaining work and evidence

Automatic review/integration bindings, future-output recipes, addressed dispatch
routes, quiescent child waiting/recovery, scoped neural orchestration and common
CLI/API/UI remain open. Delivery Tasks5–8 are incomplete. A separate isolated
exact-review prototype has real registered-check tests but is not part of this
published worker source or its acceptance claim.

Raw protocols, failed and passing JUnit, frozen source/wheel, canonical database,
private workspace/artifact, model/tool events and independent audit are retained
under `~/.cache/flycode/validation/flycode-bound-worker-ryjboeik`.
