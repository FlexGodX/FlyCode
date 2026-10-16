# FlyCode v3 / Hermes × Drosophila

## Updated Implementation Plan

> **For agentic workers:** implement task-by-task. Use TDD, deterministic fixtures, small commits, and reproducible experiments. Do not collapse research, training, and production layers into one monolithic controller.

---

## Терминология проекта

В этом плане **Hermes означает сам FlyCode** — приложение, к которому
подключаются агенты принятия решений и LLM. Нейронный контроллер оркестрирует
их вызовы и выбор действий. Фазы 12–14 выполняются внутри FlyCode и не требуют
отдельного проекта Hermes.

Расширение управления командой агентов определено в §100 и в связанной
[спецификации оркестрации](specs/connectome-orchestration.md). Это проектируемый
функционал фаз 15–20; существующие режимы и допуски фаз 0–14 его не реализуют.

Архитектура единого модуля тестирования определена в §101 и в
[спецификации проверки результата](specs/testing-module.md). Она задаёт
проверки проекта, оркестрации и коннектома для проектируемых фаз 21–25.

Отдельный выбор основной модели, необязательного оценщика и будущих подключений,
а также две соседние кнопки моделей/параметров определены в §102 и
[спецификации выбора моделей](specs/model-selection.md).

Постановка пользовательских задач, получение их агентами и отображение текущих
исполнителей определены в §103 и [спецификации таск-трекера](specs/task-tracker.md).
Его поставки добавлены в roadmap как фазы 26–29.

# 1. Главная цель

Построить гибридного autonomous coding agent, в котором:

* LLM отвечает за семантическое понимание задачи, кода, логов и документации;
* fly-derived neural controller отвечает за persistent state, strategy direction, action arbitration, fast adaptation и reward-sensitive behavior;
* полная connectome-модель используется для исследований, causal experiments, teacher trajectories и task-specific adaptation;
* reduced/functional fly используется как realtime controller;
* все варианты сравниваются с обычными baselines при одинаковых условиях.

Главный исследовательский вопрос:

> Может ли вычислительная динамика, полученная из реального connectome и функциональных схем дрозофилы, дать автономному coding-agent измеримое преимущество по устойчивости, адаптации, tool selection и долгосрочному удержанию стратегии?

---

# 2. Главный архитектурный принцип

Не строить:

```text
User
 ↓
Full Fly
 ↓
Code
```

Правильная архитектура:

```text
USER TASK
   │
   ▼
LLM CORTEX
semantic reasoning
   │
   ▼
candidate actions / plans
   │
   ▼
FLY CONTROL SYSTEM
   │
   ├── task compass
   ├── associative memory
   ├── valence
   ├── novelty
   ├── reward prediction error
   └── action arbitration
   │
   ▼
TOOL EXECUTOR
   │
   ▼
SOFTWARE ENVIRONMENT
Git / Files / Tests / Docker / DB / API
   │
   ▼
OUTCOME
   │
   └──────────────► neural state / learning
```

---

# 3. Два контура системы

Архитектура разделяется на:

```text
ONLINE / FAST PATH
```

и

```text
RESEARCH / TRAINING PATH
```

## Online path

Используется непосредственно Hermes.

```text
World
 ↓
Sensory Encoder
 ↓
Functional / Reduced Fly
 ↓
Policy Bias / Behavior Mode
 ↓
LLM
 ↓
Action
```

Требования:

* low latency;
* persistent state;
* bounded online learning;
* deterministic safety layer;
* realtime operation.

---

## Research path

Используется offline.

```text
Full Connectome
 ↓
stimulation
lesions
parameter sweeps
task training
 ↓
teacher trajectories
 ↓
distillation / architecture discovery
 ↓
Reduced Fly
 ↓
Functional Fly
```

Полная муха не обязана участвовать в каждом tool call.

---

# 4. Brain tiers

Поддерживать несколько brain backends.

## Tier 0 — baseline brains

```text
HeuristicFSM
RandomReservoir
GenericRNN
```

Нужны как контрольные модели.

---

## Tier 1 — Functional Fly

Production-oriented controller.

Состоит из:

```text
Central Complex-like module
Mushroom Body-like module
Valence module
Action Arbiter
```

Размер:

```text
~100–5000 latent units
```

---

## Tier 2 — Reduced Connectome

Connectome-informed SNN / recurrent model.

Размер:

```text
~1k–20k neurons
```

Используется:

* для research;
* advanced controller;
* distillation target/source;
* comparison with Functional Fly.

---

## Tier 3 — Full Connectome

Полная / near-full connectome simulation.

Используется:

* research;
* virtual stimulation;
* lesion experiments;
* teacher trajectories;
* task-specific connectome-constrained training.

Не считать её обязательным production backend.

---

# 5. Унифицированный BrainBackend

Все варианты должны реализовывать один interface.

```python
class BrainBackend(Protocol):

    def reset(self) -> None:
        ...

    def step(
        self,
        stimulus: "NeuralStimulus",
    ) -> "NeuralState":
        ...

    def get_state(self) -> "NeuralState":
        ...

    def close(self) -> None:
        ...
```

Реализации:

```text
MockBrainBackend
RandomReservoirBackend
GenericRNNBackend
FunctionalFlyBackend
ReducedConnectomeBackend
FrozenFullConnectomeBackend
TrainableConnectomeBackend
```

Позже:

```text
LoihiBackend
```

---

# 6. SoftwareGym

Создать независимый software-engineering environment.

Это критически важная часть проекта.

```python
class SoftwareEnvironment:

    async def reset(
        self,
        task: "TaskSpec",
    ) -> "Observation":
        ...

    async def observe(self) -> "Observation":
        ...

    async def step(
        self,
        action: "ToolAction",
    ) -> "Transition":
        ...
```

