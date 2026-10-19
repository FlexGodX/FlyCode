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
Она является дополнением к этому плану. Реализация частичная; проверенный статус
и оставшиеся требования указаны в §104.

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
| 3 | Фазы 15/16 и 26/27 | SQLite-контракты, атомарное назначение/резервирование, fencing, restart и tracker projection проверены; внутренний dispatcher исполняет ограниченный writer/review/integration маршрут, общий host/API/UI ещё не подключены | Единые задачи/попытки, транзакционная очередь и назначение без дублей |
| 4 | Фазы 22/17 и 28/29 | Внутренний verified-delivery e2e, result invalidation/reopen, fresh reverify и ограниченная crash reconciliation реализованы; остальные acceptance ещё открыты | Изоляция, восстановление, review, интеграция и verified completion |
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
чтения в копии проекта. Это не закрывает сообщения, review/integration и verified
completion. `AttemptEnvironment` и
durable tool ledger проверены отдельно: разрешения/пути и расход
проверяются до действия, повтор и неизвестный исход не разрешают новую запись.
Поддержаны локальные файловые инструменты/pytest при чтении всего проекта;
ограниченные наблюдения и Git/сервисы ещё открыты. `Runtime` теперь запускает
`run_agent` в собственной группе процессов и обслуживает модели отдельным потоком
хоста; heartbeat10с/lease30с и Stop не ждут HTTP. `Workspaces` сохраняет исходные
dirty/untracked байты и права в отдельном checkout. Нормальный сырой результат
удерживается для будущего patch/review, а неизвестный эффект pytest — для сверки;
эта поставка не объявляет verified completion и не закрывает Task5 целиком.
Адресованный журнал теперь использует тот же Store: schema1-конверты, outbox/inbox,
дедупликация по попытке и ключу, TTL/hops/fanout и200 доставок на workflow.
Вопрос/первый ответ и receipt фиксируются атомарно; поздние ответы не переходят
в новую попытку даже того же instance. Явно разрешённая связь двух реальных
worker проверяет передачу активности в HTTP-контекст коллеги; недостаток места
откладывает сообщения без срыва обычного запроса. Разрешённый `send_message`
теперь выбирается моделью, проверяет явные связи хоста через RPC и расходует общий
бюджет инструментов до публикации. Принятые вопросы/ответы доступны следующим
решениям до закрытия/истечения срока без повторных receipt; отозванные сообщения
исключены. Служебная активность не вытесняет вопрос из ограниченного контекста;
непоместившаяся подсказка инструмента откладывается с отдельным событием.
После HTTP зависимости контекста повторно проверяются атомарно: истёкший срок или
отозванный отправитель блокируют кандидатов, сохраняя фактический расход модели.
Dispatcher, ожидание дочерних задач и UI ещё требуют подключения; review/cancel остаются данными,
а не обходом канонических команд и completion gate.

Срез Task6: schema8 сохраняет точные artifact/review/integration records и
единственный способ исполнения каждой попытки. Отдельный reviewer выполняет
owner-проверки, интегратор проверяет объединённую копию и исходную папку перед
атомарным `succeeded`. Применение сохраняет реально вытесненные файлы для отката
и обнаруживает позднюю запись через старый дескриптор. Проверен реальный Qwen
repair→review→integration→fresh source verification:3 модельных вызова и26
инструментов,49.01с, без утраты посторонних байтов. Подключение к API/UI и
остальные сценарии восстановления остаются открыты;
фазы17/22/29 целиком не завершены. Schema9 связывает завершение с текущими байтами,
семантическими ревизиями и доказательствами. Изменения атомарно инвалидируют
результат без возврата расхода или переписывания истории. Reopen сохраняет задачу,
попытки и deadline; старые auxiliary review снимаются только при точном совпадении
поколения. Совместимый уже применённый результат допускает новый независимый review
и свежую проверку без повторного применения patch. Пауза/отмена владельца блокирует
завершение; старое одобрение не заменяет новую проверку.
Зарегистрированный host-pytest теперь получает supervisor с унаследованным lock
и отдельным каналом допуска; durable receipt подтверждает остановку всей группы.
`Integration.reconcile` блокирует живого координатора и неподтверждённые процессы;
неизвестные внешние эффекты требуют доверенной сверки. После неё объектный откат
сохраняет поздние записи и блокирует заменённый пользователем файл. Повтор отката
не перезаписывает новые правки. Атомарная повторная проверка назначения исключает
возврат уже остановленной попытки в активное состояние. Interrupted pytest, включая
output limit, сохраняет uncertain; фактический расход не возвращается. Это
проверенный локальный протокол, не автоматическая сверка внешних сервисов.
Следующий срез сохраняет конфликт двух проверенных writer как каноническую
запись с обоими patch, точными review и исходной базой. Один инертный черновик
согласования либо явный capacity blocker, остановка интегратора и события
фиксируются атомарно; повтор после перезапуска не расходует модель. Два
совместимых patch проходят объединённые и исходные проверки; скрытая регрессия
от их объединения блокирует применение. Одобренное владельцем согласование
теперь выполняется в отдельном worker: host-проверяемый `read_file` с ID artifact
передаёт обе неизменяемые версии в контекст с provenance и одним списанием на
чтение. Поддержаны UTF-8 файлы до3500байт; бинарные/большие файлы, неверные
пути/хеши и недостаток контекста дают явный отказ. Новый patch получает отдельный
review и свежие проверки всех исходных требований на объединённой копии и
исходной папке. Старые попытки остаются interrupted/failed. Повторное открытие
корня проверяет точную цепочку поколений, сохранённые файлы, критерии и oracle;
свежий review и все check sets обязательны, расход/deadline не сбрасываются.
Это не закрывает Task6: standalone-review recovery, оставшиеся stop/deadline/rerun
сценарии, dispatcher/shared legacy admission и общий API/UI ещё открыты.
Проверки и сохранённые неудачные запуски:
[отчёт согласования](validation/2026-10-16-pinned-resolution.md).

