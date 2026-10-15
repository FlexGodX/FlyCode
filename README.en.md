# FlyCode

**English** · [Русский](README.md)

FlyCode is an experimental autonomous coding agent. It reads project files,
proposes small changes, selects actions through a controller inspired by
computational mechanisms of the fruit fly brain, and verifies the outcome
using tests and application health checks.

**Status: working MVP through Phase 4.** The agent autonomously repairs a demo
FastAPI/PostgreSQL project, starts Docker Compose, applies Alembic migrations
and checks the API. Real FlyWire, a reduced circuit and offline training
workflows are available; research acceptance results are listed below.

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
not a biophysical simulation. Large tool outputs are clipped to 16,000 characters
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

## Real FlyWire connectome — Phase 5

```bash
python -m pip install -e '.[research]'
flycode connectome download
flycode connectome prepare --source ~/.cache/flycode/flywire-783
flycode connectome info
flycode run --repo /tmp/flycode-full --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain full
```

The initial download retrieves two official files (~853 MB) and verifies their
checksums. Source: [FlyWire Consortium v783, CC BY 4.0](https://zenodo.org/records/10676866).
Data stays in the local cache and is not distributed in this repository.

The prepared graph contains all **139,255** released proofread neurons and
**2,700,513** directed connections after summing neuropils and applying a
5-synapse threshold. Use `--connectome-path PATH` or `FLYCODE_CONNECTOME_PATH`
for another prepared artifact. Missing data fails explicitly; there is no
random-topology fallback.

Select «Нейронный контроллер → FlyWire 783» in the UI settings. The same Qwen
and Jev agents receive the connectome controller's state for call routing and
action scoring. In the roadmap, Hermes means FlyCode itself: the application
hosting these agents.

This is a research model: measured topology drives approximate recurrent rate
dynamics with engineered input/readout mappings and an initial workflow prior.
Signs follow neurotransmitter predictions: GABA/GLUT inhibitory, others
excitatory. This convention is not a complete biophysical simulation. The
legacy `frozen_connectome` remains a random-projection comparison. No advantage
over Functional Fly has been established; training and reduction measurements
are listed below.

## Offline training, graph reduction and distillation

First record successful runs. Training consumes **every** executed action in
its input directory: inspect the recordings before use. Files and resets of
the step number separate episodes; recurrent state does not leak across resets.

```bash
flycode run --repo /tmp/flycode-teacher-add --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain functional --telemetry-dir ~/.cache/flycode/training-records
flycode run --repo /tmp/flycode-teacher-import --fixture toy_import_bug \
  --task "Fix the bug" --brain functional --telemetry-dir ~/.cache/flycode/training-records

flycode train --trajectories ~/.cache/flycode/training-records \
  --output ~/.cache/flycode/checkpoints/constrained
flycode run --repo /tmp/flycode-trained --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain full --checkpoint ~/.cache/flycode/checkpoints/constrained

flycode connectome reduce --source ~/.cache/flycode/flywire-783/prepared \
  --neurons 4096 --output ~/.cache/flycode/reduced-4096
flycode run --repo /tmp/flycode-reduced --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain reduced --connectome-path ~/.cache/flycode/reduced-4096

flycode distill --trajectories ~/.cache/flycode/training-records \
  --teacher-checkpoint ~/.cache/flycode/checkpoints/constrained \
  --output ~/.cache/flycode/checkpoints/student
# Only after distill passes validation:
flycode run --repo /tmp/flycode-student --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain functional --checkpoint ~/.cache/flycode/checkpoints/student
```

`train` searches recurrent gain and leak rate and fits tool readout scores.
Adjacency remains unchanged. Input/readout arrays, dynamics and checksums are
saved in a separate checkpoint. `training.json` reports before/after imitation
error and accuracy. These are in-sample measurements, not evidence of better
end-to-end task completion.

`connectome reduce` selects a connected induced circuit and inherits its
teacher's corresponding input and output mappings. It never invents edges.
Use `--checkpoint PATH` to inherit a trained parent's parameters.

`distill` transfers **tool biases** into a small Functional Fly readout;
behavior mode arbitration is retained. Entire episodes are held out. Defaults
require RMSE ≤ 0.1 and top-tool agreement ≥ 90%; `--max-rmse` and
`--min-agreement` configure tolerances before an experiment. Failed validation
returns exit code 1 and saves a report; the rejected checkpoint cannot be
enabled. These research workflows are currently exposed through the CLI.

The v2 student uses 784 parameters and quadratic features; existing linear
checkpoints remain compatible. Validation replays the actual controller,
including its state and action history.

Measurements: two toy repositories, seeds 0/1/2, both `bias` and `hard`.
The [results report](docs/research-results/2026-10-15-adaptation.json) records conditions and measurements.

| Check | Result |
|---|---|
| Phase 6, standard generator | Frozen and trained: 12/12, 5 steps each |
| Phase 6, alternative-action probe | Frozen: 10/12, mean 6.67 steps; trained: 12/12, 5 steps |
| 4,096-neuron circuit, 12,031 edges | 4/4 successful repairs, 5 steps each |
| Full / reduced controller step | 7.11 / 0.12 ms; arrays 30.17 / 0.38 MiB |
| Phase 8, Functional Fly distillation | Held-out episode: RMSE 0.01679, agreement 100%; gate passed |
| Student task executions | 12/12; original Functional Fly also 12/12, 9 steps each |

Phases 6–8 pass limited acceptance on these data. The Phase 6 probe preserves
normal proposals and adds read-only `run_tests`/`git_diff` alternatives during
EXECUTE. The difference occurs at seed 0 in hard control; other seeds show
no advantage. Each checkpoint was compared with its own frozen initialization.
The Phase 8 gate covers one held-out episode. These results establish neither
unseen-repository generalization nor superiority over compute-matched FSM/RNNs.

## Persistent learning and research checks

Phase 10: Functional Fly learns only from executed outcomes. Learned memory
persists across tasks. MB/RPE updates, memory size and policy change are bounded;
a replay check rejects harmful updates and restores the policy snapshot.
Learning is disabled by default.

```bash
flycode run --repo /tmp/flycode-learn-add --fixture toy_arithmetic_bug \
  --task "Fix the bug" --learning-memory ~/.cache/flycode/experience
flycode run --repo /tmp/flycode-learn-import --fixture toy_import_bug \
  --task "Fix the bug" --learning-memory ~/.cache/flycode/experience

flycode benchmark --config docs/examples/benchmark.json \
  --output ~/.cache/flycode/benchmarks
flycode lesion --trajectories ~/.cache/flycode/training-records \
  --checkpoint ~/.cache/flycode/checkpoints/constrained --fraction 0.1 \
  --output ~/.cache/flycode/lesion.json
flycode sensitivity --trajectories ~/.cache/flycode/training-records \
  --checkpoint ~/.cache/flycode/checkpoints/constrained \
  --parameter recurrent_gain --values 0.6 0.9 1.2 \
  --output ~/.cache/flycode/sensitivity.json
```

Use `--learn` for learning without disk persistence. Keep the same `--seed`
when loading experience: it is bound to the feature projection. Guard counters
belong to the current controller instance; the MB update count is persisted.
In 36 actual runs, learning and frozen controllers solved every task in 9 steps:
faster task adaptation has not been demonstrated.

`benchmark` creates separate repositories and logs for every case. It currently
supports the two toy fixtures in the example. `cortex` accepts `heuristic` or
the research-only `tournament` probe. For frozen/trained comparisons, use one
configuration per seed with that seed's checkpoint. Reports show actual costs;
compute equivalence is not assumed.

Phase 11 is partial: neuron input/synaptic-gain lesions and parameter sensitivity
are measured on recorded trajectories. Graph structure and source files remain
unchanged. `counterfactual_task_success` stays `null`: the probes do not execute
counterfactual repository repairs.

## Shadow observer — Phase 12

```bash
flycode run --repo /tmp/flycode-shadow --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain functional --shadow-brain full \
  --telemetry-dir ~/.cache/flycode/shadow-records
```

The active controller directs the agent and model calls. The observer ranks the
same candidates with the same safety mask and **zero influence** on the active
choice. `shadow_decision` in `*.steps.jsonl` records both choices, agreement,
expected-risk difference and observer latency. Counterfactual success/tool-call
counts are unmeasured. Observer failures are recorded without stopping the
active agent. Use `--shadow-connectome-path`/`--shadow-checkpoint` for another
observer artifact.

With the actual FlyWire graph, both toy tasks completed in 9 steps with exactly
the same actions as the active-only runs; recommendations agreed on 12 of 18
steps. This is a CLI mode with initial telemetry. Phases 13–14 still require
broader validation and controlled increases in controller influence.

## Local Qwen + Jev through LM Studio

FlyCode supports two local models with distinct roles:

- `qwen/qwen3.5-9b` proposes concrete actions.
- `jev-style-qwen3.5-2b-decision` scores safe options using next-token
  probabilities in its native decision format.
- The fly controller schedules inference and retains final action selection.
  Choose Functional Fly or the prepared real graph with `--brain full`.

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
| `reduced` | Reduced biological circuit with inherited parent parameters |
| `full` | Real FlyWire sparse topology with modeled recurrent dynamics |
| `frozen_connectome` / `frozen_full_connectome` | Legacy experimental random projections |

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
FLYCODE_TEST_CONNECTOME=1 python -m pytest tests/e2e/test_real_connectome.py -q
```

See [project status](docs/handoff.md) for the latest full-suite result;
LM Studio and real-graph acceptance are enabled separately.
The Docker acceptance tests run the real agent with Docker/Postgres in both `bias` and `hard` modes and
check HTTP responses and the Alembic revision stored in the database. They
choose an available HTTP port and clean up their containers afterward. Without
Docker/Compose, those two cases are explicitly skipped.

Repository-wide Ruff/mypy checks are not clean yet: style and typing findings
remain. See the [project status](docs/handoff.md) for details.

## Limitations and roadmap

- The heuristic generator is not a general-purpose code repair system.
- An advantage over comparable FSM/RNN controllers has not been demonstrated.
- Phase 6–8 acceptance covers toy tasks; generalization remains untested.
- Phase 10 has bounded persistent learning, but faster task adaptation is unproven.
- Phase 11 needs other connectomes, matched compute budgets and unseen repositories.
- Phase 12 needs broader telemetry; Phases 13–14 are not implemented yet.
- Research modes are CLI-only; the UI currently offers Functional Fly and frozen FlyWire.

## Documentation

- [Quick start and Python API](docs/quickstart.md)
- [Implementation plan](docs/Plan.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety.md)
- [Research](docs/research.md)
- [Verified status and remaining work](docs/handoff.md)