---

# 7. Observation

```python
class Observation(BaseModel):
    task: str

    git_state: dict

    tests: dict
    build: dict

    docker: dict
    database: dict
    services: dict

    recent_logs: list[str]

    recent_errors: list[str]

    resource_state: dict

    action_history_summary: dict
```

Не хранить весь repository внутри observation.

---

# 8. Transition

```python
class Transition(BaseModel):
    previous: Observation
    action: ToolAction
    current: Observation

    utility: float
    aversion: float
    novelty: float
    surprise: float

    information_gain: float
    risk: float
    cost: float
```

---

# 9. Behavior modes

Оставить высокоуровневые behavioral states:

```python
class BehaviorMode(StrEnum):
    EXPLORE = "explore"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    CONSOLIDATE = "consolidate"
    STOP = "stop"
```

Но больше не рассматривать их как простой FSM enum.

Они являются состояниями динамического controller.

---

# 10. Central Complex → Task Compass

Добавить persistent low-dimensional task state.

```python
class TaskCompassState(BaseModel):
    current_direction: list[float]
    target_direction: list[float]

    deviation: float
    persistence: float
    strategy_confidence: float
```

Task Compass отвечает за:

```text
куда движется решение;
на какой стадии находится workflow;
отклоняется ли агент от цели;
не застрял ли агент в одной стратегии.
```

---

# 11. Пример Task Compass

Workflow:

```text
EXPLORE
→
DIAGNOSE
→
PLAN
→
EXECUTE
→
VERIFY
```

Если агент делает:

```text
EXECUTE
EXECUTE
EXECUTE
EXECUTE
```

без проверки:

```text
task deviation ↑
```

Task Compass увеличивает bias:

```text
VERIFY ↑
EXECUTE ↓
```

---

# 12. Mushroom Body → Fast Associative Memory

Отдельно от обычной episodic memory реализовать быструю associative memory.

Pipeline:

```text
Observation
 ↓
sparse projection
 ↓
Kenyon-like representation
 ↓
Action / Strategy associations
 ↓
learned valence
```

---

# 13. Sparse representation

```python
class SparseCode(BaseModel):
    active_indices: list[int]
    activity: list[float]
```

Target sparsity:

```text
2–10%
```

Параметр должен быть configurable.

---

# 14. Пример MB learning

State:

```text
Postgres connection refused
docker-compose exists
postgres service stopped
```

Sparse pattern:

```text
0010001000010000010...
```

Action:

```text
docker compose up -d postgres
```

Outcome:

```text
database healthy
```

Ассоциация:

```text
pattern → start_database
```

усиливается.

---

# 15. Reward Prediction Error

Не обучаться только по абсолютному reward.

Использовать:

```text
δ = r + γV(s_next) - V(s)
```

---

# 16. RPE state

```python
class PredictionError(BaseModel):
    predicted_value: float
    observed_reward: float
    next_value: float

    delta: float
```

---

# 17. Зачем нужен RPE

Пример:

Первый раз:

```text
DB не работает
↓
start Postgres
↓
DB работает
```

Большой positive prediction error.

После многократного повторения:

```text
DB не работает
↓
start Postgres
↓
DB работает
```

результат ожидаем.

RPE становится небольшим.

Это предотвращает бессмысленное reinforcement повторяющихся очевидных действий.

---

# 18. Dual Valence

Не использовать один `reward`.

Использовать:

```python
class OutcomeSignals(BaseModel):
    utility: float
    aversion: float
    novelty: float
    surprise: float
```

---

# 19. Пример Dual Valence

Action:

```text
DROP DATABASE
```

Может иметь:

```text
utility = 0.65
aversion = 0.99
```

Action arbiter видит конфликт.

Safety layer запрещает выполнение.

---

# 20. Internal drives

```python
class DriveState(BaseModel):
    progress: float
    novelty_need: float
    uncertainty: float
    stress: float
    urgency: float
    stagnation: float
    repetition: float
    risk: float
    context_pressure: float
```

Диапазон:

```text
0.0–1.0
```

---

# 21. Habituation

Повторение одинаковых бесполезных действий должно автоматически снижать их привлекательность.

Fingerprint:

```python
class ActionFingerprint(BaseModel):
    mode: BehaviorMode
    tool: str
    target: str | None
    normalized_args: dict
```

---

# 22. Curiosity / novelty

Отслеживать:

```text
новый файл
новую зависимость
новый stack trace
новый service
новый symbol
новую DB table
новый code path
```

Если:

```text
progress low
+
novelty low
```

увеличивать:

```text
EXPLORE bias
```

---

# 23. LLM Cortex

LLM остаётся главным semantic reasoner.

Он получает:

```text
Task
Behavior Mode
Task Compass state
Working Memory
Relevant Episodic Memory
Environment Observation
Candidate Tool Set
Fly Policy Bias
```

---

# 24. LLM не должен игнорировать controller

Например:

```text
BehaviorMode = VERIFY
```

LLM может:

```text
pytest
curl
git diff
docker compose ps
database health check
```

Но не может выполнить большой refactor.

---

# 25. Candidate generation

LLM генерирует несколько concrete actions.

```python
class CandidateAction(BaseModel):
    id: str

    tool: str
    arguments: dict

    rationale: str

    expected_progress: float
    expected_information_gain: float

    expected_risk: float
    confidence: float
```

Default:

```text
N = 4 candidates
```

---

# 26. Candidate Tournament

После stable MVP добавить:

```text
LLM candidates
 ↓
candidate encoder
 ↓
fly controller
 ↓
scores
 ↓
arbiter
 ↓
winner
```