Срез 2026-10-17 добавляет ограниченный stderr worker вне проекта и привязку
зарегистрированного pytest к конфигурации/корню snapshot. Проверены804 passed/
8 skipped (812 точных ID),85 сценариев extracted wheel и5 реального FlyWire.
Реальное согласование Qwen и повтор в том же workflow завершились отказом
`artifact_context_budget`; суммарно18 моделей/32 инструмента, исходные байты
сохранены, неполный workflow отменён без возврата расхода. Дублирование цели и
прочитанного факта измерено как обратная связь, но успех исправления не доказан.
Task5–8 остаются открытыми. [Отчёт](validation/2026-10-17-worker-diagnostics.md).

Следующий срез объединяет допуск canonical/standalone/probe в одном SQLite index
(schema10), убирает точные повторы фактов из копии контекста и передаёт цель один
раз. Проверены822 passed/8 skipped (830 точных ID),84 extracted-wheel и5 FlyWire.
Новый Qwen-прогон всё ещё не проходит: resolver16 раз читает один вход, второй
не прочитан, patch пустой. Общий расход22 модели/35 инструментов сохранён;
переполнения контекста и неизвестных вызовов нет, источник0 не изменён.
Workflow отменён без сброса бюджета. До нового обучения нужны наблюдаемые
кандидаты и выбор контроллера; этот срез не доказывает прирост эффективности.
[Отчёт общего допуска и контекста](validation/2026-10-17-shared-endpoint-context.md).

Исследовательский срез 2026-10-16: [benchmark обратной связи](validation/2026-10-16-feedback-benchmark.md)
выполнил 296 запусков с английскими запросами и обучил отдельные checkpoints
неизменяемого FlyWire-графа. Повторное обучение улучшило простые задачи 14/20→20/20,
но реальный single-Qwen перенос ухудшился 2/2→0/2; оба обученных варианта отклонены
для замены исходного контроллера. Результаты и неудачи сохранены. Этот срез не
закрывает фазы 19/24 и научные критерии; очередь/runtime/integration остаются открыты.

Срез standalone-review recovery 2026-10-17 сохраняет точную привязку входа/oracle
и владение координатором (schema11). После сбоя `Reviews.reconcile` исключает
живого координатора, проверяет все группы и удерживает неизвестные эффекты до
доверенной сверки. Старые незавершённые review без доказательства владения
блокируются; recovery не создаёт approval и не запускает попытку повторно.
Проверены827 passed/8 skipped (835 ID один раз),58 extracted-wheel,5 FlyWire,
Ruff/mypy45. Отдельный простой Qwen smoke прошёл за95.860s с3 моделями/26 tools
и20 подтверждёнными остановками check; это не успех прежнего pinned-conflict
случая и не dispatcher. Первые2 Docker failures и исправленный порядок запуска
сохранены. [Отчёт](validation/2026-10-17-review-recovery.md). Дополнительная
обратная связь прежнего отказа показывает оба безопасных read-кандидата во всех
16 ответах; одинаковая identity и отсутствие habituation для бесполезного
успешного чтения требуют отдельного regression. Обучение и promotion из этого
не следуют; Task5–8 остаются открытыми.

Срез pinned-read feedback 2026-10-17 различает immutable inputs по artifact ID,
сохраняет фактический success отдельно от полезности и снижает вес бесполезных
повторов вычитанием, включая нулевой/отрицательный bias. Проверены837 passed/
8 skipped (845 ID),150 extracted-wheel,5 FlyWire, Ruff/canonical mypy45;3 прежних
ошибки типизации Functional совпадают с неизменённой базой. Новый Qwen-сценарий
всё ещё FAIL:285.625s,9 моделей/23 tools. Resolver читает только A дважды и угадывает
patch3 без B/рабочего файла; local verified_completion не заменяет требуемые
наблюдения. Canonical проверка отвергает его до review/integration, source0
сохранён. Все вызовы/группы завершены, резерв0, owner cancellation сохраняет
patch/history/spend. Нативный выигрыш habituation не доказан; нужен host gate
обязательных входов. Топология/checkpoints/defaults не меняются, Task5–8 открыты.
[Отчёт обратной связи](validation/2026-10-17-pinned-read-feedback.md).

Срез mandatory-resolution inputs 2026-10-17 связывает реальные чтения с точными
attempt/fence/call, pin/path/hash и workspace. Host проверяет их перед мутацией,
публикацией, fresh review, интеграцией и актуальностью результата. Ошибки чтения
сохраняются как диагностика; старые approvals не обходят отсутствие наблюдений.
Проверены851 passed/8 skipped (859 ID),17 extracted-wheel,5 FlyWire, Ruff/mypy47;
история/расход/байты сохраняются. Новый Qwen-case FAIL488.441s,15 моделей/27 tools:
оба pin и workspace прочитаны, но metadata кандидата вложена в edit_file args.
Whitelist запрещает действие до записи; необработанный permission завершает worker.
Нужна корректируемая обратная связь о malformed args без ослабления полномочий.
Все16 model outcomes известны,3 worker и8 checks остановлены, source0 сохранён;
owner cancellation не меняет попытки/артефакты/расход. Task5–8 и admission открыты.
[Отчёт host gate](validation/2026-10-17-resolution-input-gate.md).

Срез candidate-metadata feedback 2026-10-17 возвращает корректируемую английскую
ошибку для пяти неверно вложенных полей кандидата только после полной проверки
самого вызова. Нет записи/списания, скрытого удаления аргументов или расширения
прав; actual HTTP/process fixture подтверждает следующий исправленный запрос.
Проверены856 passed/8 skipped (864 ID),31 extracted-wheel,5 FlyWire, Ruff/mypy47.
Текущий Qwen-case не запущен: исходный30s probe и один явный повтор завершились
APITimeoutError. Terminal provider output подтверждает остановку и обрезанный
JSON; trusted HostTransport сохраняет failed и776/874 токенов. Неизвестных
вызовов0, пользовательских workflow/tools0, обхода compatibility gate нет.
Все10 checkpoints неизменны; Task5–8 и научный допуск открыты.
[Отчёт корректируемых аргументов](validation/2026-10-17-candidate-metadata-feedback.md).


