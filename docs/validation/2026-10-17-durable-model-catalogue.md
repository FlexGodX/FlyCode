# Durable explicit model catalogue — 2026-10-17

Status: final validation passed; checked bytes are ready for publication.
Frozen implementation:
`97dd7e3dc67bcd653c943e84f2d955fd960de8620649a6aac1276bd2e6ab4dc1`.
Parent publication: `524075e00f6bc2c160c5e3f97adc18007f3872fd`.

## Behavior

Schema17 stores Pydantic `model-admission-v1` records in the canonical database.
Each role binds its exact completed host probe call ID/owner, endpoint, actual
loaded model and configuration hash. Discovery, profile snapshots and successful
HTTP without a valid role protocol do not confer compatibility. Restore makes no
model request; actual execution retains the existing loaded-configuration check.

The web catalogue opts into persistence. Internal callers use
`ModelCatalogue(base_url, database=path)`; omitting database retains memory-only
behavior. Read-only resolution works inside a canonical writer transaction.
No SQL transaction spans discovery, native metadata HTTP or inference. Snapshot
CAS rejects a stale process rather than overwriting a newer grant or revocation.
An observed configuration change is saved even when a busy endpoint refuses the
explicit probe; the refused probe sends no additional generation request.

Full validation also exposed a legacy history publication race: the in-memory
record was completed before source cleanup and disk persistence. The executor now
keeps the result local through cleanup, saves a detached final record under the
manager lock and only then publishes it. Stop remains active during cleanup;
unknown settlement retains reconciling. This does not add canonical hosting.

## Retained REDs and corrections

- Successful explicit probe lost on application restart: actual HTTP RED, then
  durable restore with exactly one generation request.
- Resolution acquired a second writer while a queue transaction was open:
  actual database-locked RED, then one read-only snapshot.
- Changed probe owner/configuration wrongly retained readiness: owner RED and
  three configuration REDs; per-role owner and configuration-bound evidence.
- Review found malformed primary envelope exceptions bypassing revocation.
  Expanded actual HTTP RED retains five failures and one already-rejected empty
  content case. Structural protocol failures now remove only the requested role,
  preserving completed request accounting.
- Busy-endpoint RED retained the old role after observing context8192→4096.
  Persist observed metadata before returning busy; preserve the concurrent actual
  request and send no extra POST.
- First full collection on source30d529:1058 passed/2 failed/8 skipped,1068 exact
  case IDs,245 unchanged files,1590.131s. Preserve that unsuccessful run. The
  custody failure was KeyError(PYTHONPATH) before launch, not an FD implementation
  failure. The test now derives the child path from the imported package and
  retains every duplex/closure/receipt assertion. The other failure was restart
  history reading result=None after the first API reported completed.
- Actual gated SourceExecution.finish reproduces premature completion. The final
  test requires running/result=None and second-run409 while cleanup is held,
  then completed history with the real result after settlement and restart.

Initial denied socket setup and missing local hatchling for no-build-isolation
were preparation failures, not feature REDs. The actual HTTP tests ran with local
socket permission; wheel build used the project's isolated backend.

## Final verification

- Post-correction focused32 passed38.31s; Ruff15 changed production/test files and
  scoped follow-imports=silent mypy6 pass. Final collection registers1076 cases.
- Extracted wheel50 passed42.86s, including a fresh child import from the extracted
  package, malformed/busy revocation, history settlement and actual duplex custody.
  No deselection. All156 Python/static distribution files match source bytes;
  README metadata matches. Wheel SHA256:
  `f4c15bedc398635a56b62dfc59c740486dedc1c6717c16f30d084e200f212ae7`.
- Ten extracted CLI commands pass (help/brains and existing command help paths).
  These are package/entrypoint checks, not an implemented canonical Tasks CLI.
- Real external FlyWire tests5 passed5.36s, including constrained readout training.
  No biological data or topology is replaced. These single-agent smoke tests do
  not grant orchestration or scientific admission.
- Real Qwen primary and Jev scorer explicit probes pass with exactly2 completed
  requests, usage747 and107 total tokens respectively. A fresh extracted-wheel
  process restores identical descriptors with socket connections disabled;
  model-call count remains2. Probe phase6.741s. No fallback or automatic loading.
- Final default collection:1068 passed/8 expected skipped, zero failures/errors;
  all1076 registered case IDs occur exactly once, all246 frozen files unchanged.
  End-to-end wrapper time1013.932s. Three independent non-web pytest groups use
  disjoint registered files and isolated artifact roots; all73 web cases pass
  after those groups terminate. The eight skip identities match the earlier
  collection; none is an orchestration/web case. Five opt-in real FlyWire cases
  also pass separately above. The skipped real-model repair is not relabelled
  as covered by two compatibility probes.

Final evidence root:
`/private/tmp/flycode-durable-model-final-npiasqf6`.
Retained earlier evidence:
`/private/tmp/flycode-durable-model-validation-7pc4q4ob`.
Static review found the malformed-envelope gap; the subsequent correction review
found no additional actionable defects. Review does not replace runtime checks.

Independent read-only evidence audit passes67 checks with zero mismatches;
450 input hashes remain stable after the audit generator exits. It confirms
full/wheel case identities, frozen bytes, package/README, live receipts and
separate historical failures. Retained restore evidence disables Python socket
connections, not an OS network sandbox; no independent packet/process attestation
or new execution is implied. Sealed regular-file archive:
`~/.cache/flycode/validation/flycode-durable-model-npiasqf6-v2`.

The first sealed archive is retained at the same path without `-v2`; it omitted
the machine-readable audit and exact pre-annotation report. Version2 includes both.
The initial report-reconstruction assertion failed on its trailing newline; exact
original bytes were restored and matched the recorded audit input hash. No test
outcome or source byte changed during this archival correction.

## Limits and next delivery

Admission is a last explicitly checked protocol snapshot, not proof of model
quality or unreported weight revision. Server-owned SQLite records are trusted;
corruption can refuse the entire endpoint catalogue and has no automatic repair
or probe. Snapshot CAS can require another explicit user probe after a conflict;
the actual completed request remains in the transport ledger.

History record and journal append are separate filesystem operations; this does
not promise an atomic multi-file or power-loss transaction. Concurrent managers
writing the same history directory and unique physical database host leadership
are not solved here. Managed coordinator lifecycle/recovery and execution API/CLI,
full verification UI, neural orchestration admission and the required research
matrix remain open. Aggregate Tasks5–8 and unproven §§90–99 are not complete.
