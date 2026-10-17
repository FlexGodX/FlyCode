# Mandatory resolution inputs

Goal: a numerically correct private patch cannot bypass required conflict-input
observations. Spec: `docs/Plan.md` §§100–101/104; retained native failure:
`docs/validation/2026-10-17-pinned-read-feedback.md` (9 models/23 tools).

1. RED in `tests/unit/orchestration/test_result_delivery.py`: Tools cannot admit
   mutation before both pinned conflict paths and current workspace were read.
   A real HTTP worker whose private file becomes3 without those observations
   cannot publish an artifact, even if its local tests pass. Preserve owner bytes,
   actual spending and stopped-worker/raw-result evidence.
2. Add `orchestration/inputs.py`: derive mandatory pins/paths from the existing
   canonical conflict, not task prose. Validate completed non-late read calls,
   exact attempt/fence, selector/path/hash and workspace binding. Outside SQL,
   verify immutable inputs and current workspace bytes before mutation. Recheck
   receipt metadata atomically at admission/publication. No second queue/ledger.
3. Extend `Artifacts` with a bounded UTF-8 workspace input reader and receipt in
   the existing event journal. Route resolution workspace reads through the
   existing host RPC; preserve ordinary task reading. Block unsupported binary,
   oversized or absent required text explicitly. Return actionable precondition
   failures to the real agent without executing a write or classifying usage
   mistakes as infrastructure health failures.
4. Gate publication, fresh review and integration/current-result acceptance,
   including older artifacts without required receipts. Reuse existing immutable
   artifact/ownership checks and leave original review/criteria/history intact.
   RED legacy-acceptance regression before each new gate; positive actual-worker
   fixture explicitly reads workspace in addition to both pins.
5. Verify missing/wrong paths, failed/late/other-attempt receipts, stale workspace
   versions, supported fresh reads, cancellation and ordinary-task compatibility.
   Then freeze source/README; run exact full collection, extracted wheel/CLI,
   relevant Ruff/mypy, actual FlyWire and a newly preregistered English native
   conflict case. Preserve every failed result and original budget/deadline.
   Update Plan/handoff/README RU/EN and publish checked changes. No scientific
   promotion or phase closure follows from this one engineering fixture.