Срез owner-bound worker dispatch 2026-10-17: schema12 хранит неизменяемые
одобрения точной папки и контракта задачи. Dispatcher выбирает пару task/instance
по общей baseline/aging и оставшемуся бюджету, затем запускает существующий
Runtime без ручного claim. Изменённый источник, устаревшее одобрение и пауза/отмена
до spawn не создают нового writer. Capture не удерживает SQL-транзакцию; прежние
лимиты, deadline и резерв20% сохранены. Финальный полный набор886 passed/8 skipped
(894 ID), wheel40, реальный FlyWire5, Ruff/mypy48 и CLI10 прошли. Один английский
Qwen smoke через tick создал приватный исправленный artifact за81.14с,3 модели/5
инструментов; исходные0/dirty байты сохранены, группа остановлена. Компактный
primary probe прошёл; предыдущие два timeout остаются неудачами. Smoke завершён
отменой без сброса расхода и не заявляет review/integration/verified completion.
Полный dispatcher, child wait, neural orchestration и CLI/API/UI ещё открыты;
Tasks5–8 не завершены. [Отчёт](validation/2026-10-17-bound-worker-dispatch.md).


Срез approved pipeline dispatch 2026-10-17: schema14 хранит точные review/
integration bindings и первоначальное owner approval для aggregate root и трёх
sibling этапов writer/reviewer/integrator. Последующие runtime ticks закрепляют
текущую остановленную публикацию и её принятый review, атомарно создают input pins,
binding, ready, receipt и outbox без поддельного owner scope. Изменение oracle,
общего бюджета, task/profile/source блокирует старый допуск; семантика DAG не
меняется от закрепления ожидаемых входов. Свежий dispatched review/reintegration
после reopen сохраняет provenance автора и старый результат.
Проверены931 passed/8 skipped (939 ID), wheel87,138 Python/README bytes, CLI10,
FlyWire5, Ruff/mypy48. Actual English Qwen pipeline58.348с:3 модели/34 tools,
три остановленные успешные попытки, четыре succeeded задачи, текущий verdict,
ноль ручных claim и owner-команд после исходного approval. Dirty/README сохранены,
source1 применён только интегратором; резерв0, неопределённости0. Вместе с probe
четыре завершённых model calls/5105 токенов. Повторные три неудачных worker-теста
остаются расходом, а не выигрышем эффективности. Независимая сверка прошла.
Маршрут ограничен одним writer/reviewer/integrator. Общий legacy/canonical source
admission, процессная custody старого RunManager, multi-writer recipes, child wait,
manager recovery и CLI/API/UI ещё требуют подключения. Task7 требует отдельного
encoder/readout/replay/admission; старые single-agent инструменты его не заменяют.
Топология/checkpoints/defaults не меняются, Tasks5–8 остаются открыты.
[Отчёт](validation/2026-10-17-approved-pipeline-dispatch.md).

Проверенный срез shared source admission: schema15 связывает старые
чаты и интегратор общими claims папок/вложенных путей и физическими identity.
Локальные инструменты и наблюдения старого чата получают process custody до
spawn; поздний HTTP usage остаётся связан с run ID. Все незавершённые записи,
включая interrupted и историю за пределами50 чатов, проверяются при restart;
неизвестный прежний владелец удерживает quarantine. Apply и каждый rollback
rename проверяют общий допуск; подмена корня блокирует запись вне SQL.
Снимок не выполняет настроенные Git fsmonitor/filter. Объединённые targeted
проверки45 прошли66.79с; отдельный реальный crash RunManager с потомком pytest
прошёл7.19с. Первый полный набор сохранил3 compatibility failures, исправленные
условной передачей on_prepare; следующая версия прошла964/8 skip,167 wheel,
CLI10, graph5 и реальные pipeline/Stop. Последующая проверка воспроизвела подмену
корня после последнего guard; исправление закрепляет root FD для rename и mkdir,
38 связанных проверок и Ruff/mypy53 проходят. Финальные978 ID проверены по одному:
970 pass/8 ожидаемых skip за642.303с; wheel191, CLI10, реальный FlyWire5 проходят.
Web53 выполняются последовательно после трёх non-web групп; прежний timeout
неизменённого15с observer и все ранние исходы сохранены. Реальная English цепочка
прошла66.532с (3 model/34 tool), Stop21.495с с ответом0.002014с и1333 поздними
токенами. Независимо проверены3 released claims/27 stopped receipts, текущий
verdict, сохранность user bytes и нулевые неизвестные расходы/резервы.
[Отчёт](validation/2026-10-17-shared-source-admission.md).
Это не закрывает Tasks5–8, multi-writer/child wait, общий API/UI или нейронный
оркестратор. [План среза](superpowers/plans/2026-10-17-shared-source-admission.md).

Проверенный следующий срез schema16/Tasks HTTP API отделяет immutable physical
project UUID от локальных UI selectors. Scope выдаётся по текущим root/ancestor
pins; одинаковые `.` разных папок не смешивают задачи. Drafts/owner commands,
comments/history, keyset projections и SSE используют общий Store. Два actual
HTTP RED на reconnect/scope expansion исправлены разделением scan/delivery cursor;
снимок не подтверждает пропущенные события. Python web теперь входит в identity.
Финально1017 ID:1009 pass/8 ожидаемых skip за651.131с;282 wheel, CLI10, real graph5,
Ruff/scoped mypy65 проходят. Внутренняя English pipeline55.721с и legacy Stop14.976с
прошли; это не исполнение через новый HTTP adapter. Независимо подтверждены
232 hashes,3 released claims/27 stopped receipts и нулевые неизвестные расходы.
Host leadership/recovery, physical authority перед execution, общий UI/CLI,
verification routes и нейронный Task7 остаются открыты.
[Отчёт](validation/2026-10-17-canonical-project-tasks-api.md),
[план среза](superpowers/plans/2026-10-17-canonical-project-tasks-api.md).

