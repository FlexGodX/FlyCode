# Immutable verification catalogue — 2026-10-18

The trusted `VerificationRegistry` registers canonical-project subjects and
versioned pytest/acceptance profiles in the same Store. Registration creates no
workflow, model request or tool call. Snapshot bytes remain independent of later
owner edits; missing/replaced/revoked project roots deny fresh access. Captured
bytes and independent owner oracles stay in pinned private storage outside the
subject. There are no new agent tools, HTTP paths or execution commands yet.

Schema20 adds subjects, profiles, a storage identity and project/contract approval
heads. A profile and its current approval commit atomically; failed publication
cannot revoke the preceding profile, and delayed v2 cannot replace committed v3.
Each acceptance profile has its own immutable oracle tree. Existing engine and
legacy `verify --repo` callers keep their previous constructor/capture behavior.

New catalogue copies use an owned directory descriptor with exclusive,
no-follow writes and fsync. Identities and confined paths are rechecked after
final reads. Tests cover replacement before pinning, during copy and after copy
with an identical external tree. Registration must reject those cases without
publishing an unusable subject or writing source bytes through a replaced path.
This remains trusted same-user local execution, not a hostile-code sandbox.

## Verified measurements

| Check | Result |
|---|---|
| Full default suite, one complete attempt |1236 exact IDs;1228 passed/8 explicit opt-in skips,0 failures/errors;1824.535s driver wall |
| Frozen source/test/config/README files |270 unchanged; exact collection/report/JUnit Counters checked |
| Verification and identity focused set |68 passed in9.49s, including31 new registry cases |
| Extracted wheel |62 matching verification cases passed in9.81s;162 exact package files and README metadata |
| Extracted CLI |Actual wheel import asserted; functional/full controllers available |
| Real FlyWire |Five opt-in cases passed in5.42s using the verified external v783 graph |
| Static |Ruff13 changed files; scoped mypy4 source files with silent imports; diff check passed |

Eight default skips are retained as skips: two full-graph Docker variants, the
LM Studio case and five graph cases. The latter five are also measured separately
with their opt-in enabled; the report does not silently change the default count.
There is no new real-model or browser measurement in this catalogue increment.
The overall delivery still requires those acceptance checks at its final scope.

All native REDs remain in the external archive. The first focused green selection
had19 passes/one equality failure because environment package tuples reload as
JSON lists; the assertion now compares canonical JSON, preserving byte checks.
Review exposed real symlink, approval-publication and late-replacement defects;
each has retained failing output and behavioral regression coverage. Initial
wheel preparation used the byte email parser and corrupted the unlabelled UTF-8
README payload; UTF-8 text parsing confirmed the original wheel was correct.
That setup failure started no wheel pytest and is retained separately.

Both historical15s web observation cases pass here without raised deadlines.
This does not recover their missing historical CPU/I/O samples or establish a
causal latency fix. Previous failures and their unresolved diagnosis remain.

Source manifest: `6cd759e3d13ec599cf84ee86e6491ac1ccbd1bf3f8a7557290c148b40ccf780a`.
Runtime: `5d984b23baf8cc2dd7b7e87b37953dd44774973f0155e99473e58b3fe559ac86`.
Wheel SHA256: `ff4453612a228e0d05d4786836d090775c091d2d20b30d1c06c98ec290b502e7`.
Parent PUBLIC commit: `be2fc4d9c77925e2574b32222ba4d4f0a8a8898b`.
[Machine-readable evidence](2026-10-18-verification-catalogue.json).

Archive: `~/.cache/flycode/validation/flycode-verification-catalogue-4i6315_o`;
597 regular files/6367831bytes, no special entries, independently rehashed.
Archive manifest SHA256:
`e58433362c58e355daa3c556cfdc6c90b82b447002893139447924260d1aba08`.
No owned full/package/graph process remains active.

This is Task1 of the [shared control plan](../superpowers/plans/2026-10-18-verification-control.md).
Executable plans, start/cancel/rerun, cumulative budgets, Host recovery, protected
API/CLI/artifacts/reproduction and the verification screen remain required.
TST13/Phase25, aggregate Tasks5–8 and scientific §§90–99 are not closed. Historical
trained-Qwen transfer remains negative; nothing changes the default checkpoint,
biological topology or research admission.
