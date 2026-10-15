# FlyCode Quick Start

## Install

Python 3.12 is recommended for the current checkout.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
flycode brains
```

## Run an offline repair

```bash
flycode run --repo /tmp/flycode-toy --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain functional --control bias
```

Fixture destinations are recreated on every run. Use a disposable directory.
The default heuristic cortex needs no API key. It supports the toy repairs and
narrow, file-based rules for the environment demo below. Use the LLM cortex for
other problems:

```bash
# Set OPENAI_API_KEY in your environment first.
flycode run --repo ./my-project --task "Fix the failing tests" \
  --brain functional --llm openai
```

Available baseline names are `mock`, `heuristic_fsm`, `random_reservoir` and
`generic_rnn`; `functional` selects Functional Fly. `--control hard` selects
hard control instead of the default `bias`. `frozen_connectome` is an
experimental random-projection backend; it does not implement a real biological
connectome. Phases 5–14 and the train/distill/benchmark/lesion/sensitivity CLI
commands remain outside the completed working version.

## Phase 4: FastAPI + Postgres

Requires a running Docker daemon, the Compose plugin, network access for the
first image build, and a free host port 8000. Postgres stays inside the Compose
network and does not require host port 5432.

```bash
docker info
docker compose version
flycode run --repo /tmp/flycode-environment --fixture fastapi_postgres_app \
  --task "Fix this FastAPI application and get it fully running" \
  --brain functional --control bias --max-steps 60 --max-tool-calls 60
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/users
```

The agent reads the project, repairs its configuration and code, builds the
stack, checks Postgres, applies Alembic migrations, runs the application's tests
inside its container and checks both HTTP endpoints. Edits invalidate previous
verification. Completion requires all these checks; passing tests alone is
insufficient. The service stays running after the CLI exits.

Stop the demo when finished:

```bash
docker compose --project-directory /tmp/flycode-environment down
```

The real acceptance test chooses an available HTTP port and cleans up its own
containers. It runs the agent with both bias and hard control:

```bash
python -m pytest tests/e2e/test_environment_autonomy.py -q
python -m pytest -q
```

Without Docker/Compose, the acceptance cases are skipped explicitly.

## Python API

```python
import asyncio
from pathlib import Path

from flycode import (
    HeuristicCandidateGenerator,
    SoftwareEnvironment,
    TaskSpec,
    make_brain,
    run_agent,
)


async def main():
    task = TaskSpec(
        id="example",
        task="Fix the bug",
        fixture="toy_import_bug",
        max_steps=25,
        seed=42,
    )
    result = await run_agent(
        SoftwareEnvironment(Path("/tmp/flycode-api")),
        make_brain("functional", seed=42),
        HeuristicCandidateGenerator(),
        task,
    )
    print(result.success, result.metrics.verified_completion)


asyncio.run(main())
```

For an existing service repository, pass explicit completion targets through
`TaskSpec(success_criteria={"database": True, "service_urls": [...]}, ...)`.
Compose files and `alembic.ini` automatically add stack and migration checks.
Tool calls accept a `service` argument for running tests/migrations inside a
Compose service. Read the repository's configuration to choose that service.

See `docs/Plan.md` for the authoritative roadmap and `docs/handoff.md` for the
verified project state.

## Two local models in LM Studio

The `lmstudio` cortex uses the installed `qwen/qwen3.5-9b` reasoner and the
`jev-style-qwen3.5-2b-decision` v1 decision scorer. The scorer requires its
single-token options/logprobs protocol; it is not interchangeable with a chat
planner or the Jev v3 runtime. The existing brain schedules inference and
retains final action arbitration.

```bash
lms load qwen/qwen3.5-9b --context-length 8192 -y
lms load jev-style-qwen3.5-2b-decision --context-length 4096 -y
# Start the server if it is not already running:
lms server start --port 1234
flycode run --repo /tmp/flycode-local --fixture toy_arithmetic_bug \
  --task "Fix the off-by-one bug in add and verify the tests" \
  --brain functional --llm lmstudio --max-model-calls 30 \
  --request-timeout 120 --telemetry-dir /tmp/flycode-local-telemetry
```

Optional flags: `--base-url`, `--model`, `--decision-model`. The local endpoint
is `http://127.0.0.1:1234/v1` by default. `LMSTUDIO_BASE_URL` overrides the
default endpoint; `LMSTUDIO_API_KEY` supplies authentication when enabled.
`OPENAI_API_KEY` and cloud endpoint settings are not used for this mode.

Routing is explicit: verified STOP and simple non-Compose VERIFY checks skip
both models; a blocked controller requests neither; confidence >= 0.8 skips
Jev; hard control skips its unused scoring request. Qwen proposes up to four
alternatives, Jev scores only safe candidates, and final safety masking still
applies. Jev errors retain Qwen proposals and are reported; Qwen errors stop
without substituting an invented fix. Budgets count failed requests too;
consolidation makes no inference request. Counts reset for each `run_agent` run.

Inspect `model_calls` / `model_errors` in `runs.jsonl` and `model_routing` in
`run.steps.jsonl`. Qwen's local request uses JSON-schema output, a 1536-token
response cap and disabled reasoning output; Jev requests exactly one token.
No automatic retries or cloud fallback are performed. Model probabilities are
advisory scores, not a safety guarantee or measured code-repair calibration.

The live acceptance is opt-in so ordinary tests need no loaded models:

```bash
FLYCODE_TEST_LMSTUDIO=1 python -m pytest tests/e2e/test_lmstudio.py -q
```