Следующее проверенное runtime-уточнение повторно сверяет registered physical
source перед dispatch/launch, после workspace capture, перед baseline/effects
интегратора. Byte-identical root/ancestor replacement больше не даёт новый запуск.
Main83 focused cases и13 новых из wheel проходят; Ruff4/mypy52,145 package files,
README metadata/CLI10 и233 hashes проверены. Полный1017/wheel282/native прогон
принадлежит родителюb479926 и не заявляется повторённым для этих новых байтов.
Принятая private-copy работа может продолжать расход; потеря authority удерживает
reconciling/claim до trusted repair, в том числе блокирует автоматический rollback.
Worker crash custody, host leadership, общий UI/CLI/API и Task7 остаются открыты.
[Отчёт](validation/2026-10-17-registered-execution-authority.md).

Канонический worker сохраняет точный ProcessRef до spawn и восстанавливает
остановку через guardian/custody, сохраняя неизвестные HTTP/pytest расходы.
Main176 и isolated176 проверок,15 wheel native, Ruff7/mypy52/CLI10 проходят;
полный suite/graph/realLM на этом срезе не повторялись. Unique host и общая
execution/verification проводка ещё открыты; Tasks5–8 не закрыты.
[Отчёт](validation/2026-10-17-worker-custody.md).


Проверенный общий экран `/tasks` добавляет board/list/instances, фильтры и
черновики/команды/комментарии/историю из canonical API. Main и extracted-wheel
browser проходят реальные lost-reply/409/stale-response/history сценарии,
390/320 layout, сохранность Desktop/projects/projectless; независимый Store audit
подтверждает отсутствие inference и неизменные user bytes. Полные execution/
verification UI и host, расширенные поля/фильтры и нейронный допуск ещё открыты.
Tasks5–8 не закрыты; non-UI suite здесь не повторялся.
[Отчёт](validation/2026-10-17-tasks-interface.md).

Следующий проверенный срез schema17 сохраняет явно проверенные роли моделей с
точными completed probe receipts и configuration hash. Restore не вызывает HTTP;
read-only snapshot не захватывает writer lock, stale CAS не перезаписывает новый
допуск/отзыв. Malformed output и увиденная смена конфигурации при занятом endpoint
отзывают прежнюю роль. Исправлена реальная legacy race: terminal API публикуется
после source settlement и успешного сохранения истории под lock.
Финально1076 ID:1068 pass/8 ожидаемых skip,0 failures/errors за1013.932с,
246 неизменных файлов; extracted-wheel50 без deselection,156 точных package files,
CLI10, Ruff15/scoped mypy6, реальный FlyWire5 и2 явных Qwen/Jev probe проходят.
Свежий wheel-процесс восстанавливает роли с отключённой сетью без новых вызовов.
Старый полный1058/2 failures/8 skip сохранён и не переименован в успешный результат.
Это зависимость shared coordinator, а не его реализация; будущая leadership
миграция использует schema18. Tasks5–8 и §§90–99 остаются открытыми.
[Отчёт](validation/2026-10-17-durable-model-catalogue.md),
[план среза](superpowers/plans/2026-10-17-durable-model-catalogue.md).

В проверенном продолжении schema18 добавляет физическое
владение общей базой: точный kernel lock, внешний path pin и поколение Host.
Проверки охватывают native/HTTP утрату владения, поздний расход, запуск pytest
и принятие артефакта после подготовки файлов; исходные RED сохранены.
Повреждённая/односторонняя инициализация требует trusted repair, а не автоматической
перезаписи. До финального metadata исправления прошли560 orchestration проверок;
после композиции исправления на main прошли106 целевых проверок. Exact IDs и
хэши этих исторических срезов проверены отдельно.
Внутренний фоновый coordinator исполняет одобренный writer/review/integration
маршрут через независимые Store connections, сохраняя heartbeat при долгой проверке.
Native проверки охватывают закрытие клиента, pause, drain до фактического HTTP
исхода и восстановление worker/review/integration при недоступной исходной папке.
Поздний acknowledgement и освобождение системного lock повторно запускают сверку;
неизвестные эффекты удерживают резерв. UI/CLI attachment и защищённый HTTP Stop
для общего host ещё не реализованы. Финально1130 exact ID:1122pass/8 ожидаемых
skip,0failures/errors,250 неизменных файлов за1008.627с. Extracted-wheel108/CLI10,
158 точных package files/README, Ruff/scoped mypy13 и реальный FlyWire5 проходят.
Реальный English Qwen pipeline завершён за74.002с с3model/34tool calls и текущим
verified результатом; первый probe timeout сохранён со сверенными673 входными
токенами, затем один явный повтор прошёл. Исходный полный1121pass/1failure/8skip
сохранён; исправлены только две ссылки тестовой подмены. Tasks5–8 и §§90–99
остаются открытыми; научного promotion нет.
[План продолжения](superpowers/plans/2026-10-17-managed-coordinator.md).
[Рабочий отчёт](validation/2026-10-17-managed-host-foundation.md).
[Проверка coordinator core](validation/2026-10-17-managed-coordinator.md).

Следующий ограниченный срез добавляет защищённый локальный read-only канал
подключения к standalone coordinator: физическая база/поколение, свежий challenge
и раздельные HMAC, закрытые права descriptor/socket/parent,8 peers и общий deadline.
Клиент не восстанавливает очередь и не завершает Host. Native RED сохраняют
отсутствующий endpoint, ложный atime revoke и две ошибки startup cleanup.
Финально1142 exact ID:1134pass/8 прежних ожидаемых skip,0fail/error за1085.932с;
252 неизменных файла. Wheel120 exact/159 package files/README, CLI10, FlyWire5,
Ruff/mypy3 проходят. Actual English Qwen pipeline85.104с/3model/34tools сохраняет
одну сессию при отключении клиента;4 HTTP receipts (1probe+3workflow) сверены с
LM Studio, без retry/fallback. Независимый аудит3561 checks/698 stable hashes
подтверждает ограниченную приёмку. UI пока не запускает/подключает host;
защищённый HTTP Stop двух приложений, общий verification UI/CLI/API и нейронная
оркестрация остаются открытыми. Tasks5–8 и §§90–99 не закрыты.
[Отчёт endpoint](validation/2026-10-17-coordinator-attachment.md),
[план attachment](superpowers/plans/2026-10-17-coordinator-attachment.md).


