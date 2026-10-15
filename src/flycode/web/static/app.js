"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const activeStatuses = new Set(["queued", "running", "stopping"]);
const statusNames = {
  queued: "В очереди",
  running: "В работе",
  stopping: "Остановка…",
  completed: "Выполнено",
  incomplete: "Нужна проверка",
  failed: "Ошибка",
  stopped: "Остановлено",
  interrupted: "Прервано",
};
const modeNames = {
  explore: "Исследование",
  diagnose: "Диагностика",
  execute: "Выполнение",
  verify: "Проверка",
  recover: "Восстановление",
  consolidate: "Подведение итогов",
  stop: "Завершение",
  exploit: "Решение",
  orient: "Ориентация",
};
const modeDescriptions = {
  explore: "Собирает контекст и ищет причину проблемы.",
  execute: "Выбирает действие, которое приблизит результат.",
  verify: "Проверяет результат тестами и наблюдениями.",
  recover: "Меняет подход после обнаруженной проблемы.",
  consolidate: "Сохраняет результат и опыт этого запуска.",
  stop: "Обязательные проверки завершены.",
};
const toolNames = {
  read_file: "Чтение файла",
  list_files: "Обзор файлов",
  search: "Поиск в проекте",
  search_files: "Поиск в проекте",
  git_status: "Состояние репозитория",
  git_diff: "Проверка изменений",
  run_tests: "Запуск тестов",
  edit_file: "Редактирование файла",
  create_file: "Создание файла",
  stop: "Завершение задачи",
  docker_compose: "Управление окружением",
  docker_logs: "Чтение логов",
  postgres_status: "Проверка базы данных",
  postgres_query: "Запрос к базе данных",
  alembic: "Миграции базы данных",
  http_request: "HTTP-запрос",
  service_health: "Проверка сервиса",
};
let bootstrap,
  history = [],
  selected = null,
  source = null,
  seen = new Set(),
  stepCards = new Map();
let latestObservation = null,
  currentMode = "",
  modelRows = {},
  changes = [],
  modelCalls = {},
  startedAt = null;
let modelHealth = { connected: false, models: [] },
  currentTab = "conversation",
  toastTimer;
