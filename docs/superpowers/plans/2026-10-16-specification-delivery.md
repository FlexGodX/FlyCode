# Specification delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать изменения §§100–103 с проверяемыми результатами, общей очередью задач и явным отделением исследовательских допусков.

**Architecture:** Существующий одиночный runtime сохраняется как совместимый исполнитель. Новые задачи и попытки имеют единое транзакционное хранилище; tracker — его представление. Независимая проверка связывает verdict с фактическими байтами результата, а диспетчер резервирует ресурсы до вызова исполнителя.

**Tech Stack:** Python >=3.10, Pydantic v2, SQLite, pytest, FastAPI, существующие HTML/CSS/JS, LM Studio.

**Spec:** `docs/specs/model-selection.md`, `docs/specs/testing-module.md`, `docs/specs/connectome-orchestration.md`, `docs/specs/task-tracker.md`; основной план §§100–103.

## Global Constraints

- Один активный workflow, до четырёх экземпляров; один inference на endpoint по умолчанию.
- `decision_model=null` отключает оценщик; отсутствие поля сохраняет legacy default.
- Черновик не вызывает модели и инструменты. Статус `succeeded` требует актуального verification verdict.
- SQLite не удерживает транзакцию во время inference; candidate scores привязаны к ID.
- Биологическая топология неизменна. Старый допуск одиночного агента не разрешает orchestration.
- Только доверенные локальные проекты в процессной изоляции; unsupported hostile sandbox блокируется.
- Не перенумеровывать §§1–103; программная поставка не закрывает научные критерии §§90–99.

## Review Focus

1. История dual-запуска не должна менять личные настройки следующего single-запуска (задача 1).
2. Обнаружение model ID не доказывает поддержку structured output/logprobs (задача 2).
3. Exit 0, пустой/all-skipped/flaky отчёт не дают verified pass (задача 3).
4. Одновременные claim и повтор команды не создают второе назначение/расход (задача 4).
5. Потерянный worker и поздний результат не разрешают повторную запись в проект (задачи 5–6).

## Порядок и критерии завершения

Номера фаз — группы требований, а не последовательность разработки. Порядок:
§102 → 21 → 15/16 + 26/27 → 22 → 17/28/29 → 18/23 → 19/24 → 20/25.
Ранние экраны и события добавляются вместе с соответствующим backend.
Каждый пункт считается завершённым только после перечисленных проверок.

Поставка `9eb6422`: nullable scorer, catalogue/probe и две панели опубликованы;
371 passed/8 skipped, visual QA desktop/390/320. На момент той поставки live
single-Qwen repair не был принят из-за короткого timeout. Новые live single/dual
прогоны на runtime `18913d5` прошли; результаты и границы сохранены в
`docs/validation/2026-10-16-model-selection.json`. Старый failure не удалён.

Отдельный запрос benchmark/training выполнен на runtime `694d42d`:
`docs/validation/2026-10-16-feedback-benchmark.md`, 296 реальных запусков,
английские запросы, immutable biological graph, две сохранённые попытки обучения.
Toy-family completion 14/20→20/20, но single-Qwen held-out 2/2→0/2; trained
checkpoints не активированы. Этот ограниченный standalone эксперимент не закрывает
Task7, orchestration admission или §§90–99. Следующая поставка здесь остаётся Task4.

Trusted-local pytest engine/CLI опубликованы в `5d16b5a`; Task3a реализует
owner-approved критерии, oracle bytes и составной план. Общий runtime completion
по-прежнему требует очереди, review и integration (Tasks4–6).

### Task 1: Необязательный оценщик и раздельные панели (§102)

**Files:** modify `src/flycode/llm/lmstudio.py`, `src/flycode/research/cortex.py`, `src/flycode/cli.py`, `src/flycode/web/runs.py`, `src/flycode/web/static/{index.html,app.js,styles.css}`; tests `tests/integration/test_local_cortex.py`, `tests/unit/web/test_app.py`.

**Interfaces:** `make_lmstudio_cortex(..., decision_model: str | None = DEFAULT_DECIDER)`; `RunRequest.decision_model` и `CortexConfig.decision_model` с тем же default; CLI `--no-decision-model` исключает `--decision-model`.