---

# 27. Hard Control и Bias Control

Поддерживать два режима.

## HARD_CONTROL

```text
Fly decides final candidate.
```

Использовать только:

* research;
* benchmarks;
* controlled environments.

---

## BIAS_CONTROL

Production default.

```text
LLM candidate scores
+
Fly biases
+
Safety mask
=
final ranking
```

---

# 28. Пример Bias Control

LLM:

```text
read logs       0.30
edit config     0.35
restart service 0.20
run tests       0.15
```

Fly:

```text
read logs       +0.30
edit config     -0.20
restart service +0.05
run tests       +0.05
```

Final:

```text
read logs       0.60
edit config     0.15
restart service 0.25
run tests       0.20
```

Winner:

```text
read logs
```

---

# 29. Safety mask

Safety всегда выше fly-controller.

Pipeline:

```text
Candidate scores
 ↓
Fly bias
 ↓
Risk detector
 ↓
Safety mask
 ↓
Final selection
```

Нельзя разрешать fly-core обходить safety.

---

# 30. Risk reflex

High-risk actions:

```text
database reset
DROP
DELETE without WHERE
rm -rf
git reset --hard
volume deletion
credential changes
history rewrite
mass file deletion
```

Должны либо:

```text
block
```

либо переводить систему в:

```text
VERIFY_RISK
```

---

# 31. Recovery reflex

После:

```text
regression
crash
migration failure
DB failure
service failure
unexpected test increase
```

автоматически повышать:

```text
RECOVER
DIAGNOSE
```

---

# 32. Working Memory

Хранить до:

```text
20–30 significant facts
```

Пример:

```text
Auth failure begins in service.py:83.

Postgres container failed because port 5432 is occupied.

Migration 0004 introduced NOT NULL users.email.

Last edit increased failing tests from 2 to 7.
```

---

# 33. Episodic Memory

После задачи:

```python
class Episode(BaseModel):
    task: str

    initial_state: dict

    key_observations: list[str]

    actions: list[dict]

    failures: list[dict]

    successful_patterns: list[dict]

    final_result: str

    lessons: list[str]
```

---

# 34. Consolidation

Перед STOP:

```text
CONSOLIDATE
```

LLM формирует:

```text
Что произошло?
Что помогло?
Что не помогло?
Какая стратегия оказалась успешной?
Какие наблюдения следует запомнить?
```

---

# 35. Connectome training modes

Это центральная новая часть проекта.

Поддерживать три режима.

---

# 36. Frozen Full Fly

Структура:

```text
real connectome
🔒
```

Не обучать внутренние weights.

Обучается:

```text
input mapping
readout
```

Цель:

> проверить, полезна ли исходная динамика connectome сама по себе.

---

# 37. Connectome-Constrained Fly

Топология:

```text
🔒 fixed
```

Trainable:

```text
synaptic gains
time constants
thresholds
input gains
cell-type gains
selected modulatory parameters
readout
```

---

# 38. Запрещённое изменение

Нельзя произвольно создавать:

```text
Neuron A → Neuron X
```

если такой connectivity нет в выбранной биологической topology.

---

# 39. Формула

Концептуально:

```text
W_effective =
TopologyMask ⊙ TrainableGain
```

TopologyMask:

```text
frozen biological graph
```

---

# 40. Задача constrained training

Обучать сеть на software-engineering trajectories:

```text
state
candidate actions
selected action
outcome
future reward
```

Цели:

```text
action selection
strategy switching
persistent state
recovery
delayed reward
risk arbitration
```

---

# 41. Functional Fly

После экспериментов full/constrained fly извлекать наиболее полезные motifs.

Основные modules:

```text
Task Compass / CX
Fast Associative Memory / MB
Valence
Novelty
RPE
Action Arbiter
```

---

# 42. Distillation

Teacher:

```text
Frozen / constrained / reduced fly
```

Student:

```text
Functional Fly
```

---

# 43. Distillation targets

Не ограничиваться action logits.

Обучать student повторять:

```text
action preferences
strategy switches
internal state trajectories
response to novelty
response to risk
recovery behavior
response to delayed reward
```

---

# 44. Research server

Полный connectome вынести в отдельный service.

```text
fly-research-server
```

API:

```text
POST /episode
POST /stimulate
POST /step
POST /lesion
POST /reset
POST /parameter-sweep
GET /state
DELETE /episode
```

---

# 45. Задачи research server

```text
virtual stimulation
virtual lesions
neural tracing
parameter perturbation
trajectory generation
teacher data generation
motif discovery
cross-connectome experiments
```

---

# 46. Research vs production separation

```text
Research Plane
Full Fly
↓
teacher data
↓
distillation
↓
Production Plane
Functional Fly
↓
Hermes
```

Это разделение не абсолютное.

Full Fly должен оставаться runnable в agent.

---

# 47. CLI brain selection

```bash
flycode run --brain functional
```

```bash
flycode run --brain reduced
```

```bash
flycode run --brain full
```

```bash
flycode run --brain constrained
```

---

# 48. Full Fly может использоваться напрямую

Это важно.

Не предполагать заранее, что distillation обязательна.

Benchmark может показать:

```text
Full Fly достаточно быстрый
+
качество значительно лучше
```

Тогда его можно оставить online.

---

# 49. Software tools

MVP tools:

```text
read_file
list_files
search
git_status
git_diff
pytest
edit_file
create_file
```

---

# 50. Environment tools

Следующая стадия:

```text
docker_compose
docker_logs
postgres_status
postgres_query
alembic
http_request
service_health
```

---

# 51. Target demo

User task:

```text
Fix this FastAPI application and get it fully running.
```

