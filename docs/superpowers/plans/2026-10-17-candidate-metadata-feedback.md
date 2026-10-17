# Recover misnested candidate metadata

Goal: preserve strict execution arguments while returning correctable feedback
for candidate prediction/rationale fields misplaced inside tool arguments.
Evidence: `docs/validation/2026-10-17-resolution-input-gate.md`,15 models/27 tools,
exact final provider response and complete `Unsupported tool arguments` traceback.

1. RED: actual AttemptEnvironment returns a failed transition with actionable
   English feedback, no debit/effect or infrastructure error. A subsequent clean
   call succeeds. Direct Tools authorization still rejects with code permission.
2. Only recognize the five candidate-schema metadata keys observed in the failure.
   Validate the underlying normal tool call completely before classifying a
   correctable metadata error; dangerous paths, root overrides, unapproved tools,
   fences, deadlines, budgets and cancellation retain terminal behavior. Never
   silently strip fields and execute a repaired call.
3. Add an internal StoreError subtype in orchestration/tools.py, preserving the
   external permission code. AttemptEnvironment translates only that subtype and
   existing missing-input errors into failed transitions. Journal metadata-only
   blockers. No queue/store/domain migration or policy permission expansion.
4. RED real HTTP/process worker: first proposal nests metadata, second request
   includes actual error feedback, corrected call publishes exact private bytes;
   preserve original source and actual model/tool spending under original limits.
5. Freeze code/README after targeted checks; full exact collection, relevant
   Ruff/mypy, extracted wheel/CLI, real graph and new English native case. Retain
   every failure and current budget/identity. Update Plan/handoff/report and
   publish checked changes; no scientific promotion or aggregate phase closure.

Measured status: steps1–4 verified, including29 passing tool cases and actual
HTTP/process correction. Frozen full856 passed/8 skipped,31 extracted-wheel,
5 real FlyWire, Ruff/mypy47 and CLI pass. Two explicit bounded primary probes
fail at30s with retained terminal provider evidence and known776/874 tokens;
no user workflow/native conflict case starts. Publish the checked correction
with this limitation, preserving both failures and current checkpoints.
Aggregate Task5–8 and scientific/orchestration admission remain open.
