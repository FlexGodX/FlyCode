"use strict";

(() => {
  const find = selector => document.querySelector(selector);
  const shell = find(".app-shell");
  const sidebarToggle = find("#menuToggle");
  const inspectorToggle = find("#inspectorToggle");
  const classic = find('.interface-switch[href="/desktop"]');
  classic.href = "/";
  classic.firstChild.textContent = "Классический интерфейс";
  find('.interface-switch[href="/verification"]').href = "/verification?interface=desktop";
  find(".brand").href = "/desktop";
  find(".version").textContent = "DESKTOP";
  find("#search").placeholder = "Поиск чатов";
  find("#newTask").childNodes.forEach(node => {
    if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) node.textContent = "Новый чат";
  });
  find(".composer-wrap").append(find(".suggestions"));
  shell.classList.add("inspector-hidden");

  const themeToggle = document.createElement("button");
  themeToggle.id = "desktopTheme";
  themeToggle.type = "button";
  themeToggle.className = "icon-button";
  const themeIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  themeIcon.setAttribute("viewBox", "0 0 24 24");
  themeIcon.setAttribute("aria-hidden", "true");
  themeIcon.innerHTML = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>';
  themeToggle.append(themeIcon);
  inspectorToggle.before(themeToggle);

  function preference(key, value) {
    try {
      if (value === undefined) return localStorage.getItem("flycode.desktop." + key);
      localStorage.setItem("flycode.desktop." + key, value);
    } catch { /* Presentation preferences must not prevent startup. */ }
  }
  function setTheme(theme) {
    document.body.dataset.desktopTheme = theme;
    themeToggle.setAttribute("aria-label", theme === "dark" ? "Светлая тема" : "Тёмная тема");
    themeToggle.title = themeToggle.getAttribute("aria-label");
  }
  setTheme(preference("theme") === "dark" ? "dark" : "light");
  themeToggle.addEventListener("click", () => {
    const theme = document.body.dataset.desktopTheme === "dark" ? "light" : "dark";
    setTheme(theme);
    preference("theme", theme);
  });

  function syncPanels() {
    const narrow = innerWidth <= 700;
    const navigationOpen = narrow ? shell.classList.contains("sidebar-open")
      : !shell.classList.contains("desktop-sidebar-collapsed");
    sidebarToggle.setAttribute("aria-expanded", String(navigationOpen));
    sidebarToggle.setAttribute("aria-label", navigationOpen ? "Скрыть боковую панель" : "Показать боковую панель");
    const inspectorOpen = innerWidth <= 1190 ? shell.classList.contains("inspector-open")
      : !shell.classList.contains("inspector-hidden");
    if (inspectorToggle.getAttribute("aria-expanded") !== String(inspectorOpen))
      inspectorToggle.setAttribute("aria-expanded", String(inspectorOpen));
  }
  function toggleNavigation() {
    if (innerWidth <= 700) shell.classList.toggle("sidebar-open");
    else {
      const collapsed = shell.classList.toggle("desktop-sidebar-collapsed");
      preference("sidebar", collapsed ? "closed" : "open");
    }
    syncPanels();
  }
  if (preference("sidebar") === "closed") shell.classList.add("desktop-sidebar-collapsed");
  sidebarToggle.addEventListener("click", event => {
    event.stopImmediatePropagation();
    toggleNavigation();
  }, true);
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b" && !find("dialog[open]")) {
      event.preventDefault();
      toggleNavigation();
    }
  });
  new MutationObserver(syncPanels).observe(shell, {attributes: true, attributeFilter: ["class"]});
  new MutationObserver(syncPanels).observe(inspectorToggle, {attributes: true, attributeFilter: ["aria-expanded"]});
  window.addEventListener("resize", syncPanels);
  syncPanels();
})();
