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

Verified MVP result: **166 tests passed, with no skips**. The acceptance tests
run the real agent with Docker/Postgres in both `bias` and `hard` modes and
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