- [x] RED: сервер содержит только primary; `make_lmstudio_cortex(..., decision_model=None)` возвращает два кандидата при неопределённом контроллере, один HTTP inference, счётчик только primary; повтор после budget=1 не вызывает модель.
- [x] Run `.venv/bin/pytest tests/integration/test_local_cortex.py -q`; наблюдать failure проверки наличия `None`.
- [x] GREEN: условное создание/сброс/счётчик/вызов decider; null проходит доменные модели, пустые/sentinel IDs отклоняются; old default dual сохранён.
- [x] UI: model button и справа execution button; атомарный Apply, независимые настройки; personal defaults отдельно от просмотра истории, отключённые модели явно подписаны.
- [x] Проверить HTTP integration, CLI mutual exclusion, identity null ≠ dual; browser desktop/390/320, клавиатура, history → new task.
- [x] Зафиксировать проверенный diff и обновить статус §102.

### Task 2: Совместимые будущие модели (§102)

**Files:** create `src/flycode/llm/catalogue.py`; modify `src/flycode/web/app.py`, static model panel; test `tests/unit/llm/test_catalogue.py`.

**Interfaces:** `ModelDescriptor` с opaque ref, provider model ID, availability, roles и versioned capabilities; `ModelCatalogue.discover()` / `probe(model_ref, role)`; GET `/api/models`, POST `/api/models/probe`.

- [x] RED: новый ID обнаружен без ready capability; explicit primary probe принимает structured candidates, scorer probe требует finite option probabilities; ошибки и отсутствующий ID не становятся ready.
- [x] Run новый test; подтвердить отсутствие catalogue/неверное поведение.
- [x] GREEN: один доверенный endpoint из server config, никакого URL в запросе; роли по реальному протоколу, probe учитывает запрос и возвращает причину.
- [x] Проверить generic primary/single и generic pair, unavailable selection без silent fallback, refresh без inference, session-token/origin на probe.
- [x] Зафиксировать результат, указать пределы обнаружения и проверки совместимости.

### Task 3: Снимки и pytest verification (фаза 21, основа 22)

**Files:** create `src/flycode/domain/verification.py`, `src/flycode/verification/{__init__,snapshot,runner,verdict,evidence,engine}.py`; tests `tests/unit/verification/`, `tests/integration/test_verification.py`; modify `src/flycode/research/identity.py`.

**Interfaces:** `SubjectSnapshot`, `VerificationPlan`, `CaseResult`, `VerificationVerdict`; `VerificationEngine.verify(repo: Path, ..., should_stop) -> VerificationVerdict`; evidence ID and hash, no caller-supplied output path.

- [x] RED: dirty/untracked bytes change hash; symlink escape and secrets rejected/excluded with explicit inventory; tests run in owned copy; source never modified by runner.
- [x] RED: actual pytest pass/fail/error/skip/empty/timeout cases; exit 0 without complete cases is inconclusive; changed subject invalidates verdict.
- [x] GREEN: bounded copies and JUnit parser with DTD/entity rejection; registered argv only, process group cancellation, actual dependency fingerprint, atomic evidence manifest outside subject.
- [x] Verify subprocess tests using `sys.executable`, inspect raw evidence/hash; add verification package to policy identity.
- [x] Commit verified foundation; unsupported runners/isolation remain blocked, not simulated.

### Task 3a: Доверенный контракт приёмки и составной план (оставшаяся фаза 21)

**Files:** create `src/flycode/verification/plan.py`, `src/flycode/verification/oracle.py`, `tests/unit/verification/test_plan.py`; modify `domain/verification.py`, `verification/engine.py`, `verification/verdict.py`, `tests/integration/test_verification.py`.

**Interfaces:** `AcceptanceCriterion(id, check_ids, expected_case_ids)` и `AcceptanceContract(id, version, criteria)` принадлежат доверенному владельцу. `PlanBuilder.build(snapshot, contract, checks)` возвращает `VerificationPlan` с requirement→check→case mapping; `evaluate(attempts, invalidated, contract=None)` сохраняет старый suite-вызов и проверяет contract при его наличии. `CheckSpec` получает stage/targets/dependencies; `SubjectSnapshot` — server-owned project/workflow/task/attempt refs. `VerificationVerdict` явно различает registered-suite и acceptance-contract scope; ORC завершает задачу только вторым scope.

- [x] RED: критерий `R1` требует `test_api.py::test_regression`; suite с одним другим passed-тестом не закрывает `R1`.

```python
criterion = AcceptanceCriterion(id='R1', check_ids=['regression'],
                                expected_case_ids=['test_api.py::test_regression'])
contract = AcceptanceContract(id='api-contract', version=1, criteria=[criterion])
attempt = CheckAttempt(number=1, argv=['pytest'], exit_code=0, duration=1,
    cases=[CaseResult(id='test_api.py::test_other', outcome='passed')],
    collected=['test_api.py::test_other'], complete=True)
status, reasons, _ = evaluate([attempt], invalidated=False, contract=contract)
assert status == 'inconclusive'
assert any('R1' in reason for reason in reasons)
```

