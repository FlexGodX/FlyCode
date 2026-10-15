// Interface acceptance against an isolated FlyCode UI with empty history.
const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const base = process.env.FLYCODE_UI_URL || "http://127.0.0.1:8796";
  const output = process.env.FLYCODE_SCREENSHOTS || "/tmp/flycode-desktop-screenshots";
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: "chrome", headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(() => !document.querySelector("#projectSelect").disabled);
    await page.screenshot({path: path.join(output, "classic-before.png")});
    await page.getByRole("link", {name: "Desktop-интерфейс", exact: true}).click();
    await page.getByRole("heading", {name: "Что будем создавать?"}).waitFor();
    await page.waitForFunction(() => !document.querySelector("#projectSelect").disabled);
    assert.equal(new URL(page.url()).pathname, "/desktop");
    assert.ok(await page.locator("body.desktop-ui").count());
    await page.screenshot({path: path.join(output, "desktop-welcome.png")});
    await page.locator("#menuToggle").click();
    assert.equal(await page.locator("#sidebar").isVisible(), false);
    await page.keyboard.press("Control+b");
    assert.equal(await page.locator("#sidebar").isVisible(), true);
    await page.locator("#desktopTheme").click();
    const theme = await page.locator("body").getAttribute("data-desktop-theme");
    await page.reload();
    await page.waitForFunction(() => !document.querySelector("#projectSelect").disabled);
    assert.equal(await page.locator("body").getAttribute("data-desktop-theme"), theme);
    await page.screenshot({path: path.join(output, "desktop-theme.png")});
    await page.locator("#modelsButton").click();
    await page.locator("#modelsDialog").waitFor({state: "visible"});
    await page.getByRole("button", {name: "Закрыть выбор моделей"}).click();
    await page.locator("#settingsButton").click();
    await page.locator("#settingsDialog").waitFor({state: "visible"});
    await page.locator("#saveSettings").click();
    await page.getByRole("button", {name: "Попробовать на демо"}).click();
    await page.getByRole("button", {name: "Запустить задачу"}).click();
    await page.getByText("Задача выполнена", {exact: true}).waitFor({timeout: 20000});
    await page.getByRole("tab", {name: /Изменения/}).click();
    assert.match(await page.locator("#changes").innerText(), /a \+ b/);
    await page.screenshot({path: path.join(output, "desktop-changes.png")});
    await page.getByRole("tab", {name: "Диалог", exact: true}).click();
    await page.reload();
    await page.getByText("Задача выполнена", {exact: true}).waitFor();
    assert.ok(await page.locator("#history .history-item").count());
    await page.locator("#inspectorToggle").click();
    assert.equal(await page.locator("#inspector").isVisible(), true);
    await page.screenshot({path: path.join(output, "desktop-inspector.png")});
    await page.keyboard.press("Escape");
    await page.getByRole("button", {name: "Новая задача", exact: true}).click();
    await page.getByRole("button", {name: "Попробовать на демо"}).click();
    await page.getByRole("button", {name: "Запустить задачу"}).click();
    await page.getByRole("button", {name: "Остановить", exact: true}).click();
    await page.getByText("Агент остановлен", {exact: true}).waitFor();
    await page.keyboard.press("Control+k");
    assert.equal(await page.locator("#timeline").innerText(), "");
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const model = await page.locator("#modelsButton").boundingBox();
      const settings = await page.locator("#settingsButton").boundingBox();
      assert.ok(model.height >= 44 && settings.height >= 44);
      assert.ok(settings.x >= model.x + model.width);
      await page.locator("#menuToggle").click();
      await page.getByRole("button", {name: "Без проекта", exact: true}).click();
      assert.equal(await page.locator("#projectSelect").inputValue(), "");
      await page.getByRole("button", {name: "Запустить задачу"}).waitFor();
      await page.screenshot({path: path.join(output, `desktop-mobile-${width}.png`)});
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await page.getByRole("link", {name: "Классический интерфейс", exact: true}).click();
    assert.equal(new URL(page.url()).pathname, "/");
    assert.equal(await page.locator("body.desktop-ui").count(), 0);
    assert.equal(await page.locator('link[href="/static/desktop.css"]').count(), 0);
    await page.screenshot({path: path.join(output, "classic-after.png")});
    assert.deepEqual(errors, []);
    console.log("Desktop browser acceptance passed; screenshots: " + output);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
