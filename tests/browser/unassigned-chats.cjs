// Offline browser acceptance against a real, isolated FlyCode server.
const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "flycode-chats-"));
  fs.mkdirSync(path.join(root, "workspace"));
  // Only model availability is external to the UI under test.
  const models = http.createServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(request.url.endsWith("/models") ? {data: [{id: "reasoner"}]} : {
      choices: [{message: {content: "[]"}, finish_reason: "stop"}], usage: {},
    }));
  });
  await new Promise(resolve => models.listen(0, "127.0.0.1", resolve));
  const port = process.env.FLYCODE_CHAT_TEST_PORT || "8796";
  const url = `http://127.0.0.1:${port}`;
  let serverOutput = "";
  const server = spawn(path.resolve(".venv/bin/python"), ["-m", "flycode.cli", "ui",
    "--workspace", path.join(root, "workspace"), "--data-dir", path.join(root, "history"), "--port", port], {
    env: {...process.env, LMSTUDIO_BASE_URL: `http://127.0.0.1:${models.address().port}/v1`},
    stdio: ["ignore", "pipe", "pipe"],
  });
  for (const stream of [server.stdout, server.stderr]) stream.on("data", data => {serverOutput += data;});
  let browser;
  try {
    const deadline = Date.now() + 15000;
    while (true) {
      if (server.exitCode !== null) throw new Error(serverOutput);
      if (await fetch(url).then(response => response.ok).catch(() => false)) break;
      if (Date.now() > deadline) throw new Error("UI startup timed out: " + serverOutput);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({channel: "chrome", headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    await page.addInitScript(() => localStorage.setItem("flycode.models", JSON.stringify({
      llm: "lmstudio", model: "reasoner", decision_model: null,
    })));
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(url + (process.env.FLYCODE_UI_PATH || "/"));
    await page.getByRole("button", {name: "Без проекта", exact: true}).click();
    assert.equal(await page.locator("#projectSelect").inputValue(), "");
    assert.equal(await page.locator("#projectTitle").innerText(), "Без проекта");
    await page.locator("#settingsButton").click();
    await page.getByText("Бюджеты и повторяемость", {exact: true}).click();
    await page.locator("#maxSteps").fill("1");
    await page.locator("#saveSettings").click();
    const ids = [];
    for (const task of ["First independent chat", "Second independent chat"]) {
      await page.getByRole("button", {name: "Новая задача", exact: true}).click();
      await page.getByRole("textbox", {name: "Опишите задачу"}).fill(task);
      const created = page.waitForResponse(response => response.url().endsWith("/api/runs") && response.request().method() === "POST");
      await page.getByRole("button", {name: "Запустить задачу"}).click();
      const response = await created;
      assert.equal(response.status(), 201);
      const record = await response.json();
      assert.equal(record.request.project, null);
      ids.push(record.id);
      await page.locator(".completion-card").waitFor();
      assert.equal(await page.locator("#projectSelect").inputValue(), "");
      assert.ok(fs.existsSync(path.join(root, "history", record.id, "repository")));
      assert.ok(!fs.existsSync(path.join(root, "history", record.id, "repository", "mathutils.py")));
    }
    assert.notEqual(ids[0], ids[1]);
    assert.equal(await page.locator("#history .history-item").count(), 2);
    await page.getByRole("navigation", {name: "Проекты"}).getByRole("button", {name: "workspace", exact: true}).click();
    assert.equal(await page.locator("#history .history-item").count(), 0);
    await page.getByRole("button", {name: "Без проекта", exact: true}).click();
    await page.getByRole("button", {name: /First independent chat/}).click();
    await page.reload();
    await page.locator(".completion-card").waitFor();
    assert.equal(await page.locator("#projectTitle").innerText(), "Без проекта");
    assert.equal(await page.locator("#history .history-item").count(), 2);
    await page.screenshot({path: path.join(root, "desktop.png")});
    await page.setViewportSize({width: 320, height: 844});
    await page.locator("#menuToggle").click();
    await page.getByRole("button", {name: "Без проекта", exact: true}).click();
    assert.equal(await page.locator("#projectSelect").inputValue(), "");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: path.join(root, "mobile.png")});
    assert.deepEqual(errors, []);
    console.log("Unassigned chat browser acceptance passed; screenshots: " + root);
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
    models.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
