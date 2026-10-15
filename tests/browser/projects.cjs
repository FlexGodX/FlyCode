// Run against an isolated FlyCode UI with empty history.
const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const root = process.env.FLYCODE_PROJECT_TEST_ROOT;
  assert.ok(root, "Set FLYCODE_PROJECT_TEST_ROOT to an isolated temporary directory");
  const output = path.join(root, "screenshots");
  fs.mkdirSync(output, {recursive: true});
  fs.mkdirSync(path.join(root, "existing"), {recursive: true});
  const browser = await chromium.launch({channel: "chrome", headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    let releaseBootstrap;
    const bootstrapGate = new Promise(resolve => {releaseBootstrap = resolve;});
    await page.route("**/api/bootstrap", async route => {
      await bootstrapGate;
      await route.continue();
    });
    await page.goto(process.env.FLYCODE_UI_URL || "http://127.0.0.1:8792");
    assert.equal(await page.getByRole("button", {name: "Попробовать на демо"}).isDisabled(), true);
    assert.equal(await page.getByRole("button", {name: "Создать проект", exact: true}).isDisabled(), true);
    releaseBootstrap();
    await page.locator("#projectSelect option").first().waitFor({state: "attached"});
    await page.getByRole("button", {name: "Создать проект", exact: true}).click();
    await page.getByRole("textbox", {name: "Название проекта"}).fill("Local notes");
    const choices = [path.join(root, "existing"), null, path.join(root, "new-folder")];
    const pickerModes = [];
    await page.route("**/api/projects/choose-folder", route => {
      pickerModes.push(route.request().postDataJSON().mode);
      return route.fulfill({json: {path: choices.shift()}});
    });
    await page.getByRole("button", {name: "Выбрать папку…", exact: true}).click();
    await page.waitForFunction(value => document.querySelector("#projectPath").value === value, path.join(root, "existing"));
    await page.getByRole("button", {name: "Выбрать папку…", exact: true}).click();
    await page.getByRole("button", {name: "Выбрать папку…", exact: true}).waitFor();
    assert.equal(await page.locator("#projectPath").inputValue(), path.join(root, "existing"));
    await page.getByRole("button", {name: "Сохранить проект", exact: true}).click();
    await page.locator("#projectDialog").waitFor({state: "hidden"});
    const notes = await page.locator("#projectSelect").inputValue();
    assert.match(notes, /^local:/);
    assert.equal(await page.locator("#projectTitle").innerText(), "Local notes");
    // Exercise two real chats without calling local models.
    await page.getByRole("button", {name: "Попробовать на демо"}).click();
    await page.getByRole("textbox", {name: "Опишите задачу"}).fill("First demo chat");
    await page.getByRole("button", {name: "Запустить задачу"}).click();
    await page.getByText("Задача выполнена", {exact: true}).waitFor();
    await page.getByRole("button", {name: "Новая задача", exact: true}).click();
    assert.equal(await page.locator("#projectSelect").inputValue(), "demo");
    await page.getByRole("textbox", {name: "Опишите задачу"}).fill("Second demo chat");
    await page.getByRole("button", {name: "Запустить задачу"}).click();
    await page.getByText("Задача выполнена", {exact: true}).waitFor();
    assert.equal(await page.locator("#history .history-item").count(), 2);
    await page.getByRole("navigation", {name: "Проекты"}).getByRole("button", {name: "Local notes", exact: true}).click();
    assert.equal(await page.locator("#history .history-item").count(), 0);
    assert.equal(await page.locator("#projectSelect").inputValue(), notes);
    await page.reload();
    await page.locator("#projectSelect option").first().waitFor({state: "attached"});
    assert.equal(await page.locator("#projectSelect").inputValue(), notes);
    assert.equal(await page.locator("#history .history-item").count(), 0);
    await page.getByRole("button", {name: "Создать проект", exact: true}).click();
    await page.getByRole("textbox", {name: "Название проекта"}).fill("New project");
    await page.getByRole("combobox", {name: "Папка проекта"}).selectOption("create");
    await page.getByRole("button", {name: "Выбрать папку…", exact: true}).click();
    await page.waitForFunction(value => document.querySelector("#projectPath").value === value, path.join(root, "new-folder"));
    assert.deepEqual(pickerModes, ["existing", "existing", "create"]);
    await page.screenshot({path: path.join(output, "desktop-dialog.png")});
    await page.getByRole("button", {name: "Сохранить проект", exact: true}).click();
    await page.locator("#projectDialog").waitFor({state: "hidden"});
    assert.ok(fs.statSync(path.join(root, "new-folder")).isDirectory());
    assert.ok(!fs.existsSync(path.join(root, "new-folder", ".git")));
    await page.screenshot({path: path.join(output, "desktop-projects.png")});
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 844});
      if (!(await page.locator("#sidebar").isVisible()))
        await page.locator("#menuToggle").click();
      await page.getByRole("button", {name: "Создать проект", exact: true}).click();
      await page.screenshot({path: path.join(output, `mobile-dialog-${width}.png`)});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.getByRole("button", {name: "Закрыть создание проекта"}).click();
    }
    assert.deepEqual(errors, []);
    console.log("Project browser acceptance passed; screenshots: " + output);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
