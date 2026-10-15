# FlyCode

**English** · [Русский](README.md)

FlyCode is an experimental autonomous coding agent. It reads project files,
proposes small changes, selects actions through a controller inspired by
computational mechanisms of the fruit fly brain, and verifies the outcome
using tests and application health checks.

**Status: working MVP through Phase 4.** The agent autonomously repairs a demo
FastAPI/PostgreSQL project, starts Docker Compose, applies Alembic migrations
and checks the API. Real FlyWire, a reduced circuit and offline training
workflows, persistent learning, observers and guarded scoped autonomy are
available; research acceptance results are listed below.

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

The optional [desktop interface](http://127.0.0.1:8790/desktop) adds compact
navigation, light/dark themes and collapsible panels. The classic interface stays
at `/`; sidebar links switch between them. Both share projects, chats, history
and model preferences. Projects can use any local folder without Git, selected
through a native system dialog. Chats without a project keep their files separate.

- **«Попробовать на демо» (Try a demo)** repairs a real toy bug without models.
- For your own task, select a project, describe the outcome and press the arrow
  or `⌘ Enter` / `Ctrl Enter`. Local Qwen + Jev are selected by default.
- The conversation shows action rationales, model calls, tools and verification
  results. Expand «Почему это действие» to compare candidates and Jev scores.
  The Changes tab shows successfully applied edits from this run.
- The Journal tab adds text search, source/error filters, event/step IDs and
  expandable JSON details. «Следить» controls live scrolling; «Скачать JSONL»
  downloads the selected chat’s complete recorded journal, independent of filters.
- The inspector highlights the active stage and displays the controller mode,
  confidence, risk, steps and actual model requests. On narrow screens, open it
  using the top-right button.
- The model button selects the primary model and an optional scorer. Disable
  «Дополнительная оценка вариантов» to use Qwen without Jev. The adjacent settings
  button on its right opens controller, memory, seed, step/call budgets and timeout.
  Stop waits for
  the current call; it prevents new actions and preserves edits already applied.

`--workspace` sets the initial directory. Register additional local folders using
the Projects button in the sidebar. **The agent edits the selected project's
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

`benchmark` creates separate repositories and logs for every case. It supports
the two teacher toys and separate `search_helper_bug` / `boundary_regression_bug`
fixtures for helper discovery and boundary behavior. These are synthetic tasks,
not unseen real repositories. `cortex` accepts `heuristic`, the research-only
`tournament` probe or `lmstudio` with explicit model, timeout and call budgets. For frozen/trained comparisons, use one
configuration per seed with that seed's checkpoint. Reports show actual costs;
compute equivalence is not assumed.

Phase 11 is partial: neuron input/synaptic-gain lesions and parameter sensitivity
are measured on recorded trajectories. Graph structure and source files remain
unchanged. `counterfactual_task_success` stays `null`: replay probes do not
execute repository repairs. For actual task execution with a damaged controller,
set `lesion_fraction` on a benchmark controller. Reports retain mask provenance
and active-edge counts; source graph files remain unchanged.

The repeated-family runner compares frozen and learning Functional Fly instances
for each seed. Repositories and cortex contexts are fresh each episode; only
the corresponding controller retains learned experience. Reports include policy
fingerprints, rollbacks, completion and first/last repetition costs.
`faster_adaptation_demonstrated` requires successful tasks and fewer tool calls
for the learning condition at every one of at least three seeds.

```bash
flycode adapt --config docs/examples/adaptation.json --output ~/.cache/flycode/adaptation
flycode benchmark --config docs/examples/benchmark-lesion.json --output ~/.cache/flycode/lesion-tasks
flycode benchmark --config docs/examples/benchmark-heldout-lmstudio.json --output ~/.cache/flycode/heldout
```

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
steps. The observer is available in the CLI and browser. Broader validation on
unseen repositories remains an empirical requirement.

### Observer analysis and admission checks

`docs/examples/benchmark-admission.json` compares an active controller,
an observed controller and the full graph on two toy tasks with three seeds.
After running it, use the experiment directory identified in the output:

```bash
flycode benchmark --config docs/examples/benchmark-admission.json --output ~/.cache/flycode/admission
flycode shadow-analysis --trajectories /path/to/experiment/trajectories --output ~/.cache/flycode/shadow-analysis.json
flycode validate-rollout --report /path/to/experiment/report.json \
  --controller observed --baseline baseline --mode bounded --output ~/.cache/flycode/validation.json
```

Admission requires complete paired runs, clean verified completion without
regressions/model errors, at least three seeds, two fixtures and 30 observer
recommendations for shadow admission. Checksums bind logs, implementation and
controller artifacts. Initial admission permits only the 10% step. Different
models, graphs, checkpoints, tasks or budgets require matching evidence.
An `accepted=true` file alone cannot authorize use: checks are recomputed.
`--mode autonomous` compares measured `hard` runs against paired `bias` runs
on the same task set.

## Bounded influence and guarded autonomy — Phases 13–14

After creating admission, run a matching synthetic fixture:

```bash
flycode run --repo /tmp/flycode-bounded --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain functional --shadow-brain full --influence .1 \
  --validation-report ~/.cache/flycode/validation.json
flycode validate-rollout --report /path/to/experiment/report.json \
  --controller autonomous_full --baseline baseline --mode autonomous \
  --output ~/.cache/flycode/autonomous-validation.json
flycode run --repo /tmp/flycode-autonomous --fixture toy_arithmetic_bug \
  --task "Fix the bug" --brain full --autonomous \
  --validation-report ~/.cache/flycode/autonomous-validation.json
```

Influence can change only allowlisted diagnostic choices: reads, search, status
and logs. It cannot displace edits, SQL, tests or STOP. Levels are
0/10/25/50/100%; each candidate score correction is bounded by `0.2 × influence`.
A failed intervened action or regression disables influence for the rest of the
episode; already executed tool effects are not undone. Clean runs without an
actual intervention cannot authorize promotion to the next level.

`--autonomous` enables guarded hard selection. A failed hard choice that differs
from bias, or a regression, switches the remainder of the episode to bias.
Safety masks, budgets and verified completion remain active. Research
`--control hard` does not substitute for admission. Local-model admission also
binds the exact task instruction.

Open **«Наблюдение, память и допуск»** in browser settings to choose a prepared
full/reduced graph, trained checkpoint, distilled student, observer or Functional
Fly memory profile. Profiles are isolated by name, controller and seed, saved
atomically, and retained between tasks. Activity shows recommendations, actual
choices, learning and fallbacks. Older history remains readable.
The server indexes `~/.cache/flycode` by default; `FLYCODE_ARTIFACT_ROOT` selects
another artifact root. Browser clients select opaque IDs, never filesystem paths.

Current admission covers two teacher toys, three seeds and the heuristic cortex.
It does not authorize arbitrary repositories or Qwen/Jev without their own
paired evidence. This is evidence compatibility, not authentication against the
owner of local files.

[Measured rollout results](docs/research-results/2026-10-16-rollout.json): all
36 influence-level cases completed, with zero actual interventions; promotion
above 10% was rejected. In a separate final comparison, guarded full control
completed 6/6 toy cases in 5 steps, versus 12/12 baseline cases in 9 steps.
This narrow result does not demonstrate a general advantage.

The [expanded measurements](docs/research-results/2026-10-15-expanded-evaluation.json)
include null results and the limitations of the initial helper-reuse check.

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

For a single primary model, use `--no-decision-model` instead of
`--decision-model ...`. No Jev client is created and no scorer request is made.
In the JSON API, explicit `"decision_model": null` disables scoring; an omitted
field preserves the legacy dual-model default.

Other compatible models on the same LM Studio endpoint use the same selector.
Refreshing discovery never performs inference. An explicit role probe makes one
request with a 30-second timeout and displays its outcome and call count.
Primary models must return structured actions; scorers must return option token
probabilities. Protocol compatibility does not establish model quality or a known
weights revision. The initial pair has a bundled compatibility profile; other
models require an explicit probe before applying the selection. Personal model
defaults persist independently from the actual models shown in run history.

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

Verify a trusted local Python project independently:

```bash
flycode verify --repo /path/to/project --output /tmp/flycode-verification
```

This captures allowed inputs, runs the registered pytest profile in a separate
copy and returns a JSON verdict. Only `passed` exits with code0. Empty suites,
skipped/xfail, flaky results, timeout and incomplete evidence cannot pass.
`--reruns 1` retains both attempts; a passing retry never erases a failure.
Evidence includes snapshot/plan, checksum manifest, verdict, JUnit and observed
cases. Changed bytes, executable modes, environment or runtime invalidate approval.

This is the initial phase21 pytest foundation, covering the registered suite
rather than automatically proving every user acceptance criterion. Process copies
are for trusted code and do not restrict host/network access. `--trust untrusted`
blocks execution until a supported sandbox exists. Docker/HTTP/browser profiles,
independent oracles, verification UI and the team completion gate remain in the
[delivery plan](docs/superpowers/plans/2026-10-16-specification-delivery.md).

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
- Phase 12 needs broader telemetry on real tasks.
- Phases 13–14 have scoped implementations; broad autonomy and causal benefit remain unproved.
- The browser supports local artifacts, observers, memory and evidence-gated execution.

## Documentation

- [Quick start and Python API](docs/quickstart.md)
- [Implementation plan](docs/Plan.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety.md)
- [Research](docs/research.md)
- [Verified status and remaining work](docs/handoff.md)