Environment:

```text
FastAPI
PostgreSQL
SQLAlchemy
Alembic
Docker Compose
pytest
```

Intentionally break:

```text
Docker configuration
migration
DB connection
API route
Python bug
```

---

# 52. Desired autonomous trajectory

```text
EXPLORE
↓
DIAGNOSE
↓
PLAN
↓
EXECUTE
↓
VERIFY
↓
RECOVER
↓
EXECUTE
↓
VERIFY
↓
CONSOLIDATE
↓
STOP
```

---

# 53. Training dataset

Каждый trajectory:

```python
class TrainingStep(BaseModel):
    observation: dict

    neural_state: list[float] | None

    task_compass_state: dict | None

    candidates: list[dict]

    chosen_action: dict

    outcome: dict

    reward_prediction_error: float

    future_return: float
```

---

# 54. Offline imitation training

Первый способ обучения.

Teacher:

```text
high-quality LLM agent
human-approved trajectories
heuristic controller
```

Fly-core учится:

```text
state → good behavior bias
```

---

# 55. RL / task optimization

После imitation добавить environment-based optimization.

Не начинать с online RL на живом repository.

Использовать deterministic benchmark environments.

---

# 56. Online learning

Production Fly должен иметь bounded online adaptation.

Разрешено:

```text
MB associative weights
readout
small modulatory biases
```

Запрещено автоматически менять большие части controller без limits.

---

# 57. Online-learning limits

Добавить:

```text
max update norm
learning-rate cap
replay rollback
policy snapshot
drift detector
```

---

# 58. Shadow Mode

Перед реальным контролем:

```text
Hermes chooses A
Fly chooses B
```

Выполняется A.

Записывается B.

---

# 59. Shadow analysis

Считать:

```text
Fly agreement rate
counterfactual success estimate
risk delta
tool-call delta
latency
```

---

# 60. Gradual influence rollout

```text
0% fly influence
↓
10%
↓
25%
↓
50%
↓
bias control
↓
hard control for selected tasks
```

---

# 61. Baselines

Обязательные:

```text
LLM only
Heuristic FSM
Random Reservoir
Generic RNN
Functional Fly
Reduced Connectome
Frozen Full Fly
Connectome-Constrained Fly
```

---

# 62. Matched-compute rule

Сравнение Functional Fly vs Generic RNN должно иметь сопоставимые:

```text
parameter count
hidden state size
latency
training examples
training budget
```

---

# 63. Candidate Tournament benchmark

Сравнить:

```text
LLM ranking only
Random Reservoir ranking
Generic RNN ranking
Fly ranking
```

На одинаковых candidates.

---

# 64. Benchmark categories

## Simple bug

```text
obvious failing unit test
```

---

## Search

Нужный helper существует в другом месте.

---

## Regression

Первая очевидная правка вызывает новый failure.

---

## Docker

Service configuration broken.

---

## Database

Postgres not running / migration broken.

---

## HTTP

Endpoint returns 500.

---

## Partial observability

Часть observations скрывается.

Проверяет persistent state.

---

## Delayed reward

Последствия действия появляются через:

```text
10–20 agent steps
```

---

## Context reversal

Ранее успешная стратегия становится неверной.

Проверяется скорость переобучения.

---

## Noisy feedback

Некоторые тесты stochastic.

Проверяется устойчивость controller.

---

## Novel repository

Неизвестный framework.

Проверяется exploration.

---

## Resource constrained

Например:

```text
max 20 tool calls
```

---

# 65. Основная utility-метрика

Не использовать только success rate.

Использовать:

```text
U =
TaskQuality
- λ1 * Latency
- λ2 * ComputeCost
- λ3 * ToolCalls
- λ4 * SafetyRisk
- λ5 * Regressions
```

---

# 66. Metrics

Собирать:

```text
success
verified completion
steps
tool calls
LLM calls
tokens
wall-clock time
compute cost
changed lines
regressions
recovery events
unsafe proposals
repeated actions
novel observations
strategy switches
policy confidence
candidate entropy
RPE statistics
```

---

# 67. Virtual lesions

Ablation должна быть selective.

Отключение:

```text
CX
```

ожидаемо ухудшает:

```text
long-horizon strategy persistence
```

---

Отключение:

```text
MB
```

ожидаемо ухудшает:

```text
fast adaptation
reversal learning
```

---

Отключение:

```text
Valence
```

ожидаемо ухудшает:

```text
risk/reward arbitration
```

---

Отключение:

```text
Novelty
```

ожидаемо ухудшает:

```text
exploration
```

---

# 68. Sensitivity analysis

Автоматические sweeps:

```text
synaptic gain
0.5×
0.75×
1.0×
1.25×
1.5×
```

```text
time constant
0.7×–1.3×
```

```text
noise
low → high
```

```text
input intensity
0.5×–1.5×
```

---

# 69. Robustness rule

Если advantage существует только при одном очень точном parameter set:

```text
не считать это сильным результатом
```

---

# 70. Cross-connectome research

Поддержать несколько biological datasets.

Например:

```text
female connectome
male CNS connectome
```

---

# 71. Cross-connectome objective

Искать:

```text
shared motifs
shared dynamics
stable action-selection properties
```

---

# 72. Motif extraction

Research pipeline:

```text
connectome
↓
graph analysis
↓
candidate motifs
↓
simulation
↓
software-task evaluation
↓
distillation
```

---

# 73. Project structure