Protected application attachment and historical Stop are now validated (schema19):
trusted detached launch, four-field status, immutable prior-resolution Stop grants,
protected pause/cancel after root replacement, actual two application processes and
SIGKILL/restart authentication rejection. Reaper exhaustion has retained RED and
pre-spawn ownership fix. Final1158 exact IDs:1150pass/8expectedskip/0fail/error,
1091.355s against255 unchanged files; extracted-wheel133/159package files/
README,CLI10,FlyWire5,Ruff/mypy4 pass. Actual English Qwen pipeline
84.158s/3model/34tools and4 independently matched provider receipts
pass with preserved owner bytes and zero reserve/uncertainty. Native Stop response
is less than1s during review/integration, including replaced roots; unknown
pytest effects remain held. UI buttons/CLI attachment, shared verification and
neural orchestration remain open; Tasks5–8 and §§90–99 are not closed.
[Application report](validation/2026-10-17-application-coordinator.md).


Validated Tasks coordinator controls (2026-10-18): explicit connection/status,
minimal durable Stop discovery after root loss and page/app restart, identical
lost-reply pause/cancel replay, revision conflicts and private-content purge.
Final1169 exact IDs/1161pass/8expectedskip/0fail/error,
1114.357s;263 unchanged frozen files. Wheel165/
159 exact package files/README,CLI10,FlyWire5,Ruff/mypy3 pass.
Main6 native browser cases plus packaged attachment/Stop/Tasks, classic/Desktop,
non-Git projects and projectless chats pass. Real English Qwen pipeline
110.966s/3model/34tools and4 matched provider receipts preserve owner
bytes and zero unknown/reserve. Stop response40.3–53.3ms;
49 actual custody receipts and597 evidence hashes are checked.
Canonical CLI/shared verification/neural admission remain open; Tasks5–8 and
§§90–99 are not closed. [Report](validation/2026-10-18-coordinator-controls.md).


Срез2026-10-18: публичный `flycode tasks` использует общий Store для черновиков,
команд, комментариев, событий, агентов, подключения службы и Stop после утраты
папки. Проверены native CLI/web, ограничения доступа и затрат; transient idle
countdown исключён из model fingerprint, реальные config changes отзывают допуск.
Итоги final full/wheel/graph/actual-model:
[проверенный CLI](validation/2026-10-18-canonical-cli.md). Общие verification
plans/start/cancel/rerun/artifacts и оставшиеся Tasks5–8 не закрыты.


Срез2026-10-18, каталог verification (schema20): неизменяемые снимки/профили и
текущий owner approval хранятся в общем Store; descriptor-backed копии не следуют
подменённым каталогам. Регистрация не запускает workflow, модель или инструменты.
Full1236 exact IDs/1228pass/8expectedskip/0fail/error,1824.535с;270 неизменных файлов.
Focused68, wheel62/162 package files/README/CLI, FlyWire5 и Ruff13/scoped mypy4
проверены. Общие executable plans/start/cancel/rerun/Host recovery/API/CLI/UI
остаются следующим обязательным этапом; фаза25/TST13 и Tasks5–8 не закрыты.
[Отчёт каталога](validation/2026-10-18-verification-catalogue.md),
[полный план управления](superpowers/plans/2026-10-18-verification-control.md).


Срез2026-10-18, управление verification (schema21): executable plans,
CAS/idempotent start/cancel/rerun, накопительные бюджеты, реальные pytest процессы
в общем Coordinator pool4, recovery и устойчивый Stop. Защищённые API/CLI/SSE,
выдача отчётов, воспроизведение и экран `/verification` доступны из classic и
Desktop. Focused118, delta18, staged CLI4, extracted-wheel18 проходят; числа
пересекаются. Final wheel169 exact files/README и packaged browser2plans/6runs,
Desktop/projects/projectless compatibility, Ruff53/scoped mypy24 проверены.
Полный suite, graph и live-model в этом срезе не запускались по указанию
пользователя о проверках только изменённой функции. Общие Tasks5–8, автоматическое
представление pipeline review/integration, нейронный допуск и §§90–99 остаются
открытыми; параллельный мозг не включён в эту поставку.
[Отчёт управления](validation/2026-10-18-verification-control.md).


Срез2026-10-18, независимая команда: version2 `Pipelines.approve_team` допускает
до трёх независимых sibling writer/reviewer пар и одного общего интегратора.
Проверенный маршрут из двух пар делает5 реальных process attempts/3 receipts;
совместный результат становится current только после всех review и проверок.
42 v1/v2 cases и6 extracted-wheel проходят,12 файлов неизменны, package169/
README/CLI/Ruff3/mypy2 проверены. Падение review, конфликт и скрытая регрессия
объединения сохраняют исходные файлы. Зависимости по-прежнему требуют succeeded;
произвольный DAG/child-wait/retry/replan и оставшиеся Tasks5–8 не закрыты.
Проверялись только затронутые функции; модели — HTTP fixtures, не live admission.
[Отчёт команды](validation/2026-10-18-team-pipeline.md).


Срез2026-10-18, завершение отдельной задачи (schema22): неизменяемый receipt
подтверждает остановленного автора, точный независимый review, критерии и evidence.
Задача становится succeeded без применения файлов и завершения workflow;
изменения/отмена отзывают зависимые назначения, сохраняя расход до остановки.
Native40, migrations8, final24 overlapping correction cases и final wheel10
проверены; package170/README RU/EN/CLI/Ruff19/mypy7 совпадают с изолированной копией.
Первый compatibility run226pass/4fail сохранён: legacy reopen исправлен, cancel
проверяет отзыв fence, два harness используют точный импорт. Все четыре повторно
проверены точечно; повтор всего230 не заявляется. Task2 имеет реальный RED копии
зависимого writer: нужны композиция входов и последовательная интеграция.
Общий DAG/child-wait/replan/UI результатов и Tasks5–8 остаются открытыми.
Параллельный мозг/CLI/MaleCNS не входят в поставку; full/live-model/graph не запускались.
[Отчёт завершения задачи](validation/2026-10-18-task-completion.md).

