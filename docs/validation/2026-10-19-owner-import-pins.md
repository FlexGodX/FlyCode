# Owner import/profile pins — 2026-10-19

Trusted `OracleRegistry.approve(..., expected_hash=...)` compares the complete
approval fingerprint before saved approval bytes, owned-fd writes, existing-key
activation or current-head updates. Mismatch retains the previous inventory/head.
Exact imports retain file modes/checks and ordinary contract supersession.
Calls omitting the keyword keep their prior behavior. Storage construction can
create an empty directory; the guard makes no new no-follow traversal guarantee.

Existing registered-plan publication now checks the exact saved profile row and
its SQL project column as well as the current acceptance approval head. Missing,
changed or invalid records cannot publish a plan from an older physical preflight.
The guard stays SQL-only. The actual two-connection `create_plan` races cover
registered suites. Acceptance profiles are tested directly through the SQL guard;
they are not labelled acceptance-plan e2e.

Final extracted wheel: **22 passed0.43s**, exact collection, no skips. Native final
feature22 pass0.57s, relevant existing compatibility43 pass3.82s, including actual
registered subprocess execution and diagnostic reruns. Frozen parallel source204
with the own two-file override:22 pass0.73s, interoperability only. Ruff3/mypy2
pass; package177 source/static files and316 complete inputs remain unchanged.
UTF-8 README metadata and extracted CLI help pass. Wheel SHA-256:
`6bc97f4280c1f60c2b0d9b369308585b9c672c01f7d2fada420021c820851eed`. Counts overlap and must not be summed.

| Boundary | Exit | Cases | Seconds | Meaning |
|---|---:|---:|---:|---|
| owner-import-red | 1 | 4 | 0.319 | Missing expected_hash keyword; four actual expected-import paths fail before implementation. |
| owner-import-green | 0 | 13 | 0.276 | Thirteen import cases pass after pre-write expected hash guard. |
| owner-profile-red | 1 | 8 | 0.827 | Three actual suite publication races incorrectly accepted; three acceptance fixture failures are invalid setup (missing server-owned subject refs), not feature RED; two SQL-only cases pass. |
| owner-profile-corrected-red | 1 | 9 | 0.551 | Three actual create_plan suite races and three direct acceptance row-pin cases fail; current acceptance head rejection and two SQL-only checks pass. |
| owner-profile-green | 0 | 22 | 0.568 | All22 final feature cases pass against native frozen source. |
| owner-import-compat | 0 | 43 | 3.819 | Forty-three relevant existing registry/plan cases and real registered execution/rerun scenarios pass. |
| owner-import-combined | 0 | 22 | 0.728 | All22 cases pass against204-source frozen current parallel checkout with own two-source override; interoperability only, excluded public package. |

The initial mixed profile RED contains three genuine suite races and three
invalid acceptance-fixture failures (plain subject had no server-owned ref).
Tests were corrected at their proper layer; no fake runtime references or
production bypass was added. The corrected RED reproduces six real missing row
pins. Initial Ruff found two import-layout issues, fixed only in the new test;
initial mypy already passed. Full failed pytest logs/JUnit/inputs remain retained;
initial static output remains in the tool transcript, final static logs are saved.

Read-only independent review found no blockers in this two-source change.
Registered profile version1 is a format version; owner acceptance generation is
`draft.acceptance.version`. Future public requests must resolve exact server
profile refs and pin complete records in the final parent/replan CAS.

This increment does **not** implement that parent/replan wiring, no-follow oracle
staging, owner RPC, CLI/HTTP/Tasks approval/replan, atomic filesystem+SQL activation
or combined retry/replan acceptance. Those remain in the
[owner command plan](../superpowers/plans/2026-10-19-owner-pipeline-commands.md).
Full aggregate/controller/neural/research and final checks remain open. No whole
suite, live model/graph, UI or training run; no biological performance claim.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-owner-import-checked-f0m0ahbe`; 4571 regular files,
71985368 bytes. Manifest SHA-256 `601c9c5aa68bba4ebb71892f2f246c9f67754ab8189c1a71b98a772a396d2b3c`.
Source and destination regular-file hashes plus inventory were verified. Original
custody paths/identities are retained; the copy is not portable execution authority.
Publication identity follows in a separate record after push.
