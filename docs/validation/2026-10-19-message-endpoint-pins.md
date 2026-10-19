# Exact message endpoint admission — 2026-10-19

Checked against public parent `bf16604ccc7e875faa464039e71b89ca8fda6b77`.
This Task5 increment prevents a selected message from following a reused instance
into another attempt. It does not complete automatic owner-approved task routes.

The Host captures only live permitted endpoints before HTTP, including task,
attempt, instance, fence, semantic revision and contract. The prompt displays
these identities. After HTTP it rejects a changed endpoint even without incoming
message excerpts. A Host-generated candidate ID is bound to its tool and argument
digest; modified, forged and previous-generation candidates cannot debit a tool.
The broker checks the exact endpoint again inside its publication transaction,
including the original causal target. A second-connection replacement after tool
admission produces one failed spent tool and no delivery. Metadata and heartbeat
changes retain the same semantic endpoint. Raw controller progress keeps ready-time
pins and still permits an explicit recipient subset. Existing unpinned trusted SDK
audit/replay behavior and persisted record schemas/hashes remain unchanged.

| Final verification | Result |
|---|---|
| Extracted public wheel, message/runtime/tool tests | 150 passed, 94.83s |
| Frozen merge with current parallel brain | 213 passed, 147.75s |
| Public Ruff / mypy | 4 / 3 files pass |
| Merged Ruff / mypy | 6 / 4 files pass |
| Package / UTF-8 README / extracted Stop CLI | 184 files match checked source; pass |

Counts overlap. Final wheel SHA-256:
`82993e5418d64a850e6336fa0751b6e76a6dbf967737ec4e155baaadd7597082`.
Source/test/package hashes were checked unchanged across each final measurement.
Mypy retains its ordinary notices about untyped function bodies; this is not a
claim of checking every body in the parallel controller.

The parallel registered-command path originally bypassed candidate generation:
a real neural worker returned a message but the new guard refused it. Compatibility
edits preserve the complete neural Ready setup and lazy cortex, place Host endpoint
identities in command context, hide send_message without live routes and bind the
same `response.id:index` IDs used by Runtime. Eight added cases cover actual worker
send/metadata/pause/reuse, absent-route read/forced-message refusal, refined-response
IDs and replacement after binding. These compatibility edits and their additional
test remain in the local parallel implementation and retained patch, outside the
public 184-file candidate. The existing trusted test-source circuit establishes
software compatibility, not full biological or research acceptance.

Retained failures include the four baseline held-HTTP failures, real progress
retargeting, raw-subset regression and actual neural command rejection. Initial
missing-API tests and harness/schema/type/style/merge-preparation mistakes are
listed separately in the JSON record and are not counted as product RED or passes.
The wrong refine fixture was corrected against the actual RefineOutput schema.
No full suite, new interface, live production-model/biological benchmark or
scientific admission/promotion was run.

Private evidence: `/Users/Administrator/.cache/flycode/validation/flycode-message-endpoints-checked-l3ee7eik` — 5007 regular files,
57356646 bytes, 0 special/link skips;
142 regenerable cache directories omitted.
Manifest SHA-256: `7926dc1418d69bc2e79ce4a84a796f56efb68a79381fcca9150d2c2968136593`.
Automatic owner task grants, dynamic sibling routes, message/task-request avalanche,
aggregate and scientific/final delivery gates remain open.