- [x] RED: попытка удалить required check, цикл dependencies, foreign snapshot ref и изменение утверждённого contract hash отклоняются до запуска процесса.
- [x] GREEN: хранить утверждённый контракт вне изменяемой копии, связывать checks с точными snapshots и версиями oracle/profile. Предложение агента добавляет optional checks и не редактирует required.
- [x] RED→GREEN: дефект воспроизводится baseline negative control, patch закрывает target, полный regression и integrated snapshot проверяются отдельно; удачный target на сломанной базе не даёт общего pass.
- [x] Проверить actual case identities, неполный required coverage, semantic revision и новую базу; косметическое изменение заголовка не меняет контракт. Добавить lifecycle/error class попытки без выдуманных lease для одиночного процесса.
- [x] Запустить `.venv/bin/pytest tests/unit/verification/test_plan.py tests/integration/test_verification.py -q`, затем полный pytest; обновить TST/TV coverage и только после этого закрывать фазу 21.

Validation for Task3a: 452 passed/8 skipped (188.54s); targeted verification/identity62
passed, Ruff/mypy passed. Extracted wheel CLI pass/empty outcomes and staged
acceptance passed. Cleanup PermissionError uncertainty remains explicit; bounded
absence confirmation and optional-check safety gates have fault-injection regressions.

### Task 4: Единые контракты и атомарная очередь (15/16/26/27)

**Files:** create `src/flycode/domain/orchestration.py`, `src/flycode/orchestration/{store,scheduler}.py`, `src/flycode/tracker/{service,projection,commands}.py`; tests `tests/unit/orchestration/`, `tests/integration/test_tracker.py`.

**Interfaces:** canonical `WorkItem`, `AgentProfile`, `AgentInstance`, `AgentAttempt`; SQLite `Store.create_task`, `command`, `claim`, `events`; revisions/idempotency/fencing in transaction.

- [x] RED: draft creates no tool/model effects; enqueue requires objective, criteria, permission and budget; cycles/foreign dependencies rejected.
- [x] RED: two connections race for one task; exactly one attempt/owner/event/reservation; replayed command returns same result; root WIP=1; dependencies and profile/scope enforced.
- [x] GREEN: parameterized SQL, unique active assignment indexes, persistent sequence/outbox, baseline priority and every-eighth aging; reserve before execution.
- [x] Verify restart durability, stale revision conflict, no eligibility/backoff reasons, budget exhaustion and paused queue; no second tracker status store.
- [x] Commit with phase coverage, not blanket phase completion.

Task4 implementation covers canonical persistence and host-side commands only.
Tests exercise actual SQLite connections, transaction rollback on outbox failure,
restart during stop, cumulative spending, reserved verification capacity, both
aging counters, scope reduction and model descriptor version changes. Tracker
distinguishes assigned from runtime-confirmed running and retains stopped attempt
history. No worker process, message consumer, heartbeat, workspace integration or
completion is implied. Stop acknowledgement is currently a trusted host operation;
Task5 must establish actual process termination before invoking it. Profile
context capacity and tool argument enforcement also remain runtime requirements.
Verification: full default suite494 passed/8 skipped; final targeted suite46
passed (three additional checks followed full-suite collection). Relevant
Ruff/mypy pass; extracted wheel persists/reopens the queue and runs CLI `brains`.

**Execution notes for Task4:**

- Keep common Pydantic `WorkItem`, `WorkflowSpec`, `AgentProfile`, `AgentInstance`,
  `AgentAttempt`, permissions and budget models in `domain/orchestration.py`.
  IDs originate in the store; task revision and semantic contract revision are
  separate. Tracker commands/projections consume these exact objects.
- Use one SQLite file with tasks/workflows/profiles/instances/attempts,
  reservations, commands and sequenced events/outbox. `BEGIN IMMEDIATE`, foreign
  keys and unique active task/instance assignments enforce ownership across
  connections. An event and reservation commit with the attempt, never later.
- Replay matching idempotency keys before revision checks, while still enforcing
  caller scope; a reused key with a different request is a conflict. Creation,
  metadata commands and enqueue never construct a cortex or execute project tools.
- Draft roots may coexist, but only one root workflow is active. Persist separate
  root-start and internal-dispatch counters; each eighth successful launch
  ages the oldest eligible candidate. Increment only on the runtime-confirmed
  first start, not on a stored assignment, failed spawn, claim or wait.