let scrollPositions = { conversation: 0, changes: 0, logs: 0 };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  node.append(use);
  return node;
}
function showError(message) {
  $("#formError").textContent = message;
  $("#formError").hidden = !message;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-FlyCode-Token": bootstrap?.token || "",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : response.status === 422
          ? "Проверьте задачу и настройки запуска."
          : "Сервер не ответил. Проверьте подключение.",
    );
  }
  return response.json();
}
function activeRun() {
  return history.find((run) => activeStatuses.has(run.status));
}
function formatTime(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function eventTime(event) {
  return new Date(event.timestamp).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
function projectName(id) {
  return bootstrap?.projects.find((project) => project.id === id)?.name || id;
}
function modelName(id) {
  if (id === "qwen/qwen3.5-9b") return "Qwen 3.5 9B";
  if (id === "jev-style-qwen3.5-2b-decision") return "Jev 2B";
  return id.split("/").pop();
}
function patch(action) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("div", "patch-line meta", "@@ Фрагмент изменения @@"));
  if (action.tool === "edit_file") {
    for (const line of String(action.arguments.old_text || "").split("\n"))
      fragment.append(el("div", "patch-line removed", "− " + line));
    for (const line of String(action.arguments.new_text || "").split("\n"))
      fragment.append(el("div", "patch-line added", "+ " + line));
  } else {
    for (const line of String(action.arguments.content || "").split("\n"))
      fragment.append(el("div", "patch-line added", "+ " + line));
  }
  return fragment;
}
function details(title, child) {
  const node = el("details", "step-details");
  node.append(el("summary", "", title), child);
  return node;
}
function setNode(role, working, caption) {
  const node = $(`[data-node="${role}"]`);
  if (!node) return;
  node.classList.toggle("active", working);
  node.querySelector(".node-state").textContent =
    caption || (working ? "Работает" : "Готов");
}
function clearNodes() {
  for (const role of ["controller", "reasoner", "decider", "tools"])
    setNode(
      role,
      false,
      role === "controller" || role === "tools" ? "Готов" : "Ожидание",
    );
}
function resetInspector() {
  clearNodes();
  latestObservation = null;
  currentMode = "";
  modelCalls = {};
  $("#modeName").textContent = "Ожидание задачи";
  $("#modeDescription").textContent = "Агент начнёт с исследования проекта.";
  for (const field of ["confidence", "risk"]) {
    $("#" + field).value = 0;
    $("#" + field + "Value").textContent = "—";
  }
  $("#signalChart").replaceChildren();
  $("#stepsValue").textContent =
    "0 / " + (selected?.request.max_steps || $("#maxSteps").value);
  $("#callsValue").textContent = "0";
  $("#filesValue").textContent = "0";
  $("#testsValue").textContent = "—";
  $("#circuitNote").textContent =
    selected?.request.llm === "heuristic"
      ? "Демонстрация работает без моделей. Действия выбирает контроллер из встроенных правил."
      : "Контроллер решает, когда нужны модели. Активный этап будет подсвечен.";
  if (selected?.request.llm === "heuristic") {
    setNode("reasoner", false, "Без вызова");
    setNode("decider", false, "Без вызова");
  }
}
function updateObservation(observation) {
  latestObservation = observation;
  const stats = $("#stepsValue");
  stats.replaceChildren(
    document.createTextNode(String(observation.step_number)),
    el("small", "", " / " + selected.request.max_steps),
  );
  $("#testsValue").textContent =
    observation.tests.status === "passed"
      ? String(observation.tests.passed ?? "✓")
      : observation.tests.status === "failed"
        ? "Ошибка"
        : "—";
}
function renderHistory() {
  const filter = $("#search").value.toLowerCase();
  $("#historyCount").textContent = history.length;
  const items = history.filter((run) =>
    run.request.task.toLowerCase().includes(filter),
  );
  $("#history").replaceChildren();
  if (!items.length)
    $("#history").append(
      el(
        "p",
        "history-empty",
        history.length
          ? "Ничего не найдено"
          : "Здесь появятся ваши задачи и результаты работы агента.",
      ),
    );
  for (const run of items) {
    const button = el(
      "button",
      "history-item" + (selected?.id === run.id ? " selected" : ""),
    );
    const text = el("span", "history-text");
    text.append(
      el("strong", "", run.request.task),
      el(
        "small",
        "",
        `${statusNames[run.status] || run.status} · ${new Date(run.created_at).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}`,
      ),
    );
    button.title = run.request.task;
    button.append(icon(run.status === "completed" ? "check" : "chat"), text);
    button.addEventListener("click", () => openRun(run));
    $("#history").append(button);
  }
}
function controls() {
  const running = activeRun();
  $("#sendButton").hidden = !!running;
  $("#sendButton").disabled = !!running;
  $("#stopButton").hidden = !running;
  $("#stopButton").disabled = running?.status === "stopping";
  $("#stopButton").lastChild.textContent =
    running?.status === "stopping" ? "Останавливаем…" : "Остановить";
  $("#taskInput").disabled = !!running;
  $("#projectSelect").disabled = !!running;
  $("#settingsButton").disabled = !!running;
  $("#liveStatus").hidden = !running;
  if (running?.status === "stopping")
    $("#liveText").textContent =
      "Завершаем текущий вызов. Новых действий не будет.";
  else if (running && selected?.id !== running.id)
    $("#liveText").textContent = "Другая задача ещё выполняется";
  else if (running) $("#liveText").textContent = "Агент работает";
  $("#taskInput").placeholder = running
    ? "Агент работает. Действия и результаты появятся выше."
    : "Опишите задачу — остальное возьмёт на себя агент…";
  const status = selected?.status;
  $("#runStatus").className = "status-pill " + (status || "");
  $("#runStatus").replaceChildren(
    el("span", "status-dot"),
    document.createTextNode(
      status ? statusNames[status] || status : "Готов к работе",
    ),
  );
  $("#engineLabel").textContent =
    $("#llmSelect").value === "heuristic" ? "Без моделей" : "Qwen + Jev";
  const models = selected?.request || {
    model: $("#reasonerModel").value,
    decision_model: $("#deciderModel").value,
  };
  for (const [role, id] of [
    ["reasoner", models.model],
    ["decider", models.decision_model],
  ]) {
    const label = $(`[data-node="${role}"] strong`);
    label.textContent = modelName(id);
    label.title = id;
  }
  $("#modelSettings").hidden = $("#llmSelect").value === "heuristic";
  $("#composerHint").replaceChildren(
    document.createTextNode(
      $("#projectSelect").value === "demo"
        ? "Демо запускается в отдельной папке."
        : "Агент применяет изменения напрямую в выбранном проекте.",
    ),
    el("span", "", "⌘ Enter — отправить"),
  );
}
function setTab(name) {
  if (currentTab !== name)
    scrollPositions[currentTab] = $("#content").scrollTop;
  const changed = currentTab !== name;
  currentTab = name;
  for (const button of $$(".tab")) {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active);
    button.tabIndex = active ? 0 : -1;
  }
  for (const section of ["conversation", "changes", "logs"])
    $("#" + section).hidden = section !== name;
  if (name === "changes") renderChanges();
  if (changed) $("#content").scrollTop = scrollPositions[name];
}
function renderChanges() {
  const container = $("#changes");
  container.replaceChildren();
  if (!changes.length) {
    container.append(
      el(
        "div",
        "empty-panel",
        "Здесь появятся изменения файлов, которые агент успешно применил в этом запуске.",
      ),
    );
    return;
  }
  container.append(
    el("h2", "panel-title", "Изменения за запуск"),
    el(
      "p",
      "panel-subtitle",
      "Успешно применённые фрагменты. Номер шага показывает порядок изменений.",
    ),
  );
  for (const { action, step } of changes) {
    const card = el("article", "patch-card");
    const heading = el("div", "patch-title");
    heading.append(
      el("strong", "", action.arguments.path),
      el("span", "", "Шаг " + step),
    );
    card.append(heading, patch(action));
    container.append(card);
  }
}
function addSelection(data) {
  for (const previous of $$(".auto-open")) {
    previous.open = false;
    previous.classList.remove("auto-open");
  }
  const action = data.chosen_action,
    step = latestObservation?.step_number + 1 || stepCards.size + 1;
  const card = el("article", "step-card");
  card.dataset.action = action.id;
  const heading = el("div", "step-header");
  const glyph = el("span", "step-icon");
  glyph.append(icon("terminal"));
  const title = toolNames[action.tool] || action.tool;
  heading.append(
    glyph,
    el(
      "strong",
      "",
      title + (action.arguments.path ? " · " + action.arguments.path : ""),
    ),
    el("span", "step-mode", modeNames[currentMode] || currentMode),
    el("span", "step-number", String(step).padStart(2, "0")),
  );
  card.append(
    heading,
    el(
      "p",
      "step-rationale",
      action.rationale ||
        "Действие выбрано контроллером по текущему состоянию проекта.",
    ),
  );
  if (data.routing?.error)
    card.append(el("p", "diagnostic", data.routing.error));
  const candidates = el("div");
  for (const candidate of data.candidates || []) {
    const blocked = data.blocked?.includes(candidate.id);
    const row = el(
      "div",
      "candidate" + (candidate.id === action.id ? " chosen" : ""),
    );
    row.append(
      el(
        "strong",
        "",
        `${candidate.id === action.id ? "✓ " : ""}${toolNames[candidate.tool] || candidate.tool}`,
      ),
      el(
        "span",
        "",
        blocked
          ? "Заблокировано"
          : "Оценка " + Number(data.scores[candidate.id] || 0).toFixed(3),
      ),
      el("p", "", candidate.rationale),
    );
    if (data.routing?.decision_scores?.[candidate.id] !== undefined)
      row.append(
        el(
          "p",
          "",
          `Jev: ${(data.routing.decision_scores[candidate.id] * 100).toFixed(1)}% среди вариантов`,
        ),
      );
    candidates.append(row);
  }
  if (data.routing?.reason)
    candidates.append(el("pre", "", data.routing.reason));
  card.append(
    details(
      `Почему это действие · ${data.candidates?.length || 1} варианта`,
      candidates,
    ),
  );
  card.append(
    details(
      "Параметры действия",
      el("pre", "", JSON.stringify(action.arguments, null, 2)),
    ),
  );
  stepCards.set(action.id, card);
  $("#timeline").append(card);
}
function finishRun(record) {
  selected = record;
  history = history.map((run) => (run.id === record.id ? record : run));
  clearNodes();
  controls();
  renderHistory();
  startedAt = null;
  if ($("#timeline .completion-card")) return;
  const card = el("article", "completion-card " + record.status);
  const title =
    {
      completed: "Задача выполнена",
      incomplete: "Результат требует проверки",
      failed: "Не удалось завершить задачу",
      stopped: "Агент остановлен",
      interrupted: "Запуск прерван",
    }[record.status] || "Запуск завершён";
  const heading = el("h2");
  heading.append(
    icon(record.status === "completed" ? "check" : "pulse"),
    document.createTextNode(title),
  );
  card.append(heading);
  const description =
    record.error ||
    (record.status === "completed"
      ? "Все обязательные проверки пройдены."
      : record.status === "stopped"
        ? "Новые действия остановлены. Уже применённые изменения сохранены."
        : record.status === "incomplete"
          ? "Агент завершил запуск, но обязательные проверки ещё не пройдены. Посмотрите результаты шагов и журнал."
          : "История сохранена. Вы можете начать новую задачу.");
  card.append(el("p", "", description));
  if (record.result) {
    const metrics = record.result.metrics;
    $("#callsValue").textContent = Object.values(metrics.model_calls).reduce(
      (sum, value) => sum + value,
      0,
    );
    const stats = el("div", "completion-stats");
    stats.append(
      el("span", "", `${record.result.steps} шагов`),
      el("span", "", `${metrics.changed_lines} строк изменено`),
      el("span", "", formatTime(metrics.wall_time_s)),
    );
    card.append(stats);
  }
  $("#timeline").append(card);
  $("#eventConnection").textContent = "История сохранена";
}
function handleEvent(event) {
  if (seen.has(event.id)) return;
  seen.add(event.id);
  const nearBottom =
    $("#content").scrollHeight -
      $("#content").scrollTop -
      $("#content").clientHeight <
    180;
  const data = event.data;
  const log = el("details", "log-event");
  const logTitle = el("summary");
  logTitle.append(el("time", "", eventTime(event)), el("span", "", event.kind));
  log.append(logTitle, el("pre", "", JSON.stringify(data, null, 2)));
  $("#logs").append(log);
  switch (event.kind) {
    case "status":
      if (data.phase === "stopping") {
        selected.status = "stopping";
        controls();
      }
      if (data.phase === "preparing") {
        selected.status = "running";
        history = history.map((run) =>
          run.id === selected.id ? selected : run,
        );
        controls();
        renderHistory();
        $("#liveText").textContent = "Знакомится с проектом";
      }
      break;
    case "observation":
      updateObservation(data.observation);
      break;
    case "controller": {
      currentMode = data.mode;
      clearNodes();
      setNode("controller", true, "Выбирает режим");
      const state = data.state;
      $("#modeName").textContent = modeNames[data.mode] || data.mode;
      $("#modeDescription").textContent =
        modeDescriptions[data.mode] ||
        "Выбирает следующее действие по состоянию проекта.";
      $("#confidence").value = state.confidence;
      $("#confidenceValue").textContent =
        Math.round(state.confidence * 100) + "%";
      $("#risk").value = state.risk_level;
      $("#riskValue").textContent = Math.round(state.risk_level * 100) + "%";
      const bar = el("progress");
      bar.max = 1;
      bar.value = state.confidence;
      bar.title = `Шаг ${data.step}: ${Math.round(state.confidence * 100)}%`;
      $("#signalChart").append(bar);
      break;
    }
    case "planning":
      $("#liveText").textContent =
        selected.request.llm === "heuristic"
          ? "Выбирает следующее действие"
          : "Контроллер направляет работу моделей";
      break;
    case "model_start": {
      setNode("controller", false, "Маршрут выбран");
      setNode(data.role, true);
      modelCalls[data.model] = (modelCalls[data.model] || 0) + 1;
      $("#callsValue").textContent = Object.values(modelCalls).reduce(
        (sum, value) => sum + value,
        0,
      );
      const name = modelName(data.model);
      $("#circuitNote").textContent =
        data.role === "reasoner"
          ? `Контроллер запросил семантический разбор. ${name} предлагает варианты следующего действия.`
          : `${name} оценивает безопасные варианты. Итоговый выбор остаётся за контроллером.`;
      $("#liveText").textContent =
        data.role === "reasoner"
          ? `${name} предлагает варианты действий…`
          : `${name} сравнивает безопасные варианты…`;
      const row = el("div", "model-activity running");
      row.append(
        el("span", "working-dot"),
        document.createTextNode(
          data.role === "reasoner"
            ? `${name} · формирует варианты действий`
            : `${name} · оценивает варианты`,
        ),
        el("time", "", eventTime(event)),
      );
      modelRows[data.role] = row;
      $("#timeline").append(row);
      break;
    }
    case "model_end":
      setNode(data.role, false, data.error ? "Ошибка" : "Ответ получен");
      modelRows[data.role]?.classList.remove("running");
      modelRows[data.role]
        ?.querySelector(".working-dot")
        ?.replaceWith(icon(data.error ? "pulse" : "check"));
      modelCalls = data.model_calls;
      $("#callsValue").textContent = Object.values(modelCalls).reduce(
        (sum, value) => sum + value,
        0,
      );
      if (data.error)
        $("#timeline").append(el("div", "diagnostic", data.error));
      break;
    case "selection":
      clearNodes();
      setNode("controller", true, "Действие выбрано");
      addSelection(data);
      if (data.routing)
        $("#circuitNote").textContent =
          data.routing.route === "local"
            ? "Контроллер выбрал действие без вызова моделей: " +
              data.chosen_action.rationale
            : data.routing.route === "reasoner+decider"
              ? "Qwen предложил действия, Jev оценил варианты. Контроллер и проверка безопасности выбрали итог."
              : "Контроллер выбрал действие из доступных вариантов.";
      break;
    case "tool_start": {
      setNode("controller", false, "Действие выбрано");
      setNode("tools", true, "Выполняет");
      $("#liveText").textContent =
        toolNames[data.action.tool] || data.action.tool;
      stepCards.get(data.action.id)?.classList.add("running");
      break;
    }
    case "tool_end": {
      setNode("tools", false, data.outcome.success ? "Завершено" : "Ошибка");
      updateObservation(data.observation);
      const card = stepCards.get(data.action.id);
      if (card) {
        card.classList.remove("running");
        card.classList.add(data.outcome.success ? "done" : "failed");
        card
          .querySelector(".step-icon")
          .replaceChildren(icon(data.outcome.success ? "check" : "pulse"));
        const output = [
          data.outcome.stdout,
          data.outcome.stderr,
          data.outcome.error_message,
        ]
          .filter(Boolean)
          .join("\n");
        const payload = data.outcome.payload;
        const text =
          output ||
          payload.content ||
          (Object.keys(payload).length
            ? JSON.stringify(payload, null, 2)
            : "Действие завершено успешно.");
        const block = details(
          data.outcome.success ? "Результат действия" : "Ошибка действия",
          el("pre", "", text),
        );
        if (!data.outcome.success || data.action.tool === "run_tests") {
          block.open = true;
          block.classList.add("auto-open");
        }
        card.append(block);
      }
      if (
        data.outcome.success &&
        ["edit_file", "create_file"].includes(data.action.tool)
      ) {
        changes.push({
          action: data.action,
          step: data.observation.step_number,
        });
        const files = new Set(
          changes.map((change) => change.action.arguments.path),
        ).size;
        $("#changeCount").textContent = files;
        $("#filesValue").textContent = files;
        if (currentTab === "changes") renderChanges();
      }
      break;
    }
    case "diagnostic":
      $("#timeline").append(
        el("div", "diagnostic", data.routing?.error || data.message),
      );
      break;
    case "finished":
      finishRun(data);
      source?.close();
      break;
  }
  if (nearBottom && currentTab === "conversation")
    $("#content").scrollTop = $("#content").scrollHeight;
}
function openRun(run) {
  source?.close();
  selected = run;
  seen = new Set();
  stepCards = new Map();
  changes = [];
  modelRows = {};
  $("#timeline").replaceChildren();
  $("#logs").replaceChildren();
  $("#changes").replaceChildren();
  scrollPositions = { conversation: 0, changes: 0, logs: 0 };
  $("#content").scrollTop = 0;
  $("#welcome").hidden = true;
  $("#changeCount").textContent = "0";
  $("#timeline").append(el("div", "user-message", run.request.task));
  const intro = el("div", "agent-intro");
  const mark = el("span", "brand-mark");
  mark.append(icon("fly"));
  intro.append(
    mark,
    document.createTextNode("FlyCode"),
    el(
      "span",
      "",
      run.request.llm === "heuristic"
        ? "Демонстрация без моделей"
        : "Локальные модели",
    ),
  );
  $("#timeline").append(intro);
  $("#projectTitle").textContent = projectName(run.request.project);
  $("#taskTitle").textContent = run.request.task;
  $("#projectSelect").value = run.request.project;
  $("#llmSelect").value = run.request.llm;
  $("#maxSteps").value = run.request.max_steps;
  $("#reasonerModel").value = run.request.model;
  $("#deciderModel").value = run.request.decision_model;
  $("#taskInput").value = "";
  startedAt = activeStatuses.has(run.status)
    ? new Date(run.created_at).getTime()
    : null;
  resetInspector();
  controls();
  renderHistory();
  setTab("conversation");
  showError("");
  localStorage.setItem("flycode.selected", run.id);
  $(".app-shell").classList.remove("sidebar-open");
  $("#menuToggle").setAttribute("aria-expanded", "false");
  $("#eventConnection").textContent = activeStatuses.has(run.status)
    ? "Подключаемся…"
    : "Загружаем историю…";
  source = new EventSource(`/api/runs/${run.id}/events`);
  const stream = source;
  stream.onmessage = (message) => {
    if (selected?.id !== run.id) return;
    try {
      handleEvent(JSON.parse(message.data));
    } catch (error) {
      showError("Не удалось отобразить событие: " + error.message);
    }
  };
  stream.onopen = () => {
    $("#eventConnection").textContent = activeStatuses.has(selected.status)
      ? "● События в реальном времени"
      : "История сохранена";
  };
  stream.onerror = async () => {
    if (selected?.id !== run.id) return;
    try {
      const latest = await api("/api/runs/" + run.id);
      if (!activeStatuses.has(latest.status)) {
        stream.close();
        // A finished event may arrive after the status is saved; replay is retried
        // before showing an interrupted session that has no final event.
        if (latest.status === "interrupted") finishRun(latest);
        else if (!$("#timeline .completion-card")) {
          const response = await fetch(
            `/api/runs/${run.id}/events?after=${Math.max(0, ...seen)}`,
          );
          for (const line of (await response.text()).split("\n"))
            if (line.startsWith("data: "))
              handleEvent(JSON.parse(line.slice(6)));
        }
        $("#eventConnection").textContent = "История сохранена";
      } else $("#eventConnection").textContent = "Переподключение…";
    } catch {
      $("#eventConnection").textContent = "Нет связи с сервером · повторяем";
    }
  };
}
function newTask() {
  const running = activeRun();
  if (running) {
    toast("Сначала завершите или остановите текущую задачу.");
    openRun(running);
    return;
  }
  source?.close();
  selected = null;
  localStorage.removeItem("flycode.selected");
  $("#timeline").replaceChildren();
  $("#logs").replaceChildren();
  changes = [];
  seen.clear();
  $("#welcome").hidden = false;
  $("#taskTitle").textContent = "Новая задача";
  $("#projectTitle").textContent = "Рабочее пространство";
  $("#changeCount").textContent = "0";
  $("#taskInput").value = "";
  $("#eventConnection").textContent = "Локальная сессия";
  resetInspector();
  controls();
  renderHistory();
  setTab("conversation");
  showError("");
  $(".app-shell").classList.remove("sidebar-open");
  $("#menuToggle").setAttribute("aria-expanded", "false");
  $("#taskInput").focus();
}
async function refreshModels() {
  $("#connectionLabel").textContent = "Проверяем подключение…";
  try {
    modelHealth = await api("/api/models");
  } catch {
    modelHealth = {
      connected: false,
      models: [],
      error: "Нет связи с сервером FlyCode.",
    };
  }
  $("#connectionLabel").textContent = modelHealth.connected
    ? `${modelHealth.models.length} моделей доступны`
    : "Нет подключения";
  $("#modelHealth").textContent = modelHealth.connected
    ? "Сервер доступен. Модели: " + modelHealth.models.join(", ")
    : modelHealth.error;
  $("#availableModels").replaceChildren(
    ...modelHealth.models.map((model) => {
      const option = el("option");
      option.value = model;
      return option;
    }),
  );
}
function openSettings() {
  $("#settingsDialog").showModal();
}
async function submitTask(event) {
  event.preventDefault();
  showError("");
  if (activeRun()) return;
  const task = $("#taskInput").value.trim();
  if (!task) {
    $("#taskInput").focus();
    return;
  }
  const body = {
    task,
    project: $("#projectSelect").value,
    llm: $("#llmSelect").value,
    max_steps: Number($("#maxSteps").value),
    model: $("#reasonerModel").value.trim(),
    decision_model: $("#deciderModel").value.trim(),
  };
  if (
    !Number.isInteger(body.max_steps) ||
    body.max_steps < 1 ||
    body.max_steps > 100
  ) {
    showError("Укажите от 1 до 100 шагов в настройках.");
    openSettings();
    return;
  }
  $("#sendButton").disabled = true;
  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    });
    history.unshift(run);
    openRun(run);
  } catch (error) {
    showError(error.message);
    $("#sendButton").disabled = false;
  }
}
$("#taskForm").addEventListener("submit", submitTask);
$("#newTask").addEventListener("click", newTask);
$("#search").addEventListener("input", renderHistory);
$("#settingsButton").addEventListener("click", openSettings);
$("#connectionButton").addEventListener("click", openSettings);
$("#refreshModels").addEventListener("click", refreshModels);
$("#saveSettings").addEventListener("click", () => {
  controls();
  $("#settingsDialog").close();
});
$("#llmSelect").addEventListener("change", controls);
$("#projectSelect").addEventListener("change", () => {
  if ($("#projectSelect").value !== "demo") $("#llmSelect").value = "lmstudio";
  controls();
});
$("#demoSuggestion").addEventListener("click", () => {
  $("#taskInput").value =
    "Исправь ошибку в арифметике и убедись, что тесты проходят.";
  $("#projectSelect").value = "demo";
  $("#llmSelect").value = "heuristic";
  controls();
  $("#taskInput").focus();
});
$("#projectSuggestion").addEventListener("click", () => {
  $("#projectSelect").value = ".";
  $("#llmSelect").value = "lmstudio";
  controls();
  $("#projectSelect").focus();
  toast(
    "Выберите проект и опишите задачу. Изменения применяются прямо в его файлах.",
  );
});
$("#stopButton").addEventListener("click", async () => {
  const run = activeRun();
  if (!run) return;
  $("#stopButton").disabled = true;
  try {
    const record = await api(`/api/runs/${run.id}/stop`, { method: "POST" });
    history = history.map((item) => (item.id === record.id ? record : item));
    if (selected?.id === record.id) selected = record;
    controls();
    renderHistory();
  } catch (error) {
    showError(error.message);
    $("#stopButton").disabled = false;
  }
});
for (const button of $$(".tab")) {
  button.addEventListener("click", () => setTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = $$(".tab");
    const index =
      (tabs.indexOf(button) +
        (event.key === "ArrowRight" ? 1 : -1) +
        tabs.length) %
      tabs.length;
    setTab(tabs[index].dataset.tab);
    tabs[index].focus();
  });
}
function closeInspector() {
  $(".app-shell").classList.remove("inspector-open");
  $(".app-shell").classList.add("inspector-hidden");
  $("#inspectorToggle").setAttribute("aria-expanded", "false");
}
$("#inspectorToggle").addEventListener("click", () => {
  const shell = $(".app-shell");
  if (innerWidth <= 1190) {
    const opened = shell.classList.toggle("inspector-open");
    shell.classList.remove("inspector-hidden");
    $("#inspectorToggle").setAttribute("aria-expanded", String(opened));
  } else {
    const hidden = shell.classList.toggle("inspector-hidden");
    $("#inspectorToggle").setAttribute("aria-expanded", String(!hidden));
  }
});
$("#inspectorClose").addEventListener("click", closeInspector);
$("#menuToggle").addEventListener("click", () => {
  const open = $(".app-shell").classList.toggle("sidebar-open");
  $("#menuToggle").setAttribute("aria-expanded", String(open));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeInspector();
    $(".app-shell").classList.remove("sidebar-open");
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    newTask();
  }
  if (
    (event.metaKey || event.ctrlKey) &&
    event.key === "Enter" &&
    !$("#settingsDialog").open
  ) {
    event.preventDefault();
    $("#taskForm").requestSubmit();
  }
});
setInterval(() => {
  if (startedAt)
    $("#elapsed").textContent = formatTime((Date.now() - startedAt) / 1000);
}, 1000);
(async () => {
  try {
    bootstrap = await api("/api/bootstrap");
    $("#projectSelect").replaceChildren(
      ...bootstrap.projects.map((project) => {
        const option = el("option", "", project.name);
        option.value = project.id;
        option.title = project.description;
        return option;
      }),
    );
    history = await api("/api/runs");
    renderHistory();
    controls();
    renderChanges();
    const selectedId = localStorage.getItem("flycode.selected");
    const previous =
      activeRun() || history.find((run) => run.id === selectedId);
    if (previous) openRun(previous);
    refreshModels();
    $("#inspectorToggle").setAttribute(
      "aria-expanded",
      String(innerWidth > 1190),
    );
  } catch (error) {
    showError("Не удалось загрузить рабочее пространство. " + error.message);
    $("#sendButton").disabled = true;
  }
})();
