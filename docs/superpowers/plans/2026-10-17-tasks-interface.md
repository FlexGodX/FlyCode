# Canonical Tasks interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offer a working canonical task board, searchable list, actual agent activity and durable draft/comment/command interface while preserving both chat interfaces.

**Architecture:** `/tasks` is a separate packaged page using FlyCode's existing visual tokens. It reads protected canonical APIs with the bootstrap token, never reconstructs a lifecycle from legacy runs, and refreshes projections after authenticated SSE notifications. All mutations carry fresh server revisions and unique idempotency keys.

**Tech Stack:** Existing FastAPI, HTML/CSS/JavaScript, real Chromium browser acceptance.

**Spec:** `docs/specs/task-tracker.md` TRK-01–04 and §§100–104 in `docs/Plan.md`. This increment does not claim the complete tracker/runtime/verification phase.

## Global Constraints

- Preserve `/` and `/desktop`, including personal model defaults and independent projectless chats.
- Draft saves perform no inference or project tools; canonical project registration is explicit on create.
- Never synthesize running agents, progress percentages, successful completion or stop confirmation.
- Mutations use server IDs, token, origin and revision checks; literal user text uses textContent.
- Native folder selection is available through the existing project flow; no path typing requirement.

## Review Focus

1. A delayed response cannot overwrite a newer selection/filter; serialize refresh and bind detail rendering to current task ID.
2. SSE snapshot frontier differs from delivery cursor; only delivered SSE IDs advance reconnect history.
3. Server conflict leaves the mutation unconfirmed and refreshes current revision without silently retrying user intent.
4. All pages of tasks/history remain reachable; filters must not hide older matching tasks accidentally.
5. 320/390px, keyboard dialogs and focus, offline/stale status, literal comments remain usable.

### Task 1: Board, list, actual agents and durable drafts

**Files:** Create `src/flycode/web/static/tasks.html`, `tasks.css`, `tasks.js`; modify `src/flycode/web/app.py`, `src/flycode/web/static/index.html`; create `tests/browser/tasks.cjs`.

**Interfaces:** Consume `/api/bootstrap`, protected `/api/tasks/projects`, `/api/tasks`, `/api/tasks/{id}`, `/commands`, `/comments`, `/events`, `/api/agents/activity`. Produce GET `/tasks` and sidebar navigation from both existing interfaces.

- [x] Write and run real browser acceptance before implementation. Follow the existing sidebar link, create a non-Git draft, reload and compare server IDs/revisions, search/filter, switch board/list/agents, verify no instances/model/tool calls, save literal comments, observe an external HTTP comment and a stale revision, cancel without fake completion, inspect desktop/390/320 screenshots.
- [x] Implement the packaged page with existing design tokens and separate styles; register the route and navigation only. Expose server-derived state/blockers, current attempt, profile snapshot, spend, last action and paginated event history. Use native dialog forms and explicit revision-checked commands; no drag-only interactions.
- [x] Run the same browser acceptance, existing desktop/classic/projectless/journal checks as relevant, inspect screenshots, and preserve all failed outcomes. Freeze bytes before final checks and publish a bounded report/commit only after verification.

Runtime hosting, owner stop authorization after source removal, verification execution/artifact endpoints, complete criteria editing and research admission remain separately tracked in the aggregate plan; no placeholder controls are presented for unavailable operations.

Main follow-up adds real lost-reply draft/comment replay, clears accepted transport errors,
and excludes unsupported blocked-task retry. Retained browser RED/GREEN evidence and
limits: [validation](../../validation/2026-10-17-tasks-interface.md).
