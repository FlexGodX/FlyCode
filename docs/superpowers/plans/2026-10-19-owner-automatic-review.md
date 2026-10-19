# Automatic negative-review owner controls Implementation Plan

**Goal:** Let an owner revise a stopped parent plan after an actual independent child review rejects its output, then finish through the automatic coordinator.

**Architecture:** Reuse the existing owner-command-v1 API and server authority checks. Extend the Tasks visibility predicate to the failed-child checkpoint supported by Replans. Preserve an exact stopped published writer output during direct/root-cascade pause and allow resume only for that settled output; leave replan evidence checks unchanged. A new browser scenario starts the actual coordinator through its public attach button, with no manual claims/ticks or synthetic worker results.

**Tech Stack:** Python, canonical SQLite Store, native workers/registered pytest, Playwright/Chrome.

**Spec:** docs/Plan.md §§100/103/104 and docs/superpowers/plans/2026-10-16-specification-delivery.md Tasks4/5/8.

## Constraints and review focus

One active workflow; maximum4instances/64tasks/3attempts/60modelcalls/120tools/30min;20% verification reserve. Preserve owner bytes, rejected artifacts, spending, deadline and parallel brain changes. Only feature browser checks. No new authority or quiescence endpoint. Hide the form when any full task card is paused; resume restores it. Pause, active execution and graph-version limits remain server enforced.

### Task1: Real automatic rejected-review checkpoint and visible replan

Files: create tests/browser/owner_automatic_runner.py and tests/browser/owner-automatic.cjs; modify src/flycode/web/static/owner.js and src/flycode/orchestration/store.py.

- [x] Prepare canonical resumable parent without a live manual Host; enlarge child/reviewer budgets before approval and omit retry policies.
- [x] Through the browser attach the real service, prepare and approve the parent. Serve English bad-edit/stop model responses, wait for stopped child_review_failed and capture before screenshot. Assert that the replan form exists: published code must fail this assertion.
- [x] Admit waiting_children/child_review_failed/child_failed in the existing UI predicate and translate both failure reasons. Keep all other guards.
- [x] Browser edits the failed child's objective, explicitly confirms replan, and observes fresh independent review, parent continuation and current applied integration without runner dispatch calls.
- [x] Assert rejected evidence preserved, attempt/fence/generation increased, no refund/deadline reset, source dirty bytes preserved; actual later source edit invalidates currentness. Inspect desktop1440/390/320 screenshots and keyboard submission.
- [x] Run relevant old owner preview/commands/recovery browser scenarios against the exact wheel; syntax/lint only for changed files. Check frozen combined-source compatibility without claiming research completion.
- [ ] Archive successes and failures outside source, update Plan/handoff/README RU+EN/report, publish only measured own files to master.

Full aggregate/controller/admission/neural/research acceptance remains open.

Checked evidence: [report](../../validation/2026-10-19-owner-automatic-review.md). Publication is the next operation; its commit identity is recorded separately.