- Preserve spent/reserved/free amounts independently. A developer cannot consume
  the verification reserve. Active/uncertain ownership survives restart for
  reconciliation; neither lease expiry nor retry grants a second writer.
- First pin real two-connection races, restart/idempotency, stale revisions,
  missing criteria/permissions/budget, foreign/cyclic dependencies, scope/profile
  mismatch, root WIP, pause and exhausted budgets. The executor, message delivery,
  heartbeat and actual model activity remain Task5; a persisted assignment alone
  must not be presented as an executing agent.

### Task 5: Исполнение, сообщения и ресурсы (16/28)

**Files:** create `src/flycode/orchestration/{runtime,messages,resources}.py`; modify web app/run adapter; tests integration workflow execution and recovery.

**Interfaces:** `Dispatcher` consumes committed attempts; authenticated worker identity owns fence; shared resource ledger and message envelope with causal IDs.

- [ ] RED: profile registration resolves server-owned model refs, role/capability versions, context and permissions; discovered-only or incompatible roles never qualify an instance for dispatch.
- [ ] RED: execute real local task and observe actual instance/model/tool; no fake activity; serial endpoint while tools can run; Stop remains responsive.
- [ ] RED: duplicate message dedup, TTL/hop/fanout limits; crash lease expires into reconciling; old fence cannot publish result and writer not reassigned before confirmation.
- [ ] GREEN: event-driven workers, heartbeat10s/lease30s, transaction outbox/inbox, shared budgets4instances/64tasks/3attempts/60model/120tools/30min and verification reserve20%.
- [ ] Verify pause/cancel/retry do not reset budget; no new assignments after stop; user-owned resources untouched.
- [ ] Commit and expose tracker API/activity from same event stream.

**Task5 lease foundation:** `Store.heartbeat` renews only current unexpired
ownership by 30s. `Store.expire` revokes expired attempts into reconciling,
retaining instance and reservations; it never starts a replacement. Workflow
deadline expires even a healthy lease and keeps the root slot until all owned
processes are confirmed stopped. User cancellation takes precedence over deadline.
The actual supervisor must call heartbeat every10s and run the expiry sweep;
these store methods alone do not provide a background worker or stop proof.
Verification:51 targeted tests and full default suite502 passed/8 skipped;
Ruff/mypy pass. Five lease/deadline cases also pass on the extracted wheel.
This partial Task5 increment leaves the aggregate checklist open.

**Task5 model-transport foundation:** `Resources.begin/execute/finish/calls`
uses the same SQLite file (schema2; existing schema1 upgrades in place).
Endpoint ownership, one request debit, event/outbox and idempotency commit
atomically. Active/uncertain endpoint uniqueness spans database connections.
Only the pinned, explicitly probed profile binding supplies model/endpoint IDs.
HTTP executes after the transaction. Waiting emits one event and cancellation
does not debit or send. HTTP status errors consume budget; lost transports retain
their slot and uncertainty across restart. A response from a revoked fence is
audited with actual usage, releases only its known-finished transport, and cannot
return a result to the worker. Host transport acknowledgement is trusted and must
not be exposed as an agent/browser-supplied completion flag.

Primary generation/consolidation and scorer clients now accept host-only request
executors around actual SDK calls; counters increment after admission. The
LM Studio factory rejects partially guarded pairs. These are working adapter
seams, not a connected supervisor. Legacy/probe calls still require shared
admission wiring, model capacity checks and actual worker execution remain open,
and no automatic recovery of an unknown provider outcome is implemented.
Verification: full default suite515 passed/8 skipped, targeted suite109 passed,
changed-file Ruff/mypy pass. Extracted wheel:13 transport checks and CLI `brains`
passed outside the checkout. These adapter checks do not close Task5 execution.

**Runtime implementation constraints:**

1. Reuse `run_agent`/event sink/stop callback, but do not create a second lifecycle
   in legacy `RunManager.records`. Bind events to canonical instance/attempt/fence.
2. `cortex.generate_candidates` is synchronous today, including network calls.
   Keep it outside the serving event loop and SQLite transaction. Supervise owned
   workers independently so heartbeat, cancellation and HTTP remain responsive.
3. Gate each actual primary/scorer HTTP call and each tool before its effect,
   not merely a candidate-generation step or an after-the-fact counter. Share
   one endpoint slot, including explicit probes; acquire only for the request.
   Unknown transport/process outcomes require reconciliation, not automatic replay.
4. The runtime must validate profile context against available model capacity
   and enforce tool/file arguments. Current structural context bounds and pinned
   protocol probes do not prove those properties.
