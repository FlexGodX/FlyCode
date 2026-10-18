# Independent team pipeline — 2026-10-18

Version2 owner recipes execute independent writer/reviewer pairs and one joint
integrator through the existing queue, bindings, budgets and receipts. Version1
records, requests and receipt names remain supported. No schema migration or
second queue is introduced. The trusted Python entry is
`Pipelines.approve_team(scope, workflow_id, pairs, integrator_id, source=...,
expected_revisions=..., approval_hashes=..., key=..., reruns=0)`.

Each branch pins its exact worker binding and common starting source. Approval
validates every task/profile/revision and enqueues the root and writers without
executing code. Available artifacts can receive their independent exact review
while another branch still awaits publication. Review receipts bind reviewer IDs;
joint integration requires all outputs, successful reviewers and the full set of
immutable artifact/review records. Atomic materialization rechecks the producer
sequences, task states and complete receipt set after filesystem preflight.

Ordinary dependencies still require succeeded. Writers/reviewers remain
independent root children, and the integrator depends on all reviewers. The
existing max_children8 permits up to three pairs plus the integrator. This does
not implement arbitrary writer dependencies, dynamic child-wait, retry or replan;
treating awaiting_review as succeeded would weaken the completion gate.

## Verification

| Check | Result |
|---|---|
| New v2 and existing v1 pipeline cases |42 passed/0 skipped,103.44s;21 new v2 cases; exact collection and JUnit counts checked |
| Frozen inputs |12 source/test/README files unchanged through final native and package measurements |
| Extracted wheel |Six selected native v2 cases passed/0 skipped,52.63s; package import location asserted |
| Package integrity |169 package files equal to source; exact UTF-8 README metadata; verification CLI help passed |
| Static |Ruff3/scoped mypy2 and diff check passed |

The actual HTTP-fixture/process route has two writers, two reviewers and one
integrator: five attempts, three materialization receipts, a current joint result,
preserved dirty binary bytes and no remaining reservation or uncertain spending.
There are no manual claims or owner commands between approved stages. This is an
actual process/control test using fixture model responses, not a live-model result.
The successful route drives sequential ticks; it does not establish concurrent
worker throughput or a neural advantage.

Negative cases cover duplicate roles, foreign/reader/runtime scope, stale
revisions, missing/revoked second bindings, unsupported dependencies, distinct
source roots, changed source, idempotency mismatch, recipe reload and outbox
rollback. A real publication permits one branch review while the other writer
is still ready. Two Store connections materialize exactly one review binding and
receipt; a later advance creates no duplicate. Pause after artifact preflight
blocks materialization without spending or partial inputs.

A rejected second patch, individually passing patches that fail together and
overlapping reviewed patches cannot apply or complete the workflow. Both original
artifacts/reviews remain recorded and owner source bytes stay unchanged. Existing
conflict resolution drafts remain inert until separately authorized; this recipe
does not silently expand to execute them.

Both initial tests failed on the missing feature. The first implementation passed
the five-attempt route, but the inert replay assertion failed because its helper
recomputed revisions after enqueue. The helper now retains the original request;
changed replay payloads remain correctly rejected. The initial fixture and Ruff
observations are retained, followed by the32-case composition, separate negative
checks and final42/6-case measurements. No clean-first-attempt claim is made.
Independent read-only review found no concrete defect; its stated coverage gaps
are covered by the subsequent branch/race/negative cases above.

No full repository suite, real LM, graph or training run was made, following the
user's feature-only instruction. Brain/data/training and parallel MaleCNS files
are excluded. Aggregate Tasks5–8 and research §§90–99 remain incomplete.

Source manifest SHA256:
`d74ea7f9dcef3935eaa20f4af9c0420acfe081c781ec9e760050a905caab7d65`.
Package manifest SHA256:
`399d256e5965c81671fc52f91277cab7429a16c55e2a22a01fc76273d5f2f30f`.
Wheel SHA256:
`c91daccc1b4ebb6934f1f975e2aa5df1f18cff2c659226c473a095e35a1a6128`.
Parent commit:`83b226da25a6f0081e0ce3403774503e188914ce`.
[Machine-readable report](2026-10-18-team-pipeline.json).

Archive:`~/.cache/flycode/validation/flycode-team-pipeline-1nvl6lv2`;
4213 regular files/50409896 bytes, including final native/wheel case evidence,
independently rehashed. Manifest SHA256:
`23c162865f8557a443ec186ed336c5182cfe40bdd15c033154cb23ab1b43c9cd`.
All measurement handles are terminal.
