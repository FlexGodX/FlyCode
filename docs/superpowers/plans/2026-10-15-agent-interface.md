# Agent Interface Implementation Plan

**Goal:** A Russian local browser workspace that starts real FlyCode tasks and explains their live progress.

**Architecture:** FastAPI serves packaged, buildless HTML/CSS/JavaScript. Each run executes in its own worker thread, emitting append-only events; SSE replays events after reconnect. The existing CLI and agent policy remain the execution path. UI history is stored locally outside source repositories.

**Tech Stack:** Python, FastAPI, Pydantic v2, SSE, native JavaScript, CSS, inline SVG.

**Spec:** User request for an intuitive agent interface and visualization; `docs/Plan.md` §§3,23–29,53,66 define the execution and telemetry being displayed.

## Global Constraints

- Show action rationales and observable decision evidence, never claim to expose hidden model reasoning.
- Functional Fly is not a real biological connectome.
- Bind the UI to loopback; protect mutations from cross-origin requests. Repository selection stays within the explicitly configured workspace.
- Fixture runs use newly created disposable directories; never materialize fixtures over a selected repository.
- Preserve existing CLI behavior and the safety tournament.
- No frontend build process, external assets or cloud inference required.

## Review Focus

- Slow inference must leave HTTP, event streaming and stop requests responsive.
- A stop during inference must prevent the resulting action from executing.
- Browser refresh and server restart must preserve completed history; interrupted runs are labeled interrupted.
- Invalid, escaped and symlinked project paths must not escape the workspace.
- Model/tool failures must be visible; tool success alone must not be shown as verified task completion.

### 1. Live execution contract

Files: `src/flycode/agent.py`, `src/flycode/telemetry/events.py`, `src/flycode/llm/lmstudio.py`, `tests/integration/test_agent_events.py`.

- [x] Add a red test driving the actual heuristic toy repair, asserting ordered controller → selection → tool start → tool result events, action rationales and test evidence.
- [x] Add a red test requesting stop from candidate generation and asserting no tool action runs.
- [x] Implement optional `on_event: EventSink` and `should_stop: Callable[[], bool]` parameters. Emit model start/end around actual Qwen/Jev calls. Preserve defaults for existing callers.
- [x] Run `pytest tests/integration/test_agent_events.py tests/integration/test_local_cortex.py -q`.

### 2. Local run service

Files: `src/flycode/web/{__init__,runs,app}.py`, `src/flycode/cli.py`, `tests/unit/web/test_app.py`, `tests/unit/test_cli.py`.

Interfaces: `create_app(workspace: Path, data_dir: Path)`; `RunRequest(task, project, llm, max_steps)`; `/api/bootstrap`, `/api/models`, `/api/runs`, `/api/runs/{id}`, `/api/runs/{id}/events`, `/api/runs/{id}/stop`.

- [x] Write red tests for real demo repair through HTTP, event replay, persisted history, concurrent-run rejection, invalid task/project validation, foreign Origin and Host rejection, interrupted-history recovery and responsive stop during a slow model call.
- [x] Run tests and confirm missing web service fails.
- [x] Implement one active run, UUID directories, bounded event output, disk history and graceful stop; execute the existing agent in a background thread. Serve the local assets and expose available LM Studio models without inference.
- [x] Add `flycode ui --workspace PATH --port 8790` with loopback binding and a CLI parser test.
- [x] Run the targeted web and CLI tests.

### 3. Browser workspace

Files: `src/flycode/web/static/{index.html,app.js,styles.css}`, `tests/browser/agent-ui.cjs`.

- [x] Create a browser acceptance scenario: submit demo task, inspect successful run, action rationale, test output and before/after edit; reload and reopen history; check keyboard and narrow layout.
- [x] Build task sidebar, welcome composer, model connection indicator, timeline, activity indicator, circuit inspector, metrics and expandable tool/patch details. Use text nodes for all dynamic content.
- [x] Connect SSE replay, stop, new-task/history selection, project and execution settings. Display honest empty/error/disconnected states.
- [x] Verify screenshots before execution and after completion at desktop/mobile sizes. Run browser acceptance against the actual server.

### 4. Delivery

Files: `README.md`, `README.en.md`, `docs/quickstart.md`, `docs/handoff.md`.

- [x] Document one-command launch, project scope, local models, demo, stop behavior, persisted data and limitations in Russian and English.
- [x] Run all pytest tests, targeted Ruff, browser acceptance and `git diff --check`.
- [x] Verify static files are included in a built wheel; open the working UI and publish the completed change to the existing repository.