5. Lease expiry revokes commit authority but cannot kill a process. Retain the
   ownership/reservation until owned process-group absence and unknown effects
   are reconciled. Never stop user-owned servers or infer death from an absent
   heartbeat. Publish real subprocess and HTTP evidence before closing Task5.

Existing POSIX cleanup is `verification/runner.py:_terminate(Popen)`, with
`_signal_group`/`_group_gone`; it assumes an owned `start_new_session=True`
process whose PID is its process-group ID. Ordinary agent tool subprocesses
inherit that group, but `verification.run_pytest` starts a separate session.
Track nested verification groups separately. Docker daemon resources and detached
project processes are not contained by a client process group. Block unsupported
resource profiles until owned-resource tracking exists; a stale numeric PID or
lease timeout is not sufficient ownership/absence proof.

### Task 6: Review, интеграция и completion (17/22/29)

**Files:** create `src/flycode/orchestration/{workspaces,integration}.py`, verification oracle/sandbox adapters; tests actual two-writer conflicts/integration/verification.

**Interfaces:** isolated snapshot per writer, immutable patch hash, reviewer approval exact bytes, single integrator, current `VerificationVerdict` completion gate.

- [ ] RED: user dirty/untracked files preserved; conflicting patches blocked; patch changes invalidate review; individually passing patches cannot finish failing integration.
  Include selected subfolders inside a larger Git repository: snapshot inventory must not expose foreign paths from the parent repository.
- [ ] RED: all rerun outcomes retained, flaky mandatory blocks pass, malformed/oversized report inconclusive; unsupported hostile sandbox blocked before subprocess.
- [ ] GREEN: serialize integration, run required checks on integrated snapshot, persist partial artifacts and explicit blockers; versioned criteria immutable to model.
- [ ] Verify semantic revision invalidates result, metadata revision preserves evidence, reopen/archive audit and review queue current assignee.
- [ ] Commit only after actual end-to-end task reaches current verified completion.

### Task 7: Нейронные назначения и исследования (18/19/23/24)

**Files:** create `src/flycode/orchestration/{encoder,policy}.py`; research orchestration adapter; tests replay, failure injection and scoped admission.

**Interfaces:** versioned workflow encoder, per-candidate-ID scores; shadow default influence0; separate orchestration admission identity including models/roles/budgets/permissions/encoder/scheduler/tools.

- [ ] RED: shadow cannot change baseline assignment; malformed/nonfinite score falls back; old single-agent admission rejected before effects.
- [ ] GREEN: deterministic ordered event replay, immutable connectome adjacency, bounded correction per ORC formula; no neural bypass of eligibility/reservations.
- [ ] Run preregistered6families×3seeds matrix per variant with >=30dispatch decisions; report all failures and matched/unmatched compute.
- [ ] Promote influence only with >=10 actual safe interventions and no regression under scoped validation; otherwise keep blocked with real evidence.
- [ ] Publish raw metrics and remaining scientific criteria, including unseen repositories and causal counterfactuals.

### Task 8: Общий интерфейс и доступ к evidence (20/25/26–29)

**Files:** tracker/verification web routers and static views, `cli.py`, RU/EN README, `docs/handoff.md`.

**Interfaces:** tasks/commands/comments/events, agents/activity, verification plans/start/cancel/rerun/artifacts; bounded server IDs, no paths or executable HTML from clients.

- [ ] RED: API rejects missing token/foreign scope/stale revision; event reconnect returns snapshot+cursor without duplicate commands.
- [ ] GREEN: board/list/agents from canonical projections, explicit blockers/last confirmed activity/actual model, shared review/check views; no invented percentage or hidden reasoning trace.
- [ ] Browser verify desktop/390/320 before/after screenshots, keyboard/focus, primary action and Stop always reachable, model/settings independent.
  Known visual regression to pin during this task: dark-theme selected project/chat labels lose contrast on their light selection fill (journal screenshot review).
- [ ] Run `.venv/bin/pytest -q`, changed-file Ruff/mypy, opt-in real graph and real LM Studio smoke; build/extract wheel and exercise CLI.
- [ ] Update Plan status, spec acceptance mapping, handoff, both READMEs; commit and push verified increments.

## Research and delivery boundaries

Existing phases5–14 have executable software but retain the unproven criteria recorded in §§90–99 and `docs/research.md`. Missing real datasets, unmatched compute or absent interventions must remain explicit research blockers. A local toy success cannot be reported as finishing those criteria.

Execution uses the native workflow in this session under the user's explicit request to implement after updating the plan. The referenced superpowers execution skills are not installed; their absence does not prevent the authorized native implementation.
