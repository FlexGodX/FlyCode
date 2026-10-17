# Registered physical execution authority — 2026-10-17

Status: validated bounded runtime/integration correction; Tasks5–8 remain open.
Source: `7f5438ea14ccc809e3d870eb127c2dc38f1461d00e864d199e8566674fda60a8`.
Parent: `b479926dbad49d2adadea048a34c13c42f3eb3de`.
Evidence: `/private/tmp/flycode-registered-main-validation-cqdf6gkc`.
Archive destination: `~/.cache/flycode/validation/flycode-registered-authority-cqdf6gkc`.

Registered IDs now require `Projects.resolve(actual_source).id` equality before
dispatcher admission and launch, before/after private workspace capture, before
integration baseline capture and through existing source apply/rollback guards.
Root/ancestor replacement is rejected even with identical file bytes. Review
source provenance comes from the canonical author workspace record. Filesystem
inspection stays outside SQL; budgets, stop proof and contracts are unchanged.

Six actual isolated REDs showed that substituted folders still allowed
assignment/worker launch and that a registered task could launch another
registered folder. A later actual RED caught replacement baseline bytes being
copied after source-claim admission. The added boundary checks fix those cases.
All13 new cases passed22.42s, including a real local mock-HTTP worker, independent
review and successful integration preserving user bytes. Fixture/probe setup
errors are retained separately from product REDs; no real models were invoked.

The reviewed immutable patch composed against exact parent hashes. Main83 exact
focused cases passed141.96s, with zero skips/errors; their XML IDs match the
isolated83-case run (145.08s). Ruff4 files and scoped mypy52 source files pass.
The extracted wheel passed all13 new native/mock-HTTP cases18.786s process time,
CLI lists10 brains, and all145 Python files/README metadata match source.
Wheel SHA256:
`9688b879b04332d1449b846db49ccc1e5b2e4e434e9082485ee8ed7120cbc520`.
Independent audit verifies233 file hashes, exact scoped/wheel IDs and unchanged
environment. The parent1017-ID full suite/282 wheel/real-model/graph outcomes
belong to b479926; they are not reported as rerun on this changed source.

Historical unregistered IDs retain trusted low-level compatibility, including
UUID-shaped strings; this grants no HTTP scope. Already admitted child tools may
continue/spend inside their private copy after external source replacement.
Loss of authority can block rollback into even the retained original physical
folder: result/attempt remain reconciling and the claim stays held for trusted
repair. There is no forced unlock or hostile filesystem transaction.

Durable worker crash receipts, unique DB host leadership, shared execution and
verification endpoints/UI/CLI, and neural orchestration remain separate work.
Future stop/reconciliation authority must remain usable for an already admitted
worker after its source disappears; fresh source authority is required for new
dispatch/effects, not as a substitute for exact local stop proof. No biological
topology/checkpoint/default changes or scientific admission follow.
