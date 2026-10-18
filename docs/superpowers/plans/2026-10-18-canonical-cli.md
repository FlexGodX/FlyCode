# Canonical Tasks CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Expose the canonical task queue and shared coordinator through the public `flycode tasks` CLI, independently of the web process.

**Architecture:** A trusted local CLI adapter calls the existing Projects, TrackerService, Store and coordinator attachment APIs. Explicit fresh project paths issue ordinary owner scope; a fixed local-operator actor retains only prior Stop authority after folder loss. The adapter never claims workers, starts inference or invents a second queue.

**Tech Stack:** Existing Python >=3.10, argparse, Pydantic v2 and SQLite; actual CLI/application/coordinator subprocess tests.

**Spec:** `docs/Plan.md` §§100–104; `docs/specs/task-tracker.md` TRK-02/03/07/09, TT-01/04/09/14; aggregate Task8 in `2026-10-16-specification-delivery.md`. Continue the user-authorized native execution method. Common verification access, remaining Tasks5–8 and scientific §§90–99 remain required.

## Global Constraints

- One active workflow, max4instances/64tasks/3attempts/60modelcalls/120tools/30min; reserve20% verification.
- Use the same default state path/environment as web; no new database schema or mutable queue.
- No SQLite transaction during physical-folder inspection, inference, socket or process work.
- Trusted same-OS-user local operator only; never accept actor/role/project-ID scope from input JSON.
- Registration and ordinary access require an explicit current local folder; Git is not required.
- Stop discovery/commands use only existing prior-resolution grants; no reads/resume after root loss.
- Mutation keys and expected revisions are supplied explicitly; no automatic conflict retry or budget reset.
- Preserve legacy `run`, `verify`, model defaults and existing app/CLI behavior.

## Review Focus

1. CLI/web must see the exact same task IDs/revisions/events, including after process restart.
2. Foreign IDs, replaced folders and never-resolved Stop handles must not expose task content or grant execution.
3. Repeating an unchanged command/comment/create request must not duplicate rows/events; changed payload with the same key must fail.
4. Malformed, oversized, nonfinite, extra-field and FIFO input must fail promptly before task/grant side effects, without echoing private JSON.
5. CLI exit must preserve the detached Host; Stop during actual review/integration must return promptly while retaining unknown effects and spending.

### Task1: Public canonical commands

**Files:** Modify `src/flycode/cli.py`; create `src/flycode/tracker/cli.py` and `tests/unit/orchestration/test_tracker_cli.py`.

**Interfaces:** `configure_parser(subparsers)` registers `tasks`; `tasks_command(args) -> int` executes it. `--database PATH` is optional before the verb, otherwise `default_state_path()`. Ordinary verbs require `--project PATH`. Success emits JSON stdout; operational/input rejection emits a sanitized JSON error to stderr and returns2.

Commands:

```text
flycode tasks register --project PATH
flycode tasks create --project PATH --input TASK_DRAFT_JSON --key KEY [--workflow ID] [--workflow-budget WORKFLOW_BUDGET_JSON]
flycode tasks list --project PATH [--after N] [--limit N] [--state STATE] [--query TEXT] [--archived]
flycode tasks show ID --project PATH
flycode tasks command ID --project PATH --input TASK_COMMAND_JSON
flycode tasks comment ID --project PATH --text TEXT --key KEY
flycode tasks events --project PATH [--task ID] [--after N] [--limit N]
flycode tasks agents --project PATH
flycode tasks stops [--after N] [--limit N]
flycode tasks stop ID --operation pause|cancel --expected-revision N --key KEY
flycode tasks service status|attach
```

`create --input` consumes the existing `TaskDraft` schema; its optional workflow budget consumes `WorkflowBudget`. `command --input` consumes the complete existing `TaskCommand`, preserving operation/revision/key/changes for exact replay. A child workflow creation cannot also supply a new workflow budget. Limits are1..100, cursors0..2**63-1; task/workflow IDs are32 lowercase hex. Input files must be regular and at most1MiB; reads must not block on FIFOs. Ordinary commands reuse current folder scope and canonical service validation. Stop returns only `TaskStopReceipt`.

