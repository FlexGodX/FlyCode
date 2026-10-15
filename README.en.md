# FlyCode

**English** · [Русский](README.md)

FlyCode is an experimental autonomous coding agent. It reads project files,
proposes small changes, selects actions through a controller inspired by
computational mechanisms of the fruit fly brain, and verifies the outcome
using tests and application health checks.

**Status: working MVP through Phase 4.** The agent autonomously repairs a demo
FastAPI/PostgreSQL project, starts Docker Compose, applies Alembic migrations
and checks the API. Real biological connectome data and model training belong
to later phases.

## MVP capabilities

- Read, search, create and edit files; inspect Git status and diffs.
- Run tests and a limited set of Docker/Postgres/Alembic/HTTP tools.
- Select actions using Functional Fly or baseline controllers.
- Track working and episodic memory, novelty, repeated actions and outcomes.
- Block known dangerous actions through a safety mask.
- Complete tasks only after the required checks. Edits and stack restarts
  invalidate earlier readiness evidence.

The default **deterministic heuristic generator** needs no API key, but handles
only supported demo cases. An LLM can provide semantic reasoning for other
projects.

## Installation

The verified Python version is **3.12**. Docker is unnecessary for the basic
example. The service demo requires a running Docker Engine and Docker Compose.

```bash
git clone https://github.com/FlexGodX/FlyCode.git
cd FlyCode
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
flycode brains
```

In PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

## Agent interface

```bash
flycode ui --workspace . --port 8790
```

Open **http://127.0.0.1:8790**. The installed Python package is sufficient:
no Node.js, frontend build or separate server is needed. The interface is in Russian.

- **«Попробовать на демо» (Try a demo)** repairs a real toy bug without models.
- For your own task, select a project, describe the outcome and press the arrow
  or `⌘ Enter` / `Ctrl Enter`. Local Qwen + Jev are selected by default.
- The conversation shows action rationales, model calls, tools and verification
  results. Expand «Почему это действие» to compare candidates and Jev scores.
  The Changes tab shows successfully applied edits from this run.
- The inspector highlights the active stage and displays the controller mode,
  confidence, risk, steps and actual model requests. On narrow screens, open it
  using the top-right button.
- Model settings and the step budget are beside the task input. Stop waits for
  the current call; it prevents new actions and preserves edits already applied.

`--workspace` scopes project selection to that directory. To work across several
projects, pass their parent directory. **The agent edits the selected project's
files directly.** Demos always get separate directories and never recreate a
selected project. One task runs at a time. The server binds to loopback and is
not published to the internet.

History and events live in `~/.local/share/flycode/ui`; override this with
`--data-dir PATH`. Demo files are under `<data-dir>/<run-id>/repository`.
Reloading restores the event journal; unfinished tasks are marked interrupted
on server restart. Rationales are concise action explanations, not hidden model
chain of thought. The diagram represents Functional Fly and component calls,
not a biological connectome. Large tool outputs are clipped to 16,000 characters
in the browser journal.

## First run without an API key

```bash
flycode run \
  --repo /tmp/flycode-toy \
  --fixture toy_arithmetic_bug \
  --task "Fix the bug" \
  --brain functional \
  --control bias
```

The agent creates a small project containing a bug, repairs it and runs its
tests. A successful run prints `success=True` and exits with code `0`.
Use `--fixture toy_import_bug` for a missing-import example.

**The directory supplied with `--fixture` is recreated. Use a separate
temporary directory, not an existing project.** On Windows, replace `/tmp/...`
with a path to your own temporary directory.

## Demo: repair and launch FastAPI + PostgreSQL

Make sure Docker is running and host port **8000** is available. PostgreSQL
stays inside the Compose network; host port 5432 does not need to be free.
The first run needs internet access to download images and dependencies.

```bash
docker info
docker compose version

flycode run \
  --repo /tmp/flycode-environment \
  --fixture fastapi_postgres_app \
  --task "Fix this FastAPI application and get it fully running" \
  --brain functional \
  --control bias \
  --max-steps 60 \
  --max-tool-calls 60

curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/users
```

The demo deliberately contains a wrong port mapping, migration path,
environment-variable reference, API route and Python import. The agent derives
repairs from files it reads and tool results. It then builds the containers,
checks PostgreSQL, runs migrations and tests inside the application container,
and probes `/health` and `/users` over HTTP.

The application remains running after the agent exits. Stop it with:

```bash
docker compose --project-directory /tmp/flycode-environment down
```

## Local Qwen + Jev through LM Studio

FlyCode supports two local models with distinct roles:

- `qwen/qwen3.5-9b` proposes concrete actions.
- `jev-style-qwen3.5-2b-decision` scores safe options using next-token
  probabilities in its native decision format.