```text
flycode/
├── pyproject.toml
├── README.md
│
├── docs/
│   ├── architecture.md
│   ├── research.md
│   ├── training.md
│   ├── benchmarks.md
│   └── safety.md
│
├── src/flycode/
│
│   ├── domain/
│   │   ├── task.py
│   │   ├── observation.py
│   │   ├── transition.py
│   │   └── action.py
│
│   ├── environment/
│   │   ├── software_gym.py
│   │   ├── observer.py
│   │   └── fixtures.py
│
│   ├── brain/
│   │   ├── protocol.py
│   │   ├── mock.py
│   │   ├── random_reservoir.py
│   │   ├── generic_rnn.py
│   │   ├── functional_fly.py
│   │   ├── reduced_connectome.py
│   │   ├── frozen_connectome.py
│   │   ├── trainable_connectome.py
│   │   └── encoder.py
│
│   ├── cognition/
│   │   ├── task_compass.py
│   │   ├── mushroom_body.py
│   │   ├── valence.py
│   │   ├── novelty.py
│   │   ├── drives.py
│   │   ├── rpe.py
│   │   └── habituation.py
│
│   ├── policy/
│   │   ├── behavior.py
│   │   ├── heuristic.py
│   │   ├── fly_policy.py
│   │   ├── bias_control.py
│   │   ├── hard_control.py
│   │   └── candidate_tournament.py
│
│   ├── llm/
│   │   ├── protocol.py
│   │   ├── openai_compatible.py
│   │   ├── candidate_generator.py
│   │   └── prompts.py
│
│   ├── tools/
│   │   ├── filesystem.py
│   │   ├── search.py
│   │   ├── git.py
│   │   ├── tests.py
│   │   ├── editor.py
│   │   ├── docker.py
│   │   ├── postgres.py
│   │   └── http.py
│
│   ├── memory/
│   │   ├── working.py
│   │   ├── episodic.py
│   │   └── consolidation.py
│
│   ├── training/
│   │   ├── dataset.py
│   │   ├── imitation.py
│   │   ├── constrained.py
│   │   ├── replay.py
│   │   └── distillation.py
│
│   ├── research/
│   │   ├── stimulation.py
│   │   ├── lesions.py
│   │   ├── sensitivity.py
│   │   ├── cross_connectome.py
│   │   └── motifs.py
│
│   ├── safety/
│   │   ├── risk.py
│   │   └── command_policy.py
│
│   ├── telemetry/
│   │   ├── logger.py
│   │   └── schema.py
│
│   └── benchmark/
│       ├── runner.py
│       ├── metrics.py
│       ├── matched_compute.py
│       └── ablations.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── research/
│
└── benchmarks/
```

---

# 74. Implementation roadmap

## Phase 0 — SoftwareGym

Сделать:

```text
Observation
Transition
ToolAction
SoftwareEnvironment
deterministic fixtures
```

Acceptance:

```text
один и тот же task reproducibly runs multiple times
```

---

# 75. Phase 1 — обычный autonomous agent

До мухи.

```text
LLM
Behavior Modes
Filesystem
Search
Git
Tests
Editor
STOP verification
```

Acceptance:

```text
agent fixes deterministic toy repositories
```

---

# 76. Phase 2 — Baseline controllers

Добавить:

```text
Heuristic FSM
Random Reservoir
Generic RNN
```

Не подключать full fly раньше baselines.

---

# 77. Phase 3 — Functional Fly

Реализовать:

```text
Task Compass
MB Memory
Valence
Novelty
RPE
Action Arbiter
```

Acceptance:

```text
controller управляет BehaviorMode и candidate bias
```

---

# 78. Phase 4 — Environment autonomy

Добавить:

```text
Docker
Postgres
Alembic
HTTP
service health
recovery
```

Acceptance:

FlyCode самостоятельно поднимает FastAPI/Postgres fixture.

---

# 79. Phase 5 — Real connectome

Добавить:

```text
FrozenFullConnectomeBackend
real neural state
input mapping
readout
```

Acceptance:

```text
same SoftwareGym tasks can run with --brain full
```

---

# 80. Phase 6 — Connectome-Constrained Training

Разрешить обучение выбранных dynamics parameters.

Сохранять topology.

Acceptance:

```text
trained constrained model
beats its frozen initialization on training tasks
without changing graph topology
```

---

# 81. Phase 7 — Reduced Connectome

Выделить smaller circuit.

Способы:

```text
graph motifs
functional subcircuits
teacher activity
connectivity bottlenecks
```

Acceptance:

```text
reduced brain retains useful behavior
with substantially lower compute
```

---

# 82. Phase 8 — Distillation

Teacher:

```text
trained full/reduced model
```

Student:

```text
Functional Fly
```

Acceptance:

```text
student reproduces selected teacher behavior
within configured tolerance
```

---

# 83. Phase 9 — Candidate Tournament

Добавить:

```text
4 candidate actions
Fly scoring
Safety mask
winner
```

Acceptance:

```text
tournament operates in both hard and bias modes
```

---

# 84. Phase 10 — Online learning

Добавить bounded MB/RPE updates.

Acceptance:

```text
agent adapts faster on repeated task families
without uncontrolled policy drift
```

---

# 85. Phase 11 — Causal science

Добавить:

```text
lesions
sensitivity
cross-connectome
matched compute
multi-seed runs
```

---

# 86. Phase 12 — Shadow integration into Hermes

Fly recommendations не влияют на действия.

Собрать enough telemetry.

---

# 87. Phase 13 — Bounded Hermes influence

Default:

```text
BIAS_CONTROL
```

Только low-risk decisions.

---

# 88. Phase 14 — Full autonomous Fly mode

Использовать только после benchmark validation.

---

# 89. CLI

```bash
flycode run \
  --repo ./project \
  --task "Fix the application" \
  --brain functional \
  --control bias
```

---

```bash
flycode run \
  --brain full \
  --control hard
```

