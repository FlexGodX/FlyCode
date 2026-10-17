# Approved pipeline dispatch implementation plan

**Goal:** execute an owner-approved writer → independent review → integration
route through the canonical dispatcher, without manual claims or owner commands
between stages. Preserve exact evidence, user bytes, original spend and deadline.

**Scope:** delivery Tasks5/6, Plan §§100/101/104. This bounded route has one
writer, one reviewer and one integrator under an aggregate root. General
multi-writer recipes, automatic retry/replan, quiescent parent wait, host-loop
recovery, shared UI/API and orchestration research remain separate acceptance.

## Architecture

- Immutable review-class bindings select explicit review/integration modes.
  Deep artifact, source and evidence inspection happens outside SQLite;
  assignment rechecks canonical metadata inside the common transaction.
- Review producer provenance stays distinct from the current owner generation.
  A fresh approved review can inspect a retained stopped producer after reopen.
  Integration retains its existing lineage and source compatibility guards.
- A durable owner recipe approves existing task IDs, task/profile/model/source
  pins, oracle heads, budgets and bounded rerun policy before execution.
- Runtime materialization selects the current stopped published writer attempt,
  then the exact accepted review. It changes only approved input pins and enqueue
  state; binding, receipt and event commit together. It never forges owner scope.
- Receipt uniqueness is independent of the runtime actor. Changed criteria,
  permissions, budget, model, source or oracle block the old authority. Retrying
  does not reset spend or deadline and does not release unknown effects.

## Steps and verification

1. [x] RED/GREEN exact review binding and native registered checks, denied scopes,
   old oracle, artifact tamper, migration rollback and responsive owner Stop.
   Confirm physically stopped checks retain uncertainty when effects are unknown.
2. [x] RED/GREEN exact integration binding, two-connection assignment race,
   metadata mutation between deep preflight and claim, changed source/evidence/
   criteria. Delegate actual application and recovery to the existing integrator.
3. [x] Verify fresh dispatched review and reintegration after root reopen using
   retained producer bytes and new evidence; preserve previous result history.
   These checks ran in the isolated prototype, not the published checkout.
4. [x] RED/GREEN one-shot owner recipe, zero-manual-claim subprocess/HTTP DAG,
   restart, replay, actor-independent receipt race and authority revocation.
   Do not use synthetic calls or manual stage approvals as acceptance evidence.
5. [x] Merge only checked source, freeze identity and run the complete default
   collection once per exact ID with Compose module affinity. Check relevant
   Ruff/mypy, extracted wheel tests/CLI, real FlyWire and the registered English
   actual-model pipeline. Retain every failed setup, RED and failed execution.
6. [x] Update Plan/handoff/README RU/EN with measured results and limitations,
   independently audit evidence and publish a checked commit to the public repo.

The existing standalone feedback benchmark/training report remains separate.
This delivery neither activates rejected checkpoints nor grants orchestration
admission, scientific promotion or completion of aggregate Tasks5–8.

Measured source `bab0a9d9cd07e13a32a32eec2b75cd0414bfa8f4b28659d36abf72a39cbfe417`:
931 passed/8 skipped,939 exact IDs once in537.337s; extracted wheel87 passed,
138 Python files/README bytes match; CLI10, real FlyWire5, Ruff/mypy48 passed.
Actual English Qwen pipeline58.348s, three successful stopped attempts, zero
manual claims or later owner commands,3 model calls/34 tools, current result,
preserved user bytes and no uncertainty/reservation. Independent audit passed.
[Report](../../validation/2026-10-17-approved-pipeline-dispatch.md).