Срез2026-10-18, композиция зависимых задач (Task2): назначение закрепляет точные
receipts предшественников, отдельная копия получает их проверенные изменения,
интеграция проверяет один исходный→итоговый delta и все критерии до/после применения.
Последовательные правки, ветки, конфликты/исправление, registered imports и оба
варианта root reopen проверены. Initial30, compatibility17, пересекающиеся
correction deltas3/2/1/1/5/6, wheel6 и final scope-corrected wheel2 проходят; package171/README/CLI,
279 неизменных inputs, Ruff11/scoped mypy10 проверены. Все сбои сохранены.
Общий заранее одобренный DAG, child-wait/retry/replan, общие result views,
нейронный допуск и остальные Tasks5–8 не закрыты. Только feature tests;
параллельный мозг/CLI/MaleCNS исключён из поставки.
[Отчёт композиции](validation/2026-10-18-dependent-composition.md).

Срез2026-10-18, ограниченный DAG (Task3): version3 `Pipelines.approve_dag`
допускает ациклические зависимости между тремя заранее одобренными writer/reviewer
парами. Диспетчер автоматически завершает точные проверенные outputs и закрепляет
новые привязки зависимых задач; маршрут A→B→join делает7 process attempts без
ручных назначений. Registered project, race/outbox restart, failed prerequisite,
независимый прогресс, пауза во всех claim routes, изменения source/criteria/evidence
и общий исчерпанный бюджет проверены. Final25, v1/v2 compatibility6, Store limits/
expired-fence3 и extracted-wheel7 проходят; counts пересекаются. Package171/
UTF-8 README/CLI/Ruff4/scoped mypy3 и280 неизменных inputs проверены.
Произвольная иерархия сверх трёх пар, child-wait/retry/replan, общие result views
и оставшаяся aggregate/research приёмка открыты. Пауза блокирует новые назначения
потомков; каскад остановки уже работавших потомков здесь не реализован.
Full/live-model/graph/UI/training не запускались; параллельный мозг исключён.
[Отчёт ограниченного DAG](validation/2026-10-18-dependency-recipe.md).

Срез2026-10-19, иерархия DAG (продолжение Task3): version3 поддерживает вложенные
структурные группы в прежних пределах64 задач, глубины3 и8 детей. Реальный граф
из13 задач:4 автора,3 группы,9 attempts; каждую группу закрывают только её
собственные merged/applied verdicts, а не успех детей. Нулевой бюджет, отсутствие
профиля и прошлых попыток не позволяют группе стать исполнителем.
Expanded7pass/1fail363.11s сохраняет остановку недофинансированного тестового
сценария; corrected positive1/124.64s, compatibility20/367.52s и extracted-wheel4/
173.03s проходят. Whole8-case native rerun не заявляется. Расход успешного workflow:
4 model/93 tools, без резервов и unknowns. Package171/UTF-8 README/CLI/Ruff7/scoped
mypy6 и281 неизменный packaging input проверены; шесть runtime файлов совпадают
между финальными границами. Все сбои и неполный первый static review сохранены.
Task4 с отдельным resumable parent/child-wait/retry/replan, общие result views,
нейронный допуск и оставшаяся aggregate/research приёмка открыты. Full/live-model/
graph/UI/training не запускались; параллельный мозг/schema23–25 не включены.
[Отчёт иерархии](validation/2026-10-19-dependency-hierarchy.md).


Срез Task4 child-wait,2026-10-19: version4 `approve_parent` закрепляет остановленную
частичную работу, освобождает слот на время проверенных детей и создаёт новую
fenced попытку родителя с прежним общим расходом и deadline. Его собственный review
и общий merged/applied verdict обязательны. Реальный маршрут:5 задач,6 attempts,
3 model/36 tools, без резервов/unknowns; потеря checkpoint инвалидирует результат.
Native34/158.57s, compatibility12/118.64s и extracted-wheel6/
89.32s проходят; Ruff12/scoped mypy10/package172/UTF-8 README/CLI и304
неизменных входа проверены. Counts overlap. Scope/Host/graph отзываются до новых
действий; оплаченные поздние outcomes сохраняются для остановки и учёта расхода.
Все REDs и замечания независимого review сохранены в
[отчёте](validation/2026-10-19-parent-continuation.md).
Bounded retry/replan, общие result views и aggregate/neural/research приёмка остаются
открытыми; full/live-model/graph/UI/training не запускались. Параллельные правки
мозга/schema23–25 сохранены и исключены из проверенного пакета и коммита.

Поставка ограниченных повторов после отклонённого review,2026-10-19:
version4 принимает явный owner `retry_policies`, сохраняет расходы/срок/max3,
восстанавливает точные неудачные изменения и передаёт диагностику в фактический
HTTP-запрос. Новый fence, свежие review и общая интеграция обязательны; история
отказов/evidence остаётся частью проверяемой цепочки. Без политики повторов нет.
Native44 и compatibility54, final policy/unknown delta11, extracted-wheel46 и
combined-brain interop3 проходят; Ruff12/mypy11, package173/UTF-8 README/CLI и177
unchanged inputs проходят. Счётчики пересекаются; native44/compatibility54 относятся
к границе до последней instance-validation правки, wheel46 проверяет финальный
пакет. Реальные процессы/pytest/HTTP используют тестовые ответы модели; live LM и
научный допуск не заявляются. Все отказы и варианты исходников сохранены в
[отчёте](validation/2026-10-19-review-retries.md). Параллельные изменения мозга
сохранены в общей папке и исключены из публичного пакета. Graph replan, общие
представления результатов и aggregate/neural/research acceptance остаются открыты.


Поставка owner-approved версий графа,2026-10-19: внутренний `Pipelines.replan`
добавляет до двух immutable версий v4 для целей/зависимостей существующих ещё
не завершённых дочерних задач. Все ID, критерии, права, бюджеты, расходы, срок,
max3 и checkpoint родителя сохраняются. Точные grants/stages/review/proofs
закреплены в append-only receipts; новые artifacts проверяют graph lineage.
Подтверждённые Stop/settlement и полный owner CAS обязательны. Новый автор
начинает с исходной копии и проходит свежие review/combined/applied checks.
Final extracted-wheel33 и добавленный same-wheel delta1 покрывают все34 текущих
feature cases; native30/graph2 route1/race+HTTP2, compatibility46+28 и combined-brain2
проходят. Ruff11/scoped mypy10/package174/UTF-8 README/CLI/307 unchanged inputs
проходят; recursive mypy22 errors в неизменённых модулях сохранены в
[отчёте](validation/2026-10-19-graph-replans.md). Источники отдельных прогонов
и пересечение счётчиков явно отмечены. Параллельные изменения мозга/schema
сохранены отдельно от публичного пакета. New-branch graph admission, controller
proposals, общие result views и aggregate/neural/research acceptance открыты;
весь Task4 и полная цель не закрыты. Full/live-LM/graph/UI/training не запускались.


