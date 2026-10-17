# Managed coordinator core — validation, 2026-10-17

Validated continuation of6c6d33e and the
[physical host foundation](2026-10-17-managed-host-foundation.md).
This increment does not complete aggregate Tasks5–8 or scientific §§90–99.

## Result and boundaries

The internal `python -m flycode.orchestration.coordinator` process owns one
physical Host session. `Dispatcher.select` retains canonical approval/claim;
`launch` runs on a separate Store connection. Four stage threads and one admission
thread are ephemeral execution handles, not another persisted task queue.
The control connection renews heartbeat10s/lease30s independently of blocked
review/integration/HTTP. Owner pause remains a canonical Store command.

New dispatch resolves current physical project registrations. Recovery uses
historical project IDs only for saved worker/review/integration custody and stop
settlement, including when the source folder is unavailable. It retains unknown
HTTP/pytest outcomes, spending, reservations and source claims. Graceful shutdown
drains authority before waiting for actual HTTP/stage completion, and releases
the exact kernel descriptor only afterward.

Recovery compares an observation captured before the service call. A late
acknowledgement during that call therefore triggers another pass. Temporary
coordinator/process/source barriers receive bounded1s probes; repeated identical
blocked events use canonical idempotency. A completed integration also retries
exact source custody settlement after a temporarily held lock becomes free.

The web application does not launch/attach this process yet. Two independent
application processes and protected HTTP Stop, public CLI lifecycle, verification
API and full shared UI acceptance remain open. Native tests use a real local HTTP
fixture, not a loaded LLM; actual-model evidence is a separate check.

## Retained failures

- Missing standalone module and separate selection seam: two genuine REDs.
  The earlier two `profile_scope` failures were an incomplete probe fixture,
  not feature evidence.
- Initial managed route failed because a dispatch idempotency key exceeded200
  characters; the key now hashes the exact signature/root inputs.
- Graceful shutdown initially released leadership while the old HTTP request
  was still in flight. The original RED and48-pass correction are retained.
- Worker recovery and review/integration recovery initially failed three native
  crash scenarios. The corrected seven-case set passed50.50s.
- A real late HTTP acknowledgement raced recovery and became permanently cached;
  an unchanged review lock release also failed to wake recovery. Both genuine
  failures are retained. One initial late-ack setup forgot `Host.__enter__` and
  is recorded as a fixture mistake. Corrected nine-case set passed49.99s.
- A completed integration retained its source claim after the exact held lock
  was released. The genuine one-case RED is retained before the source-settlement
  correction.

## Current checks

Final focused set:108 pass, zero failures/errors/skips,180.19s. Its exact IDs
match the registered collection. It includes all ten managed coordinator cases,
physical leadership, dispatcher, review dispatch and pipeline dispatch.
Ruff over domain/orchestration and its tests passes; scoped mypy passes13 changed
production files. `git diff --check` passes.

Initial implementation/static/test/config freeze:250 files, SHA256
`e49ce77255805a0533b02d57d66c4f57fdedb8e32e6267fe64a950b8ed7b6f3f`.
Final default collection:1130 exact IDs,1122pass/8expectedskip, zero failures/errors,
1008.627s. Its250 final files are unchanged. Compose modules stay together; all73
web tests run after the three non-web groups and pass. The final manifest appears
below. No previous full-suite result is relabelled as current evidence.

Wheel build succeeds in an isolated build environment;158 package files and
README metadata match exact source bytes. An earlier build without isolation
failed because the test environment has no hatchling; that setup failure is
retained. Extracted runtime108 exact cases/CLI10, real graph5 and the actual-model
pipeline below pass with their separate terminal receipts.

Evidence root: `/private/tmp/flycode-managed-core-validation-vnp7b7y6`.
Machine status: `/private/tmp/flycode-managed-core-status.json`.
Focused JUnit SHA256:
`8a37169ccb939b2b91200bd15cec0d451fcd991dabbe1568451f85834c6a8888`.
Closed regular-file raw copies are retained; special files are not archived.
Publication evidence archive:
`/Users/Administrator/.cache/flycode/validation/flycode-managed-core-1obgwi33`.
Its seal requires the final independent audit, mandatory regular inputs and exact
frozen hashes before any archive is accepted.

## Additional terminal observations

Five opt-in real FlyWire checks pass with zero skips, including constrained
readout training on the unchanged biological graph. This does not establish
orchestration admission or scientific promotion.

The first real Qwen primary probe timed out after30s. The exact LM Studio server
log binds the request timestamp/model to a stopped empty prediction with673
prompt tokens and zero completion tokens. `HostTransport.finish` acknowledged
that failed outcome and retained its usage; no role was granted from it. A single
explicit repeat then passed against the same loaded model/configuration. Both
attempts and the exact server receipt remain in the private evidence.

The actual standalone coordinator completed the English repair pipeline in
74.002s, with three successful stopped attempts and a current verified integration.
Workflow spending was3 model calls/34 tool calls, no outstanding reservation or
unknown effects; the two explicit probe calls are separate retained host calls.
There were no manual claims or owner commands after approval. Dirty/untracked
owner bytes survived; the exact coordinator exited0. No trained checkpoint or
scientific improvement is inferred. Runtime implementation fingerprint:
`2ecc20e62b5d138aa5a5980134aed7c9064aac8c7d0c217f0b47eab62e67cab0`.
This fingerprint covers the runtime's research identity and differs from the
250-file source/static/test/config measurement manifest above.

The initial full suite exposed an old fault-injection seam in
`test_root_replacement_between_claim_and_launch_starts_no_worker`: it accesses
`Dispatcher._launch`, which the new select/launch boundary renamed. Its original
assertions are retained. The frozen run finished before adapting that seam;
no green result was claimed for it. That initial full run completed all1130
exact IDs with1121pass/1failure/8expectedskip, zero errors,1048.804s and250
unchanged files. The two old seam references now use public `launch`; every
original assertion is unchanged. The entire registered-project module passes13
checks in19.44s. No runtime/package bytes changed.

Extracted wheel verification passed108 exact native cases in199.964s plus ten CLI
commands. Package SHA256 is
`a1470f77286c59191bb3d2be2401878d6fdb4f0fc83c4abbf727acd9b7276f64`.
A new full run has1130 registered IDs and250 files, manifest SHA256
`f58674796e280b3985f03112780e42812556ab355ed1c4e16a27aa366d5e7d0e`,
with only that test seam changed from the retained initial run. It completed
successfully under `/private/tmp/flycode-managed-core-final-1obgwi33`; every
registered ID and frozen hash is checked. The exact eight default skips are
listed in the [machine report](2026-10-17-managed-coordinator.json); none is
counted as a passing test.

The archive helper also retains three actual negative failures and their
corrections: missing mandatory input, mandatory symlink and a frozen-byte change
between validation and copy. Four final controls pass, including a valid closed
archive that skips a FIFO and a symlink. Mandatory inputs now validate before
target creation; every frozen copy binds its expected digest before sealing.
This assumes closed inputs and does not claim protection against hostile
concurrent path replacement.

Independent final audit passed3020 checks with zero failed checks and rechecked
721 closed input hashes. It confirms the final exact IDs/bytes, package, retained
failures,29 saved process receipts,7 verification manifests and371 artifact
hashes. Audit JSON SHA256:
`bf8d902fac12015c642148190077667d10bcb274e997d851269a6799de7d8da5`.
The audit explicitly has `goal_complete=false`; shared application lifecycle and
neural orchestration remain outside this core increment.

[Implementation plan](../superpowers/plans/2026-10-17-managed-coordinator.md).
