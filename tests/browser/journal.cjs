// Journal acceptance uses real demo tools, SSE replay and an actual download.
const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.FLYCODE_UI_URL || 'http://127.0.0.1:8796';
  const output = process.env.FLYCODE_SCREENSHOTS || '/tmp/flycode-journal-screenshots';
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    for (const surface of ['/', '/desktop']) {
      const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + surface);
      await page.waitForFunction(() => !document.querySelector('#projectSelect').disabled);
      await page.getByRole('button', {name: 'Попробовать на демо'}).click();
      await page.getByRole('button', {name: 'Запустить задачу'}).click();
      await page.getByText('Задача выполнена', {exact: true}).waitFor({timeout: 25000});
      await page.getByRole('tab', {name: 'Журнал', exact: true}).click();
      await page.getByRole('searchbox', {name: 'Поиск в журнале'}).waitFor({timeout: 3000});
      const total = await page.locator('#journalCount').innerText();
      assert.ok(await page.locator('#journalEntries .log-event').count() > 5);
      await page.locator('#journalSource').selectOption('tools');
      assert.ok((await page.locator('#journalEntries').innerText()).includes('edit_file'));
      await page.locator('#journalSearch').fill('not-present-in-any-event');
      await page.getByText('Нет событий по выбранным фильтрам.', {exact: true}).waitFor();
      await page.locator('#journalSearch').fill('edit_file');
      await page.locator('#journalEntries summary').first().click();
      assert.match(await page.locator('#journalEntries pre').first().innerText(), /edit_file/);
      await page.locator('#journalSearch').fill('');
      await page.locator('#journalSource').selectOption('all');
      await page.locator('#journalErrors').check();
      assert.ok(await page.locator('#journalEntries .journal-error').count() > 0);
      await page.locator('#journalErrors').uncheck();
      const downloadEvent = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Скачать JSONL'}).click();
      const download = await downloadEvent;
      assert.match(download.suggestedFilename(), /^flycode-.*\.jsonl$/);
      const lines = fs.readFileSync(await download.path(), 'utf8').trim().split('\n').map(JSON.parse);
      assert.equal(lines.at(-1).kind, 'finished');
      assert.equal(new Set(lines.map(event => event.id)).size, lines.length);
      assert.equal(lines.length, await page.locator('#journalEntries .log-event').count());
      await page.reload();
      await page.getByText('Задача выполнена', {exact: true}).waitFor();
      await page.getByRole('tab', {name: 'Журнал', exact: true}).click();
      await page.waitForFunction(text => document.querySelector('#journalCount').textContent === text, total);
      const key = surface === '/' ? 'classic' : 'desktop';
      await page.screenshot({path: path.join(output, key + '-journal.png')});
      if (surface === '/desktop') {
        await page.locator('#desktopTheme').click();
        await page.screenshot({path: path.join(output, 'desktop-journal-dark.png')});
      }
      for (const width of [390, 320]) {
        await page.setViewportSize({width, height: 844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.locator('#journalSearch').fill('edit_file');
        assert.ok(await page.locator('#journalEntries .log-event').count() > 0);
        await page.screenshot({path: path.join(output, `${key}-journal-${width}.png`)});
      }
      await page.locator('#journalSearch').fill('');
      await page.locator('#journalFollow').uncheck();
      await page.locator('#journalEntries summary').first().click();
      await page.locator('#journalEntries summary').first().focus();
      await page.evaluate(() => handleEvent({id: 9999, kind: 'model_end', timestamp: new Date().toISOString(),
        data: {role: 'reasoner', model: 'local', model_calls: {}, error: 'test model failure'}}));
      await page.waitForFunction(() => document.querySelector('[data-event-id="9999"]'));
      assert.equal(await page.locator('#journalFollow').isChecked(), false);
      assert.equal(await page.locator('#journalEntries details').first().getAttribute('open'), '');
      assert.equal(await page.evaluate(() => document.activeElement.tagName), 'SUMMARY');
      await page.locator('#journalSource').selectOption('models');
      await page.locator('#journalErrors').check();
      assert.match(await page.locator('#journalEntries').innerText(), /model_end/);
      await page.locator('#journalSource').selectOption('all');
      await page.locator('#journalErrors').uncheck();
      await page.evaluate(() => {
        for (let id = 10000; id < 10250; id++) handleEvent({id, kind: 'custom_event', timestamp: new Date().toISOString(),
          data: {message: '<img src=x onerror="window.journalXss=true">'}});
        handleEvent({id: 10249, kind: 'custom_event', data: {message: 'duplicate'}});
      });
      await page.waitForFunction(() => document.querySelectorAll('#journalEntries .log-event').length === 100);
      await page.getByRole('button', {name: 'Показать более ранние'}).click();
      assert.equal(await page.locator('#journalEntries .log-event').count(), 200);
      await page.locator('#journalEntries summary').last().click();
      assert.match(await page.locator('#journalEntries pre').last().innerText(), /<img/);
      assert.equal(await page.locator('#journalEntries img').count(), 0);
      assert.equal(await page.evaluate(() => window.journalXss), undefined);
      await page.keyboard.press('Control+k');
      assert.equal(await page.locator('#journalSearch').inputValue(), '');
      assert.equal(await page.locator('#journalEntries details').count(), 0);
      assert.equal(await page.locator('#journalDownload').getAttribute('href'), null);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Journal browser acceptance passed; screenshots: ' + output);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