- [x] RED: run actual `python -m flycode.cli tasks ...` subprocesses; public parser currently rejects `tasks`. Create/read through CLI and protected native web; assert exact shared IDs, one immutable create replay, no attempts/model/tool rows, preserved non-Git owner bytes.

```python
created = cli('create', '--project', str(source), '--input', str(draft), '--key', 'draft')
replayed = cli('create', '--project', str(source), '--input', str(draft), '--key', 'draft')
assert created['task']['id'] == replayed['task']['id']
assert web.get('/api/tasks/' + created['task']['id']).json()['task']['id'] == created['task']['id']
```

- [x] RED: literal incomplete/fully specified drafts prove enqueue gating; metadata changes, comments and keyset events prove revision conflicts, exact replay and no duplicate delivery. Child creation remains in the chosen workflow and foreign workflow/task scope is rejected.
- [x] RED: replace the source after CLI registration and prior task resolution; normal reads/resume deny, `stops` returns four fields and `stop` replay works across CLI process restarts. A project registered only by another actor grants no CLI Stop until fresh CLI resolution.
- [x] RED: invalid command/task/budget data, out-of-range cursors, malformed IDs, conflicting child/budget flags, unknown fields and nonregular input return2; no sensitive input echo or canonical effects.
- [x] GREEN: implement the parser and focused adapter with bounded regular-file reads, strict existing models, fixed local actor, fresh physical resolution and existing Store/Tracker commands. Keep database closure in `finally` and status checks outside Store initialization.
- [x] Run the new focused tests plus existing CLI/tracker/API checks and relevant Ruff/mypy; retain actual RED and passing logs/XML.

### Task2: Native lifecycle and checked publication

**Files:** Extend only `tests/unit/orchestration/test_tracker_cli.py`; update README RU/EN, Plan, handoff and validation report.

**Interfaces:** Existing `attach_coordinator`, `CoordinatorClient`, `managed_pipeline`, `native_app`, `owned_service_control` and `stopped(ProcessRef)` retain their contracts. Test-only inherited control may ask the exact daemon to signal itself; never signal a discovered PID.

- [x] RED: actual CLI `service status` on missing/stopped state returns sanitized unavailability and does not create/start Host. Explicit CLI attach launches the real detached service; after that CLI process exits, another CLI and actual web process observe the same four-field session. No task execution approval or model loading occurs.
- [x] GREEN: route status/attach through the existing authenticated coordinator client/launcher; client context closure preserves Host.

```python
with attach_coordinator(database) as client:
    session = client.status()
# Closing this client never closes the coordinator.
with CoordinatorClient(database) as other:
    assert other.status() == session
```

- [x] Actual CLI Stop during slow review and integration, pause/cancel with present/replaced roots: latency<1s, exact process-stop proof, spending/deadline unchanged, unknown tool reservation held, no new model calls and preserved original/replacement bytes.
- [x] Freeze final Python/static/tests/config/README bytes; run complete registered pytest, relevant Ruff/mypy, real FlyWire and bounded English real-model pipeline, build/extract wheel and exercise actual new CLI commands from the distribution. Preserve legacy browser checks for shared CLI/bootstrap composition.
- [x] Reconcile exact case IDs, audit immutable evidence and archive regular files; update acceptance status honestly and publish checked commit/push to public FlexGodX/FlyCode. This increment does not close the overall goal.

### Findings before publication

- [x] Reproduce the review finding with an actual CLI input containing10,000 nested arrays (20,001bytes). Catch decoder/encoder recursion only inside `_json`; return the existing sanitized input error before opening state. The initial suggested depth2,000 does not reproduce on the measured Python3.12.13.
- [x] Preserve the failed real-model pipeline: its explicit probe completed, but changing native `remaining_ttl_seconds` invalidated the whole-instance fingerprint before the first workflow model request. Add an actual HTTP regression proving countdown/presence changes preserve the admitted configuration and persisted profile, while context/instance changes still revoke it. Exclude only that transient countdown from the fingerprint; no automatic reload, probe or fallback.
- [x] Finish the currently frozen full/live checks before changing source/tests. Preserve their outcomes as pre-correction evidence, then freeze and rerun final checks against the corrected source and extracted distribution.

Final measured evidence and remaining scope: [report](../../validation/2026-10-18-canonical-cli.md). Publication receipts are retained outside Git after push.
