# Canonical worker custody Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve exact local-worker stop proof across coordinator failure without releasing unknown spending.

**Architecture:** Reuse the existing guardian/custody protocol with an asynchronous launch handle. Bind an immutable process reference before spawn, forward only hash-bound RPC/control FDs, and reconcile the exact saved reference/fence outside SQL before atomic settlement. Keep unknown external calls held.

**Tech Stack:** Existing Python/Pydantic/SQLite/POSIX custody and native pytest.

**Spec:** Plan §§100/103/104, ORC-08/09; aggregate specification delivery Task5.

## Global Constraints

- No persisted-PID signals or automatic upgrade of old uncertain workers.
- Preserve current budgets, deadlines, stderr bounds and actual child diagnostics.
- No inference or filesystem custody reads inside SQLite transactions.
- Custody GO creates the process; agent admission requires ready RPC after host thread readiness.
- No unique host daemon or Tasks5–8 completion claim.

## Review Focus

1. Before-GO coordinator death must yield exact no-start proof or remain uncertain.
2. Guardian death/changed ownership objects cannot release a live assignment.
3. A stopped local worker cannot settle unknown HTTP/pytest effects.
4. Child FD copies must close on normal, cancelled and partial-start paths.
5. Old fault injection must reach the actual child while preserving limits.

### Task 1: Durable worker process admission and recovery

**Files:** Modify `verification/custody.py`, `domain/{verification,orchestration}.py`, `orchestration/{runtime,store}.py`; new `tests/unit/orchestration/test_runtime_worker_custody.py`; narrowly adapt existing `test_runtime_process.py` fixtures.

**Interfaces:** `custody.launch(...) -> Launch`; existing `custody.run(...)` remains blocking. `WorkerRecord.process: ProcessRef | None`; `Runtime.reconcile(scope, attempt_id)` validates stopped custody; `ActualWorkerProcessView` exposes actual PID/terminal diagnostics only.

- [x] Three actual native feature REDs before implementation, with exact owned processes and retained evidence.
- [x] Hash-bound FD forwarding, pre-spawn durable binding, ready admission and fail-closed startup cleanup.
- [x] Native GO/crash/corruption/late HTTP/FD/stderr cases; unknown spending held.
- [x] Isolated176 pass; transfer immutable patch after independent hash audit.
- [x] Main exact176 pass; Ruff7/mypy52;15 extracted-wheel native cases and CLI10.
- [x] Publish verified commit and archive regular-file evidence; aggregate Tasks5–8 stay open.

[Validation](../../validation/2026-10-17-worker-custody.md) preserves failed feature,
fixture and packaging-audit outcomes separately.