Поставка новых owner-approved веток графа,2026-10-19: `new_branches` добавляет
пары writer/reviewer и разрешает временные `new:<index>` зависимости существующих
и новых задач в серверные ID одной транзакцией. Исходный граф, обязательные
неудачные задачи, права/критерии, расходы/max3/срок сохраняются; бюджет интегратора
сам не расширяется. Counter/tasks/bindings/receipt/events атомарны, сохранённая
модель receipts и schema22 не меняются. Final extracted-wheel36 проходит324.15s;
native30 и отдельные deltas, old-replan34, combined-brain3 и retained hashes
проходят. Ruff4/scoped mypy3/package175/UTF-8 README/CLI/309 unchanged inputs
проходят; все14 ранних границ, ошибки fixtures и исходное исчерпание25-tool
интегратора сохранены в [отчёте](validation/2026-10-19-new-graph-branches.md).
Combined проверяет197 source files текущих параллельных изменений, которые
сохранены в общей папке и исключены из публичного пакета. Проверки границ6tasks/
5children не объявляются literal64-task stress. Combined Task4 acceptance,
общие result/command views, controller proposals и aggregate/neural/research/
final acceptance остаются открыты. Full/live-LM/graph/UI/training не запускались.

Поставка общих result projections,2026-10-19: completion задачи и integration
workflow, актуальность, lineage входов/continuation/retry/graph, review/evidence,
активное ожидание и last_action/blockers читаются из общего Store для CLI/API/
Tasks/agents. Metadata/cursor фиксируются одной короткой SQL-транзакцией; реальные
Completions.current/Integration.current проверяются вне SQL и заново для каждой
карточки. Изменённая ревизия или поздняя потеря evidence не сохраняет current=true.
Старый checkpoint после resume остаётся в lineage, но не в активном ожидании;
планируемые pipeline этапы не предлагают ручной enqueue. Final extracted-wheel13
проходит219.11s, compatibility28 —15.14s, браузер5состояний1440/390/320/keyboard/
Stop, старый Tasks browser, Ruff4/scoped mypy3/package176/UTF-8 README/CLI и312
unchanged inputs проходят. Frozen combined-brain199 source:3проверки44.79s плюс
финальная delta1 —34.66s. Все21граница и обе wheel-версии сохранены в
[отчёте](validation/2026-10-19-task-result-projections.md); счётчики пересекаются.
Safe result artifact drilldown, public owner approval/replan и combined Task4,
controller/admission/matrix/research/final acceptance остаются открыты. Whole
Task5 и полная цель не закрыты; full/live-LM/graph/training не запускались.

Поставка материалов результатов,2026-10-19: общий CLI/API/Tasks каталог и
скачивание точных исторических baseline/target patch и отчётов независимой/
совместной/применённой проверки. Только server IDs; исторические привязки,
права/хеши и bounded regular/no-follow directory-FD чтение вне SQL. Скачивание
не расходует бюджет и не выдаёт pass. Утрата старого manifest не скрывает целые
более поздние материалы; завершение реального review не отменяет чтение файла
автора. Запоздалые scope errors не скрывают новую карточку/Stop. Final wheel7
проходит127.33s, targeted compatibility14 —12.72s, браузер1440/390/320/keyboard/
download/races и старый Tasks browser проходят. Ruff6/scoped mypy5/package177/
UTF-8 README/CLI/315 unchanged inputs и frozen combined-brain203 source2 проходят.
Все15 ранних границ сохранены в [отчёте](validation/2026-10-19-task-result-materials.md),
счётчики пересекаются. Public owner approval/replan/live-Host/oracle bridge,
combined Task4/controller/admission/neural/research/final acceptance остаются
открыты. Full/live-LM/graph/training не запускались, полная цель не закрыта.

Checked owner-import prerequisite,2026-10-19: expected oracle fingerprint is
compared before approval/head writes; existing registered-plan final SQL pins the
full profile row/project column. Final wheel22, native feature22, compatibility43,
Ruff3/mypy2, package177/316 unchanged inputs/UTF-8 README/CLI and frozen brain204
interoperability22 pass. Counts overlap; invalid acceptance-fixture RED and static
format correction are retained in [the report](validation/2026-10-19-owner-import-pins.md).
This is not owner parent/replan final pin wiring, staging/RPC or public commands;
combined Task4, aggregate/controller/neural/research/final gates remain open.

Checked parent/replan profile pins,2026-10-19: trusted owner calls now verify
exact registered criteria/trust/reruns outside SQL, then pin full profile/head/
project in final CAS before publication or new branch ID allocation. Final wheel14
pass38.14s, compatibility28 pass150.13s, frozen brain207 interoperability14
pass43.25s, Ruff4/mypy3/package178/318 unchanged inputs/UTF-8 README/CLI pass.
The actual stopped-parent→replan→fresh review→current integration route spends3
model/36tool calls,6attempts, preserves deadline/dirty bytes and ends reserve-free.
Counts overlap. [Report](validation/2026-10-19-owner-recipe-profiles.md) retains all six source boundaries.
Safe staging, live owner transport/preparation and public controls remain open,
as do combined Task4 and aggregate/controller/neural/research/final acceptance.

Checked live owner commands,2026-10-19: signed bounded requests use the existing
Host and server-selected scope/roots/profiles. Preview has no grants, bindings,
oracle activation or execution constructors. Explicit prepare supports partial
receipts; immutable intent and exact lower-level receipts survive lost response
and real Host restart. Anchored regular-file transfer retains independent heads
and fails closed on altered pending bytes. Final Host/project/task/workflow/profile
CAS prevents late publication. CLI and protected HTTP share these commands.

