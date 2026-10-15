"use strict";

// A projection of the existing event stream; journal controls never call a model.
class FlyCodeJournal {
  constructor(panel, content) {
    this.panel = panel;
    this.content = content;
    const node = (tag, text, className) => {
      const item = document.createElement(tag);
      if (text !== undefined) item.textContent = text;
      if (className) item.className = className;
      return item;
    };
    this.node = node;
    const header = node("div", undefined, "journal-header");
    const heading = node("div", undefined, "journal-heading");
    this.download = node("a", "Скачать JSONL", "journal-download");
    this.download.id = "journalDownload";
    this.download.setAttribute("download", "");
    heading.append(node("h2", "Ход выполнения"), this.download);
    this.count = node("p", "", "journal-count");
    this.count.id = "journalCount";
    this.search = node("input");
    this.search.id = "journalSearch";
    this.search.type = "search";
    this.search.placeholder = "Поиск по событиям, инструментам и результатам";
    this.search.setAttribute("aria-label", "Поиск в журнале");
    this.search.maxLength = 1000;
    this.source = node("select");
    this.source.id = "journalSource";
    this.source.setAttribute("aria-label", "Источник событий");
    for (const [value, label] of Object.entries({all: "Все источники", models: "Модели", tools: "Инструменты",
      controller: "Контроллер", runtime: "Сессия"})) {
      const option = node("option", label);
      option.value = value;
      this.source.append(option);
    }
    const filters = node("div", undefined, "journal-filters");
    const checkbox = (id, text) => {
      const label = node("label");
      const input = node("input");
      input.type = "checkbox";
      input.id = id;
      label.append(input, document.createTextNode(text));
      filters.append(label);
      return input;
    };
    filters.append(this.source);
    this.errors = checkbox("journalErrors", "Только ошибки");
    this.follow = checkbox("journalFollow", "Следить");
    header.append(heading, this.count, this.search, filters);
    this.more = node("button", "Показать более ранние", "journal-more");
    this.more.type = "button";
    this.list = node("div");
    this.list.id = "journalEntries";
    this.empty = node("p", "", "journal-empty");
    panel.append(header, this.more, this.empty, this.list);
    for (const control of [this.search, this.source, this.errors]) control.addEventListener("input", () => {
      this.limit = 100;
      this.render();
    });
    this.more.addEventListener("click", () => { this.limit += 100; this.render(); });
    this.reset(null);
  }

  reset(runId) {
    this.list.replaceChildren();
    this.entries = [];
    this.ids = new Set();
    this.open = new Set();
    this.limit = 100;
    this.search.value = "";
    this.source.value = "all";
    this.errors.checked = false;
    this.follow.checked = true;
    this.download.hidden = !runId;
    if (runId) this.download.href = `/api/runs/${encodeURIComponent(runId)}/journal`;
    else this.download.removeAttribute("href");
    this.render();
  }

  add(event) {
    if (this.ids.has(event.id)) return;
    this.ids.add(event.id);
    const data = event.data || {};
    const source = event.kind.startsWith("model_") ? "models" : event.kind.startsWith("tool_") ? "tools" :
      ["controller", "selection", "planning", "shadow", "autonomy", "rollout", "learning", "rollout_rollback",
        "autonomy_rollback"].includes(event.kind) ? "controller" : "runtime";
    const error = Boolean(data.error) || (event.kind === "tool_end" && data.outcome?.success === false) ||
      (event.kind === "finished" && data.status === "failed");
    const label = data.action?.tool || data.model || data.message || data.phase || data.mode || data.status || "";
    const title = `${event.kind}${label ? " · " + label : ""}`;
    const step = data.step ?? data.observation?.step_number;
    this.entries.push({event, source, error, title, step,
      search: JSON.stringify(event).toLocaleLowerCase()});
    if (!this.pending) this.pending = requestAnimationFrame(() => {
      this.pending = null;
      this.render();
      if (this.follow.checked && !this.panel.hidden) this.content.scrollTop = this.content.scrollHeight;
    });
  }

  render() {
    const focused = document.activeElement?.closest("#journalEntries details")?.dataset.eventId;
    for (const row of this.list.querySelectorAll("details")) {
      if (row.open) this.open.add(Number(row.dataset.eventId));
      else this.open.delete(Number(row.dataset.eventId));
    }
    const query = this.search.value.trim().toLocaleLowerCase();
    const matches = this.entries.filter(entry => (!query || entry.search.includes(query)) &&
      (this.source.value === "all" || entry.source === this.source.value) && (!this.errors.checked || entry.error));
    const shown = matches.slice(-this.limit);
    const errors = this.entries.filter(entry => entry.error).length;
    this.count.textContent = `${matches.length} из ${this.entries.length} событий · ошибок: ${errors}`;
    this.more.hidden = matches.length <= this.limit;
    this.empty.hidden = matches.length > 0;
    this.empty.textContent = this.entries.length ? "Нет событий по выбранным фильтрам." : "События появятся после запуска задачи.";
    const fragment = document.createDocumentFragment();
    for (const entry of shown) {
      const {event} = entry;
      const row = this.node("details", undefined, "log-event" + (entry.error ? " journal-error" : ""));
      row.dataset.eventId = event.id;
      const summary = this.node("summary");
      const time = this.node("time", new Date(event.timestamp).toLocaleTimeString("ru-RU", {hour12: false}));
      time.dateTime = event.timestamp;
      const title = this.node("span", undefined, "journal-event-title");
      title.append(this.node("span", event.kind), document.createTextNode(entry.title.slice(event.kind.length)));
      summary.append(time, title,
        this.node("span", `#${event.id}${entry.step !== undefined ? " · шаг " + entry.step : ""}`, "journal-event-id"));
      row.append(summary);
      const fill = () => {
        if (row.open && !row.querySelector("pre")) row.append(this.node("pre", JSON.stringify(event, null, 2)));
      };
      row.open = this.open.has(event.id);
      fill();
      row.addEventListener("toggle", fill);
      fragment.append(row);
    }
    this.list.replaceChildren(fragment);
    if (focused) this.list.querySelector(`[data-event-id="${Number(focused)}"] summary`)?.focus({preventScroll: true});
  }
}