---

```bash
flycode train \
  --brain constrained \
  --dataset trajectories/
```

---

```bash
flycode distill \
  --teacher constrained \
  --student functional
```

---

```bash
flycode benchmark \
  --brains llm,fsm,rnn,functional,reduced,full,constrained
```

---

```bash
flycode lesion \
  --brain functional \
  --module mushroom-body
```

---

```bash
flycode sensitivity \
  --brain constrained
```

---

# 90. Research success criteria

Не считать успехом:

```text
agent once fixed a bug
```

---

Считать сильным результатом:

```text
multiple tasks
multiple seeds
matched compute
stable effect
ablation specificity
robustness to parameters
```

---

# 91. Engineering success criteria

Production candidate должен:

```text
improve task quality
или
reduce tool calls
или
improve adaptation
или
improve safety
```

без неприемлемого:

```text
latency
compute
complexity
policy instability
```

---

# 92. Главный benchmark

Сравнивать:

```text
LLM only
FSM
Generic RNN
Functional Fly
Reduced Connectome
Frozen Full Fly
Constrained Full Fly
```

На одинаковых задачах.

---

# 93. Главная исследовательская гипотеза

Не:

> Может ли муха программировать?

А:

> Может ли биологически ограниченная recurrent architecture дать автономному software-agent полезные inductive biases для persistent state, fast adaptation, action selection и delayed reward?

---

# 94. Главная инженерная гипотеза

Не:

> Чем больше биологических нейронов, тем лучше.

А:

> Полный connectome может служить teacher/research substrate, а production controller должен содержать только те механизмы, которые доказали полезность в benchmark.

---

# 95. Финальная architecture
ы
```text
                        USER
                         │
                         ▼
                     LLM CORTEX
                         │
                  candidate actions
                         │
                         ▼
              ┌─────────────────────┐
              │    FLY CONTROL      │
              │                     │
              │ Task Compass / CX   │
              │ MB Fast Memory      │
              │ Valence             │
              │ Novelty             │
              │ RPE                 │
              └─────────┬───────────┘
                        │
                     biases
                        │
                        ▼
                ACTION TOURNAMENT
                        │
                        ▼
                   SAFETY MASK
                        │
                        ▼
                      ACTION
                        │
                        ▼
                 SOFTWARE GYM
                        │
                        ▼
                     OUTCOME
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
             RPE               TELEMETRY
              │
              ▼
            MEMORY


             RESEARCH / TRAINING PLANE

     Full Female Connectome
              │
     Full Male Connectome
              │
              ▼
       Stimulation / Lesion
              │
              ▼
     Connectome-Constrained
          Task Training
              │
              ▼
       Reduced Connectome
              │
              ▼
          Distillation
              │
              ▼
        Functional Fly
              │
              ▼
             Hermes
```

---

# 96. Главный принцип реализации

Не пытаться сразу построить:

```text
LLM + Full Fly + Docker + MB + CX + RL + Memory
```

в одном giant implementation.

Порядок должен быть:

```text
SoftwareGym
↓
working autonomous agent
↓
baselines
↓
Functional Fly
↓
real environment
↓
Full Connectome
↓
Constrained training
↓
Reduced Connectome
↓
Distillation
↓
Candidate Tournament
↓
Online learning
↓
Causal evaluation
↓
Hermes integration
```

---

# 97. Definition of final success

Проект считается действительно успешным, если можно показать:

```text
same task
same LLM
same tools
same compute budget
```

и получить воспроизводимый результат, что один или несколько fly-derived механизмов:

```text
улучшают persistent state
улучшают strategy switching
ускоряют adaptation
уменьшают unnecessary tool calls
улучшают delayed-reward performance
или снижают unsafe behavior
```

по сравнению с сопоставимым обычным RNN/FSM.

---

# 98. Самый важный принцип

Если в конце исследования окажется:

```text
Generic RNN > Fly
```

это не провал проекта.

Это валидный научный результат.

Если окажется:

```text
Functional Fly > Generic RNN
```

на matched-compute benchmark —

тогда появляется действительно сильный результат, который можно развивать дальше.

---

# 99. Конечная цель

Не создать:

> «coding agent с мозгом мухи»

как gimmick.

А создать:

> **гибридную agent architecture, где реальные вычислительные принципы мозга дрозофилы используются как проверяемые inductive biases для autonomous software engineering.**

И затем объективно определить, какие из этих механизмов действительно полезны.

---

# 100. Оркестрация нескольких агентов

Следующее расширение FlyCode — управление командой специализированных агентов
для одной пользовательской цели через fly-derived controller. Нормативные
требования, контракты, ограничения и сценарии приёмки вынесены в
[спецификацию оркестрации](specs/connectome-orchestration.md), версия 1.0.
Она является дополнением к этому плану. Статус: спроектировано, не реализовано.

Модель, профиль агента и запущенный экземпляр — разные сущности. Несколько
профилей могут использовать одну локальную модель. Qwen/Jev в текущем cortex
не считаются готовой системой независимых агентов.

Обязательная архитектура:

```text
User goal → validated task DAG → dispatch candidates
                                   ↓
                     connectome recommendation + safety/budget mask
                                   ↓
                       durable central dispatcher
                                   ↓
                  isolated agents ↔ addressed messages/artifacts
                                   ↓
                        review → integration → verification
```

Коннектом влияет на выбор задачи, профиля и стратегии; диспетчер выполняет вызовы
и обеспечивает права, ресурсы и восстановление. Он не может расширять доступ
по предложению модели. Приоритет учитывает решение пользователя, зависимости,
критический путь, ожидание, стоимость и риск. Состояние верхнего контроллера
отдельно от состояний исполнителей; веса назначения привязаны к candidate ID.

