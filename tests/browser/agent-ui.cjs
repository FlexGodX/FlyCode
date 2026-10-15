// Run against `flycode ui`; Playwright is a development-only dependency.
const { chromium } = require(process.env.FLYCODE_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
(async () => {
  const out = process.env.FLYCODE_SCREENSHOTS || "/tmp/flycode-ui-screenshots";
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(process.env.FLYCODE_UI_URL || "http://127.0.0.1:8790");
  await page.getByRole("heading", { name: "Что будем создавать?" }).waitFor();
  await page.screenshot({
    path: path.join(out, "desktop-before.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Попробовать на демо" }).click();
  await page.getByRole("button", { name: "Запустить задачу" }).click();
  await page
    .getByText("Задача выполнена", { exact: true })
    .waitFor({ timeout: 20000 });
  assert.ok(
    await page.getByText("Все обязательные проверки пройдены.").isVisible(),
  );
  assert.ok((await page.locator(".step-card").count()) > 2);
  await page.getByRole("tab", { name: /Изменения/ }).click();
  assert.ok(
    (await page.getByText("mathutils.py", { exact: true }).count()) > 0,
  );
  assert.match(await page.locator("#changes").innerText(), /a \+ b/);
  await page.screenshot({
    path: path.join(out, "desktop-changes.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Диалог", exact: true }).click();
  await page.screenshot({
    path: path.join(out, "desktop-after.png"),
    fullPage: true,
  });
  await page.reload();
  await page.getByText("Задача выполнена", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "Журнал", exact: true }).click();
  assert.ok((await page.getByText("tool_end", { exact: true }).count()) > 0);
  await page.getByRole("button", { name: "Новая задача", exact: true }).click();
  await page.getByRole("button", { name: "Попробовать на демо" }).click();
  await page
    .getByRole("textbox", { name: "Опишите задачу" })
    .fill("<img src=x onerror=alert(1)>");
  await page.getByRole("button", { name: "Запустить задачу" }).click();
  await page.getByText("Задача выполнена", { exact: true }).waitFor();
  assert.equal(await page.locator("#timeline img").count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(out, "mobile-after.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  await page.locator('#settingsButton').click();
  await page.getByText('Наблюдение, память и допуск', {exact: true}).click();
  assert.ok(await page.locator('#observerSelect').isVisible());
  await page.locator('#learningEnabled').check();
  await page.locator('#memoryProfile').fill('browser-check');
  await page.screenshot({path: path.join(out, 'mobile-controller-settings.png'), fullPage: true});
  await page.locator('#saveSettings').click();
  await page
    .getByRole("button", { name: "Состояние агента", exact: true })
    .click();
  assert.ok(
    await page.getByText("Functional Fly", { exact: true }).isVisible(),
  );
  await page.screenshot({
    path: path.join(out, "mobile-inspector.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  await browser.close();
  console.log("Browser acceptance passed; screenshots: " + out);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