Final wheel43 pass108.15s, compatibility98 pass56.49s, frozen combined-brain215
source43 pass116.71s, Ruff12/mypy10/package182/324 unchanged inputs/UTF-8 README/
CLI pass. Counts overlap. Native English retry/new-branch/restart/fresh-review/
current integration:7tasks,10attempts,5model/52tool calls plus1 separate probe;
original1800s deadline/limits/dirty bytes remain, no reservations or uncertainty.
All12 early boundaries, both first-wheel rounds (including97/98 regression),
configuration and report-preparation failures are retained in [the report](validation/2026-10-19-owner-commands.md).
Tasks owner confirmation/browser controls and automatic coordinator-process/UI
combined acceptance remain open, as do aggregate/controller/admission/neural/
research/full/final gates. No live-LM/graph/training/scientific claim.

Checked Tasks owner controls,2026-10-19: separate preview/preparation/approval/
stopped-parent replan/new-branch dialog, exact pending replay after reload and
independent shared Stop are wired to the live owner API. Native browser failures
also fixed catalogue cursor lifetime and stale completion-invalidation publication.
Final wheel4 owner scenarios +3 old result/material browser regressions pass;
frozen combined-brain219 source3 owner scenarios +3 old regressions pass. Counts
overlap. Native7tasks/8attempts/4model/49tools +1 separate probe reaches current
applied integration, preserves spending/deadline/owner bytes, then correctly revokes
currentness after an explicit owner edit. Keyboard/1440/390/320, Ruff3/mypy2,
JS3/package184/328 unchanged inputs/UTF-8 README/CLI pass. Only interface tests ran.
All failures are retained in [the report](validation/2026-10-19-owner-tasks-controls.md).
Automatic coordinator-process/UI/retry/restart combined acceptance, actual existing
child→new prerequisite UI execution, a gated negative-read race, aggregate/controller/
admission/neural/research/full/final gates remain open. No biological or live-LM claim.

Checked owner browser follow-up,2026-10-19: actual existing-child→new-helper
UI dependency reaches fresh review/current applied integration. A deterministic
API negative-read gate crosses source installation and final publication outside
SQLite: retained119176d completion refresh fails as expected; current708051a CAS
passes and still invalidates a later real owner edit. Exact published wheel helper
route96.72s, baseline59.90s expected failure, current95.39s pass,
frozen combined219 source96.36s pass; successful routes include3 old
result/material browser regressions. Counts overlap. Native7tasks/8attempts/4model/
49tools +1 probe; owner bytes/deadline/spending retained, reserve0/unknownfalse.
Application source/package184 is unchanged; Ruff2/JS1 test checks pass.
All gates/failures are retained in [the report](validation/2026-10-19-owner-browser-acceptance.md).
Next: real automatic no-policy negative-review checkpoint and replan affordance,
then automatic retry/restart combined acceptance. Full/controller/admission/neural/
research/final gates stay open; no biological or live-LM claim.

Checked automatic owner replan,2026-10-19: actual browser attach starts the
detached coordinator; no-policy failed independent review reaches a stable stopped
checkpoint. The dialog now admits failed-child replan and hides it for any paused
task; actual parent/child pause/resume is checked. Explicit objective revision
continues through fresh review, parent continuation and current applied integration.
Final extracted184-file wheel automatic79.56s plus3 old owner browser
cases pass; frozen combined221 source81.95s passes, retaining parallel
MaleCNS controls. Each automatic run5tasks/8attempts/5model/39tools +1 separate
probe, no reserve/uncertainty, original deadline/owner bytes/rejection evidence
retained; later source edit revokes currentness. Counts overlap. Ruff2/mypy1/JS2/package/
CLI checks pass. All RED/preparation failures are retained in [the report](validation/2026-10-19-owner-automatic-review.md).
Automatic combined retry/new-branch/restart/avalanche/Stop and full/controller/
admission/neural/research/final gates remain open. No new live-LM/biological claim.

Checked exhausted-retry owner recovery,2026-10-19: real browser approval drives
two negative attempts/reviews, stable exhaustion, owned graceful Host restart
(registration retained/session changed/generation2→3), explicit owner replan and
new prerequisite branch, then third child/fresh review/parent/current integration.
Failure-event mismatch returns409 without work; identical saved POST succeeds only
after exact original evidence is restored. Failed author history remains failed.
Final184-file wheel previous no-policy64.55s and combined126.19s pass;
frozen merged221 source145.40s passes. Combined7tasks/12attempts/6model/
55tools +1 probe, original deadline/limits/bytes/failure evidence retained,
reserve0/uncertaintyfalse; later source edit invalidates. Real terminal-lock
contention is recovered within5s; a held lock still gives cleanup/inconclusive
with uncertainty/reserve retained. Ruff5/mypy3/JS1/package/
CLI checks pass. Counts overlap; RED and old90s observer/cleanup failures are
retained in [the report](validation/2026-10-19-owner-retry-restart.md). Automatic avalanche/in-flight Stop aggregate and
full/controller/admission/neural/research/final gates remain open; no biological claim.

Checked browser model-HTTP Stop,2026-10-19: final extracted184-file wheel8
interface cases pass; frozen merged223 source4 model cases pass. Actual UI pause/
cancel under32 status reads and6 exact Stop replays, original/replaced root and
app restart preserve single workflow call/attempt, owned process stop before HTTP,
spent1model/0tools and held reservation until late usage11 is accounted. Then
reserve0/uncertaintyfalse, no tool/source effect and original deadline/owner bytes
remain. Final Stop55.74–61.87ms; authenticated same-session status returns after bounded
8-peer refusal. Reload shows actual pause/cancel and preserves privacy/Stop grant.
Ruff1/JS1/AST/import/package/UTF8 README/extracted Stop CLI pass. Production
source is unchanged. No full suite or new real-LM/graph/neural/scientific run. All failures
and limits are retained in [the report](validation/2026-10-19-browser-model-stop.md). Automatic owner-approved communication
routes, agent-message/task-request avalanche and combined/controller/admission/
neural/research/full/final gates remain open; no whole-phase completion claim.