Дополнительная ценность: план без запуска, объяснение блокировок, передача задачи,
контрольные точки, пакет проверенных результатов, изолированный cache опыта и
ограниченное сравнение двух решений. Их поведение и бюджет определены в ORC-10.

| Новая фаза | Проверяемая поставка |
|---|---|
| 15 | Реестр агентов, граф задач, базовые назначения и приоритеты |
| 16 | Адресованные сообщения, общие бюджеты, ресурсы и восстановление |
| 17 | Изолированные изменения, review, интеграция и проверка результата |
| 18 | Коннектом в zero-influence shadow для реальных назначений |
| 19 | Новый допуск orchestration, ограниченное влияние и резервный scheduler |
| 20 | Интерфейс команды, управления задачами, связи и расходов |

Приёмка каждой фазы задаётся ORC/AC-таблицами связанной спецификации. Общий выигрыш
сравнивается с одиночным агентом и обычными multi-agent scheduler при одинаковых
моделях, инструментах и суммарных бюджетах. Допуски одиночных учебных задач
не разрешают новый класс решений. Реализация orchestration не закрывает
недоказанные исследовательские критерии §§90–99.

---

# 101. Модуль тестирования и проверки результата

Нормативная архитектура, контракты и критерии приёмки определены в
[спецификации модуля тестирования](specs/testing-module.md), версия 1.0.
Статус реализации отслеживается в §104. Модуль объединяет существующие
инструменты проверок и research через адаптеры, сохраняя их разные назначения.

Три независимых результата: корректность пользовательского проекта, надёжность
FlyCode/команды агентов и измеренное качество нейронного контроллера. Успех
одной области не закрывает остальные. Агент-тестировщик предлагает проверки и
анализирует failures; итог вычисляет отдельный детерминированный evaluator.

```text
Task / patch / workflow / experiment
                 ↓
   VerificationPlan + immutable SubjectSnapshot
                 ↓
      policy / budget / isolated runners
                 ↓
       observations + evidence manifest
                 ↓
      deterministic VerificationVerdict
                 ↓
 completion gate / scoped research admission / UI
```

Вердикт связан с точными байтами кода, базой, тестовым контрактом, runner/oracle
и средой. Required checks не удаляются по решению агента или коннектома.
Пустые/skipped/flaky наборы, timeout и неполные отчёты не дают зелёный статус.
Повторы сохраняют исходные результаты; изменение кода инвалидирует approval.
Успех отдельных patch не заменяет проверку объединённого проекта.

Обязательны изоляция окружений и внешних эффектов, контроль общих бюджетов,
отмена и восстановление, воспроизводимое evidence и честное разделение живых
вызовов, mock и replay. Исследования сохраняют все случаи в знаменателе,
разделяют train/held-out и явно показывают сопоставимость ресурсов.
Программный pass сам по себе не является допуском или доказательством пользы мухи.

| Новая фаза | Проверяемая поставка |
|---|---|
| 21 | Снимки, планы, pytest adapter, структурированные результаты и verdict |
| 22 | Независимые oracle, изоляция, flaky policy, evidence и восстановление |
| 23 | Проверка оркестрации, интеграции и устойчивости к отказам |
| 24 | Проверка коннектома, экспериментальных сравнений и scoped admission |
| 25 | CLI/API, экран проверки, воспроизведение и отчёты |

Фазы 21–22 могут разрабатываться на существующем одиночном агенте и нужны до
приёмки интеграции из фазы 17. Фазы 23–24 используют части multi-agent фаз
16–19; UI фазы 25 развивается вместе с фазой 20. Нумерация не создаёт требования
сначала завершить всю оркестрацию, а затем строить её проверку.
Соответствие TST-01..15 и TV-01..17 задано в связанной спецификации.

---

# 102. Выбор моделей и раздельные настройки

Нормативные требования заданы в [спецификации выбора моделей](specs/model-selection.md),
версия 1.0. Реализация разрешена; её порядок и проверяемый статус указаны в §104.

Обязательные варианты: одна основная модель (начальный вариант — Qwen без Jev),
основная модель с оценщиком (Qwen + Jev) и отдельный демо-режим без LLM.
Jev необязателен для single-режима: его клиент не создаётся и запросы к нему
не выполняются. Коннектом, safety, бюджеты и проверка завершения сохраняются.

Каталог расширяемый: позже подключённые совместимые модели других семейств
выбираются через тот же интерфейс для разрешённых ролей. Qwen/Jev — начальные
варианты, а не закрытый список. Сервер хранит model refs, endpoints, capabilities
и причины недоступности; недоступную модель нельзя заменить молча.

```text
[ Проект ]  [ Выбранная модель / пара ▾ ] [ Параметры ⚙ ]  [ Отправить ]
                         модели             справа
```

Одна кнопка моделей открывает общий выбор режима, основной модели и оценщика.
Непосредственно справа находится отдельная кнопка параметров: коннектом,
наблюдатель, влияние/допуск, память, seed, шаги и бюджеты. Панели не сбрасывают
настройки друг друга. Порядок, доступность и область нажатия сохраняются на mobile.

Отключённый оценщик проектируется как явный `decision_model=null`; отсутствие
поля сохраняет legacy default. История и личный выбор следующих запусков
разделены. Фактическая модель/пара и наличие оценщика входят в identity допуска:
двухмодельное evidence не разрешает single-режим или другую будущую модель.

MOD-01..07 и MS-01..12 задают поведение и приёмку. Это самостоятельное расширение
существующего агента; оно не требует сначала реализовать все фазы 15–25.

---

# 103. Таск-трекер и очередь задач агентов

