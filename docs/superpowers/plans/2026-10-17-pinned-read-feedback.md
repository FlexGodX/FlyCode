# Pinned read feedback regression

Goal: stop repeated immutable-input reads from hiding other approved inputs,
without changing mutable-workspace rereads, biological topology or checkpoints.
Spec: `docs/Plan.md` §§21,25–29,104. Retrospective evidence:
`~/.cache/flycode/validation/flycode-review-verified-wsavojze/prior-pin-provider-feedback.json`.
The historical296-run benchmark and rejected training variants remain unchanged.

1. Reproduce the defect in `tests/unit/cognition/test_pinned_habituation.py`:
   independent pins/workspace must not share repetition; successful zero-utility
   pinned rereads must lose attractiveness. Actual tournament must choose an
   unread equal-score pin in both controls with positive/zero/negative bias.
   Check:8 genuine failures and2 compatibility passes retained before changes.
2. Add selector-aware, versioned pinned identity in `domain/action.py`; keep
   ordinary fingerprints. Track useful outcomes separately from actual success
   in `cognition/habituation.py`. Apply a bounded subtractive penalty only to
   pinned reads in `brain/functional_fly.py` so sign cannot invert habituation.
   Check: intermediate4 failures expose zero/negative multiplication; final92
   cognition/domain/policy/backend cases and Ruff pass. Three existing Functional
   mypy diagnostics must match an unchanged-HEAD copy; new diagnostics forbidden.
3. Freeze source/README/test bytes. Register a new English real-Qwen conflict case
   with the same objectives, original oracle and budgets as the retained failure.
   Two independent patches must pass exact reviews, conflict without source
   mutation, and a resolver must consume both pins and workspace before producing
   answer3. Require fresh review, merged/source checks, current completion,
   preserved owner bytes, confirmed process shutdown, zero reservation/unknown
   calls. Keep failed cases and settle only through existing trusted ownership.
4. Run all exact default pytest IDs once, retaining fixed-Compose module affinity;
   compare collected and JUnit identities. Verify extracted wheel bytes/README,
   consumer tests, CLI, real FlyWire and explicit model probe. Do not mutate src
   during these measurements. Record actual native outcome, not an assumed pass.
   Update Plan/handoff/report and README RU/EN; publish only checked changes.

This is a measured engineering correction, not training promotion, a complete
neural replay, dispatcher acceptance or evidence for scientific §§90–99 claims.
