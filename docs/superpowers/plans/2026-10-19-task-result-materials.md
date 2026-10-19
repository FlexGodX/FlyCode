# Task Result Materials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Expose exact saved patch versions and verification reports from canonical task results through one scoped service, CLI, HTTP and a lazy Tasks disclosure.

**Architecture:** Derive opaque material IDs from scoped persisted result records. Resolve runtime roots from recorded bindings/executions, never request paths. Read known regular files through owned directory descriptors with bounds and expected hashes; historical immutable materials do not assert current task success.

**Tech Stack:** Python/Pydantic v2, existing SQLite Store, FastAPI and plain Tasks JavaScript.

**Spec:** Plan §§103–104; 2026-10-18-dependent-execution.md Task5; specification-delivery.md Task8.

## Global Constraints

- Keep one active workflow; max4instances,64tasks,3attempts,60modelcalls,120tools,30min, reserve20% verification.
- No SQL transaction during physical inspection, inference or download.
- Do not change saved contracts/schema, owner grants, worker results or oracle authority.
- Preserve parallel brain changes; implement against clean public eb39455 in a private source snapshot.
- Run affected feature checks only; retain all RED, preparation and failed verification boundaries.
- Requests contain task/material IDs only; no source/runtime/oracle paths, argv, filenames or destination writes.
- CLI content is base64 JSON; HTTP downloads are octet-stream attachments with sandbox/nosniff headers.

## Review Focus

- Same-project unrelated task material IDs must not authorize reads.
- Leaf/ancestor symlinks, FIFO, hard links, replaced paths and oversized files must fail closed without blocking.
- Manifest or file hashes must match the persisted artifact/verdict; failure cannot become a green task state.
- Native source changes may stale completion but do not erase intact historical materials.
- Mobile/keyboard disclosure and literal filenames must not turn report bytes into active HTML.

### Task 1: Native material catalogue and exact reads

**Files:** create tracker/materials.py; modify tracker/service.py; test tests/unit/orchestration/test_task_result_materials.py.

**Interfaces:** `TrackerService.materials(scope, task_id) -> MaterialPage`; `TrackerService.material(scope, task_id, material_id) -> (MaterialView, bytes)` delegates to ResultMaterials. MaterialView has server ID, kind, record ID, stage, relative display name and SHA-256; page has task ID and items. No paths/credentials/environment manifests are exposed.

- [x] RED: actual canonical Host prepares a parent, completes a child and independent review; list/read baseline `value = 0\n`, target `value = 1\n`, retained JUnit, reject another task ID and foreign scope. Assert no new expenditure or source write.
- [x] GREEN: snapshot metadata under SQL, resolve artifact via its recorded attempt binding and reports via scoped review execution/integration records. Enumerate only changed baseline/target files and allowlisted attempt stdout/stderr/JUnit/case files. Verify manifest hash before use and selected bytes before return. Use owned/no-follow directory descriptors and bounded regular reads. Recheck recorded scope and metadata after physical reads.
- [x] Add real corruption tests for tamper, special files, links, oversized content, removed evidence and stable historical reads; make only required corrections.

### Task 2: Public CLI/HTTP/browser

**Files:** tracker/cli.py, web/tasks.py, web/static/tasks.js and tasks.css; tests/browser/task-materials.cjs; same native test module.

- [x] RED: `tasks materials TASK --project`, `tasks material TASK MATERIAL --project` and protected `/api/tasks/TASK/materials[/MATERIAL]` agree with the native service; missing token/origin/foreign task deny access and no clients pass paths.
- [x] GREEN: reuse existing project scope and error mapping. CLI returns item plus base64 content. HTTP returns exact bytes as an attachment, no inline report rendering. Lazy disclosure lists literal labels and download links; do not make a second result lifecycle.
- [x] Browser RED/GREEN: open disclosure with keyboard, download a real target and JUnit, inspect desktop/390/320 screenshots/no overflow and reject failed download visibly. Existing Tasks UI remains usable.

### Selected-read retention and stale callback correction

Material IDs contain the immutable server record ID plus a task/name/hash digest.
A selected read snapshots only that record's provenance; unrelated integration
progress cannot invalidate saved bytes. The catalogue reports unavailable record
IDs alongside intact materials, without claiming their currentness. Missing old
manifests must not block a later intact patch. Add a real native regression before
this correction. Binding corruption tests select the material owned by the
corrupted record, not an unrelated patch.

Browser errors from an earlier detail must check node/task relevance before
hiding the active detail. Delay actual HTTP catalogue/download responses, switch
to another real task, return403 and keep that new task's identity and Stop actions.
The new regression fails before applying the guards.

### Task 3: Checked publication

- [x] Run the collected feature cases, targeted compatibility, scoped Ruff/mypy, isolated wheel/extracted CLI, and frozen parallel-brain interoperability. Inspect screenshots and full logs.
- [x] Read-only independent review; fix substantive findings with real regression evidence. Archive only regular evidence files with source/destination hashes and inventory.
- [x] Update README RU/EN, Plan/handoff/delivery/subplan and truthful report. Stage only checked own paths through a separate index, verify whole package matches measured wheel, commit/push exact SHA. Owner approval/replan adapters and aggregate/controller/neural/research/final gates remain open.

## Self-review

Scope/hash/path races are owned by Task1; public authority, literal UI and actual downloads by Task2. Task3 pins source/package and excludes parallel brain code. Native orchestration and historical currentness use existing public modules; no new verification pass claim or execution authority is introduced.
