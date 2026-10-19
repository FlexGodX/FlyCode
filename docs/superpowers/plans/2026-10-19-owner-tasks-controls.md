# Owner Tasks controls implementation plan

Goal: expose the checked live owner commands in Tasks, preserving existing cards,
saved materials, comments, interface switching and independent Stop controls.
Use the published owner-command-v1 request/result models without adding fields
to their strict response schema. This is Task4 of owner-pipeline-commands.

1. [x] Add a failing native browser case for the missing plan button/dialog.
   Reuse canonical fixture generators, live AttachmentServer and owned HTTP app.
   Capture the existing detail at1440 before the first missing-control assertion.
   No new attempt/model/tool effects from opening the plan.
2. [x] Add a separate accessible dialog outside the refreshing task detail.
   Load the owner preview and existing protected verification catalogue. Display
   blockers, task/profile refs, permission/budget summaries and checks. Explicit
   preparation selects writers/current binding refs and a supported controller;
   explicit approval confirms writer/reviewer pairs, integrator, registered
   criteria, reruns and bounded optional retry policy. No implicit binding,
   criteria registration, model discovery/load or task enqueue on preview.
3. [x] Add replan objective/dependency edits and explicit new writer/reviewer
   pairs cloned from a selected saved template, with selected registered full
   acceptance criteria and bounded budgets. Server allocates all new IDs. Show a
   concrete summary and require confirmation before approval/replan submission.
4. [x] Retain the exact pending command/key in session storage before sending.
   Network loss or partial preparation offers explicit retry of saved bytes;
   page refresh never silently substitutes fresh revisions or executes it.
   Keep per-workflow requests separate; stale success/error callbacks cannot
   overwrite a newer dialog/card or remove its Stop controls.
5. [x] Use existing canonical Stop and its shared pending keys in the dialog.
   Owner busy affects only owner mutations. Root/scope loss removes plan content
   while retaining minimal historical stop handles. Stop does not wait for owner
   preparation or uncertainty about a previous response.
6. [x] Run browser-only verification for this UI increment: actual preview,
   lost preparation reply/exact retry, confirmed approval, stopped-parent replan/
   new branch and fresh current integration; controlled stale scope and partial
   response cases are labelled separately. Check keyboard, focus,1440/390/320,
   overflow, independent Stop and old Tasks/results/materials behavior. Inspect
   before/after screenshots. Save all failures and actual execution counts.
7. [x] Build/check the extracted package/static assets and README RU/EN; freeze
   combined parallel source for relevant interface interoperability. Publish only
   exact checked own bytes, preserving the separate brain work. Update Plan,
   handoff and parent plan. Full/controller/neural/research/final gates stay open.

One active workflow, max4 instances/64 tasks/3 attempts,60 model/120 tool calls,
1800s and20% verification reserve stay authoritative in the existing services.
Do not change immutable recipe hashes or turn an earlier receipt into fresh
execution permission. Native test HTTP models are not live-LM/scientific proof.

Checked Tasks owner controls,2026-10-19: separate preview/preparation/approval/
stopped-parent replan/new-branch dialog, exact pending replay after reload and
independent shared Stop are wired to the live owner API. Native browser failures
also fixed catalogue cursor lifetime and stale completion-invalidation publication.
Final wheel4 owner scenarios +3 old result/material browser regressions pass;
frozen combined-brain219 source3 owner scenarios +3 old regressions pass. Counts
overlap. Native7tasks/8attempts/4model/49tools +1 separate probe reaches current
applied integration, preserves spending/deadline/owner bytes, then correctly revokes
currentness after an explicit owner edit. Keyboard/1440/390/320, Ruff3/mypy2,
JS3/package184/328 unchanged inputs/UTF-8 README/CLI pass. Only interface tests ran.
All failures are retained in [the report](../../validation/2026-10-19-owner-tasks-controls.md).
Automatic coordinator-process/UI/retry/restart combined acceptance, actual existing
child→new prerequisite UI execution, a gated negative-read race, aggregate/controller/
admission/neural/research/full/final gates remain open. No biological or live-LM claim.

Package/interoperability are checked; exact staging/publication is the remaining part of item7.

Publication item7 completed by public708051a. The helper-first and gated-race
follow-up are checked in [the report](../../validation/2026-10-19-owner-browser-acceptance.md).