- The fly controller schedules inference and retains final action selection.
  This is the current Functional Fly; a real biological connectome is not connected yet.

If the models are already downloaded, load them and enable the local server:

```bash
lms load qwen/qwen3.5-9b --context-length 8192 -y
lms load jev-style-qwen3.5-2b-decision --context-length 4096 -y
lms server start --port 1234

flycode run \
  --repo /tmp/flycode-local \
  --fixture toy_arithmetic_bug \
  --task "Fix the off-by-one bug in add and verify the tests" \
  --brain functional \
  --llm lmstudio \
  --base-url http://127.0.0.1:1234/v1 \
  --model qwen/qwen3.5-9b \
  --decision-model jev-style-qwen3.5-2b-decision \
  --request-timeout 120 \
  --max-model-calls 30 \
  --telemetry-dir /tmp/flycode-local-telemetry
```

Do not restart the server if it is already running. Model identifiers come
from LM Studio's API; a mismatch produces an error listing available IDs.
The names above are the defaults for `--llm lmstudio`. `LMSTUDIO_BASE_URL`
sets the endpoint and `LMSTUDIO_API_KEY` supplies authentication if enabled
in LM Studio. There is no automatic cloud fallback.

The controller skips inference on verified completion, a controller block,
or simple local test verification in VERIFY mode. Confidence of at least 0.8
skips Jev. In `hard` mode the controller owns the ranking, so Jev is also
skipped. The safety mask applies independently of model scores. A Jev failure
retains Qwen proposals with a diagnostic; a Qwen failure stops the agent without
inventing a repair. Requests have timeouts, token caps and a shared call budget;
there are no automatic retries.

Output includes `[models]` routes and `model_calls` counters. Telemetry stores
routing, Jev scores and errors. Consolidation does not make an extra model
request. Explicitly enable acceptance against installed models with:

```bash
FLYCODE_TEST_LMSTUDIO=1 python -m pytest tests/e2e/test_lmstudio.py -q
```

[Integration plan](docs/superpowers/plans/2026-10-15-lmstudio-dual-model.md) ·
[Jev v1 protocol](https://huggingface.co/chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-GGUF)

## Existing projects and an LLM

Set `OPENAI_API_KEY` in your environment, then run:

```bash
flycode run \
  --repo ./my-project \
  --task "Fix the failing tests" \
  --brain functional \
  --llm openai
```

Select a model with `--model`; the current default is `gpt-4o-mini`.
Availability and costs depend on the API and model you choose.
Do not pass `--fixture` for an existing project.

The agent modifies files and can invoke infrastructure tools. Use a separate
working copy and a development environment. The safety mask provides additional
checks; it is not an isolation boundary for untrusted code.

## Controllers and modes

| `--brain` value | Purpose |
|---|---|
| `functional` / `functional_fly` | Functional Fly: Task Compass, associative memory, novelty and outcome signals |
| `mock` | Test controller |
| `heuristic_fsm` | Finite-state-machine baseline |
| `random_reservoir` | Random-reservoir baseline |
| `generic_rnn` | Recurrent-network baseline |
| `frozen_connectome` / `frozen_full_connectome` | Experimental random projections; a real connectome is not implemented yet |

`--control bias` is the default: the fly controller influences candidate
ranking. `--control hard` selects an action using controller scores.
The safety mask applies in both modes.

## Agent loop

```text
Observation → controller → action candidates → safety mask
            → action selection → tool → next observation
            → verification → completion
```

An LLM or the limited heuristic generator handles semantic reasoning. The
lightweight controller maintains strategy state and influences action
selection. Tests, services and migrations provide verifiable feedback.

## Verification

```bash
python -m pytest -q
python -m pytest tests/e2e/test_environment_autonomy.py -q
```

Main suite: **206 tests passed**; LM Studio acceptance is enabled separately.
The Docker acceptance tests run the real agent with Docker/Postgres in both `bias` and `hard` modes and
check HTTP responses and the Alembic revision stored in the database. They
choose an available HTTP port and clean up their containers afterward. Without
Docker/Compose, those two cases are explicitly skipped.

Repository-wide Ruff/mypy checks are not clean yet: style and typing findings
remain. See the [project status](docs/handoff.md) for details.

## Limitations and roadmap

- The heuristic generator is not a general-purpose code repair system.
- An advantage over comparable FSM/RNN controllers has not been demonstrated.
- A real connectome, constrained training, distillation, reduced models and
  Hermes integration are outside the completed MVP.
- `train`, `distill`, `benchmark`, `lesion` and `sensitivity` are explicit CLI
  stubs. `--brain full` and `--brain reduced` are not supported.

## Documentation

- [Quick start and Python API](docs/quickstart.md)
- [Implementation plan](docs/Plan.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety.md)
- [Research](docs/research.md)
- [Verified status and remaining work](docs/handoff.md)
