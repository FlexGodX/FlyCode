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

### Task 1: Необязательный оценщик и раздельные панели (§102)

**Files:** modify `src/flycode/llm/lmstudio.py`, `src/flycode/research/cortex.py`, `src/flycode/cli.py`, `src/flycode/web/runs.py`, `src/flycode/web/static/{index.html,app.js,styles.css}`; tests `tests/integration/test_local_cortex.py`, `tests/unit/web/test_app.py`.

**Interfaces:** `make_lmstudio_cortex(..., decision_model: str | None = DEFAULT_DECIDER)`; `RunRequest.decision_model` и `CortexConfig.decision_model` с тем же default; CLI `--no-decision-model` исключает `--decision-model`.

- [ ] RED: сервер содержит только primary; `make_lmstudio_cortex(..., decision_model=None)` возвращает два кандидата при неопределённом контроллере, один HTTP inference, счётчик только primary; повтор после budget=1 не вызывает модель.
- [ ] Run `.venv/bin/pytest tests/integration/test_local_cortex.py -q`; наблюдать failure проверки наличия `None`.
- [ ] GREEN: условное создание/сброс/счётчик/вызов decider; null проходит доменные модели, пустые/sentinel IDs отклоняются; old default dual сохранён.
- [ ] UI: model button и справа execution button; атомарный Apply, независимые настройки; personal defaults отдельно от просмотра истории, отключённые модели явно подписаны.
- [ ] Проверить HTTP integration, CLI mutual exclusion, identity null ≠ dual; browser desktop/390/320, клавиатура, history → new task.
- [ ] Зафиксировать проверенный diff и обновить статус §102.

### Task 2: Совместимые будущие модели (§102)

**Files:** create `src/flycode/llm/catalogue.py`; modify `src/flycode/web/app.py`, static model panel; test `tests/unit/llm/test_catalogue.py`.

**Interfaces:** `ModelDescriptor` с opaque ref, provider model ID, availability, roles и versioned capabilities; `ModelCatalogue.discover()` / `probe(model_ref, role)`; GET `/api/models`, POST `/api/models/probe`.

- [ ] RED: новый ID обнаружен без ready capability; explicit primary probe принимает structured candidates, scorer probe требует finite option probabilities; ошибки и отсутствующий ID не становятся ready.
- [ ] Run новый test; подтвердить отсутствие catalogue/неверное поведение.
- [ ] GREEN: один доверенный endpoint из server config, никакого URL в запросе; роли по реальному протоколу, probe учитывает запрос и возвращает причину.
- [ ] Проверить generic primary/single и generic pair, unavailable selection без silent fallback, refresh без inference, session-token/origin на probe.
- [ ] Зафиксировать результат, указать пределы обнаружения и проверки совместимости.

### Task 3: Снимки и pytest verification (фаза 21, основа 22)

**Files:** create `src/flycode/domain/verification.py`, `src/flycode/verification/{__init__,snapshot,runner,verdict,evidence,engine}.py`; tests `tests/unit/verification/`, `tests/integration/test_verification.py`; modify `src/flycode/research/identity.py`.

**Interfaces:** `SubjectSnapshot`, `VerificationPlan`, `CaseResult`, `VerificationVerdict`; `VerificationEngine.verify(repo: Path, ..., should_stop) -> VerificationVerdict`; evidence ID and hash, no caller-supplied output path.

- [ ] RED: dirty/untracked bytes change hash; symlink escape and secrets rejected/excluded with explicit inventory; tests run in owned copy; source never modified by runner.
- [ ] RED: actual pytest pass/fail/error/skip/empty/timeout cases; exit 0 without complete cases is inconclusive; changed subject invalidates verdict.
- [ ] GREEN: bounded copies and JUnit parser with DTD/entity rejection; registered argv only, process group cancellation, actual dependency fingerprint, atomic evidence manifest outside subject.
- [ ] Verify subprocess tests using `sys.executable`, inspect raw evidence/hash; add verification package to policy identity.
- [ ] Commit verified foundation; unsupported runners/isolation remain blocked, not simulated.

### Task 4: Единые контракты и атомарная очередь (15/16/26/27)

**Files:** create `src/flycode/domain/orchestration.py`, `src/flycode/orchestration/{store,scheduler}.py`, `src/flycode/tracker/{service,projection,commands}.py`; tests `tests/unit/orchestration/`, `tests/integration/test_tracker.py`.

**Interfaces:** canonical `WorkItem`, `AgentProfile`, `AgentInstance`, `AgentAttempt`; SQLite `Store.create_task`, `command`, `claim`, `events`; revisions/idempotency/fencing in transaction.

- [ ] RED: draft creates no tool/model effects; enqueue requires objective, criteria, permission and budget; cycles/foreign dependencies rejected.
- [ ] RED: two connections race for one task; exactly one attempt/owner/event/reservation; replayed command returns same result; root WIP=1; dependencies and profile/scope enforced.
- [ ] GREEN: parameterized SQL, unique active assignment indexes, persistent sequence/outbox, baseline priority and every-eighth aging; reserve before execution.
- [ ] Verify restart durability, stale revision conflict, no eligibility/backoff reasons, budget exhaustion and paused queue; no second tracker status store.
- [ ] Commit with phase coverage, not blanket phase completion.

### Task 5: Исполнение, сообщения и ресурсы (16/28)

**Files:** create `src/flycode/orchestration/{runtime,messages,resources}.py`; modify web app/run adapter; tests integration workflow execution and recovery.

**Interfaces:** `Dispatcher` consumes committed attempts; authenticated worker identity owns fence; shared resource ledger and message envelope with causal IDs.

- [ ] RED: execute real local task and observe actual instance/model/tool; no fake activity; serial endpoint while tools can run; Stop remains responsive.
- [ ] RED: duplicate message dedup, TTL/hop/fanout limits; crash lease expires into reconciling; old fence cannot publish result and writer not reassigned before confirmation.
- [ ] GREEN: event-driven workers, heartbeat10s/lease30s, transaction outbox/inbox, shared budgets4instances/64tasks/3attempts/60model/120tools/30min and verification reserve20%.
- [ ] Verify pause/cancel/retry do not reset budget; no new assignments after stop; user-owned resources untouched.
- [ ] Commit and expose tracker API/activity from same event stream.

### Task 6: Review, интеграция и completion (17/22/29)

**Files:** create `src/flycode/orchestration/{workspaces,integration}.py`, verification oracle/sandbox adapters; tests actual two-writer conflicts/integration/verification.

**Interfaces:** isolated snapshot per writer, immutable patch hash, reviewer approval exact bytes, single integrator, current `VerificationVerdict` completion gate.

- [ ] RED: user dirty/untracked files preserved; conflicting patches blocked; patch changes invalidate review; individually passing patches cannot finish failing integration.
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
- [ ] Run `.venv/bin/pytest -q`, changed-file Ruff/mypy, opt-in real graph and real LM Studio smoke; build/extract wheel and exercise CLI.
- [ ] Update Plan status, spec acceptance mapping, handoff, both READMEs; commit and push verified increments.

## Research and delivery boundaries

Existing phases5–14 have executable software but retain the unproven criteria recorded in §§90–99 and `docs/research.md`. Missing real datasets, unmatched compute or absent interventions must remain explicit research blockers. A local toy success cannot be reported as finishing those criteria.

Execution uses the native workflow in this session under the user's explicit request to implement after updating the plan. The referenced superpowers execution skills are not installed; their absence does not prevent the authorized native implementation.