Архитектура, пользовательские сценарии и контракты определены в
[спецификации таск-трекера](specs/task-tracker.md), версия 1.0.
Реализация разрешена; её порядок и проверяемый статус указаны в §104.

Пользователь создаёт задачу с проектом, описанием результата, критериями,
приоритетом и бюджетом; отдельная команда разрешает выполнение из очереди.
Сохранение черновика не вызывает модели и инструменты. Доступны доска, список
и вид «Агенты»: у каждой задачи виден текущий экземпляр, его модель, последнее
действие, связь и причина ожидания, а у каждого экземпляра — текущая задача.

Трекер представляет канонические WorkItem/AgentAttempt и события оркестрации,
не создаёт второй изменяемой очереди. Запрос агента на задачу проходит диспетчер,
зависимости, права, приоритеты и budget gate. Назначение, резерв и событие
фиксируются одной транзакцией: два агента не получают одну активную попытку.
Прямое редактирование статуса не заменяет ownership или проверенный результат.

```text
Задача пользователя → очередь WorkItem → dispatcher / connectome policy
                                             ↓
                              назначение конкретного экземпляра
                                             ↓
                             работа → review → проверка результата
                                             ↓
                           доска задач / агент → текущая задача
```

Обязательны история назначений/изменений, pause/cancel/retry/reassign, контроль
версий и idempotency, reconciliation после потери lease, актуальный verdict
перед «Готово», сохранение частичных результатов и видимость блокировок.
Трекер может хранить несколько корневых целей; первая версия сохраняет один
активный workflow одновременно и несколько исполнителей внутри него.

| Новая фаза | Проверяемая поставка |
|---|---|
| 26 | Канонические карточки/backlog, создание задач, доска/список и фильтры |
| 27 | Очередь, получение задачи агентом, атомарное назначение и budget reservation |
| 28 | Текущие исполнители, live-события, передача, pause и восстановление |
| 29 | Review/verification/integration, завершение задач и полная приёмка |

Фазы 26–27 опираются на контракты/диспетчер ORC 15–16; 28 — на события и lease;
29 — на интеграцию ORC 17 и verification TST 21–22. UI фазы 20 переиспользует
трекер, а не создаёт отдельную доску. Зависимости компонентные: не требуется
сначала последовательно закончить все фазы 15–25. TRK-01..11 и TT-01..16
определяют требования и сценарии приёмки.

---

# 104. Сводный план поставки обновлённых спецификаций

Исполняемый [план работ](superpowers/plans/2026-10-16-specification-delivery.md)
объединяет §§100–103. Требования спецификаций сохраняются; прежняя оговорка
«только спецификация» описывает предыдущую поставку, а не текущий запрет реализации.

| Порядок | Область | Статус на начало работ | Условие завершения |
|---|---|---|---|
| 1 | §102, выбор моделей | Single/dual и generic HTTP контракты, CLI, catalogue/probes и две панели проверены; live single/dual repair и отдельный acceptance plan прошли | Single/dual/generic, независимые панели и совместимость истории проверены |
| 2 | Фаза 21 | Pytest-основа готова: owner contract/oracle, exact case IDs, baseline/target/regression/integration и evidence; runtime completion gate открыт | Снимок → реальный pytest → структурированное evidence → текущий verdict |
| 3 | Фазы 15/16 и 26/27 | SQLite-контракты, атомарное назначение/резервирование, fencing, restart и tracker projection проверены; executor/API/UI ещё не подключены | Единые задачи/попытки, транзакционная очередь и назначение без дублей |
| 4 | Фазы 22/17 и 28/29 | Не реализовано | Изоляция, восстановление, review, интеграция и verified completion |
| 5 | Фазы 18/23 | Не реализовано | Реальные события командного исполнения, shadow и проверки отказов |
| 6 | Фазы 19/24 | Не реализовано | Новый scoped admission после измеренных вмешательств и сравнений |
| 7 | Фазы 20/25 | Не реализовано | Единый интерфейс tracker/agents/verification, CLI/API и browser acceptance |

UI развивается вместе с backend. Числовая последовательность фаз не заменяет
компонентные зависимости. Выполненный пункт плана требует фактических результатов
проверок; заглушки, существующие legacy команды и документирование не закрывают фазу.
Незакрытые научные критерии прежних фаз остаются в §§90–99 и `docs/research.md`.

Срез Task5: `AttemptCortex` подключает существующий `run_agent` к общему
модельному ledger, каноническим objective/criteria и закреплённой загруженной
модели. Native metadata определяет доступный контекст; смена instance/config
отзывает допуск, слишком большой запрос блокируется до HTTP. Проверен один шаг
чтения в копии проекта. Это не закрывает supervisor, tool scope enforcement,
сообщения, review/integration и verified completion. `AttemptEnvironment` и
durable tool ledger проверены отдельно: разрешения/пути и расход
проверяются до действия, повтор и неизвестный исход не разрешают новую запись.
Поддержаны локальные файловые инструменты/pytest при чтении всего проекта;
ограниченные наблюдения, Git/сервисы и процессный supervisor ещё открыты.

Исследовательский срез 2026-10-16: [benchmark обратной связи](validation/2026-10-16-feedback-benchmark.md)
выполнил 296 запусков с английскими запросами и обучил отдельные checkpoints
неизменяемого FlyWire-графа. Повторное обучение улучшило простые задачи 14/20→20/20,
но реальный single-Qwen перенос ухудшился 2/2→0/2; оба обученных варианта отклонены
для замены исходного контроллера. Результаты и неудачи сохранены. Этот срез не
закрывает фазы 19/24 и научные критерии; очередь/runtime/integration остаются открыты.
