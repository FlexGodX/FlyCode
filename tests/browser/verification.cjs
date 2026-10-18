const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const base=process.env.FLYCODE_UI_URL, output=process.env.FLYCODE_SCREENSHOTS;
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  let page;
  try {
    page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.setDefaultTimeout(12000);
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);
    await page.getByRole('button',{name:'Создать проект',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'classic-before.png')});
    await page.getByRole('link',{name:'Проверки',exact:true}).click();
    await page.getByRole('heading',{name:'Проверки',exact:true}).waitFor();
    await page.getByText('Планов проверок пока нет',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'verification-empty.png')});
    await page.getByRole('button',{name:'Новый план',exact:true}).click();
    await page.getByLabel('Набор проверок').selectOption({label:'Проверка проекта'});
    let loseReply=true;
    await page.route('**/api/verification/plans',async route=>{
      if(route.request().method()==='POST'&&loseReply){loseReply=false;const response=await route.fetch();assert.equal(response.status(),201);await route.abort();}
      else await route.continue();
    });
    await page.getByRole('button',{name:'Сохранить план',exact:true}).click();
    await page.locator('#planError').filter({hasText:/./}).waitFor();
    await page.getByRole('button',{name:'Сохранить план',exact:true}).click();
    await page.getByRole('heading',{name:'Проверка проекта',exact:true}).waitFor();
    const token=(await (await page.request.get(base+'/api/bootstrap')).json()).token;
    const get=async route=>{
      const response=await page.request.get(base+route,{headers:{'X-FlyCode-Token':token}});
      assert.ok(response.ok(),await response.text());return response.json();
    };
    let plans=await get('/api/verification/plans');assert.equal(plans.items.length,1);
    const id=plans.items[0].id;
    await page.getByRole('button',{name:'Закрыть проверку',exact:true}).click();
    await page.locator('.verification-card').first().focus();
    await page.waitForTimeout(1900);
    assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('verification-card')),true,'Read-only refresh must preserve keyboard focus');
    await page.keyboard.press('Enter');
    await page.getByRole('heading',{name:'Проверка проекта',exact:true}).waitFor();
    await page.locator('#verificationDetail').getByRole('button',{name:'Подключить выполнение',exact:true}).click();
    await page.getByText('Служба проверок подключена',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Запустить проверку',exact:true}).click();
    await page.locator('#verificationResult').getByText('Проверка пройдена',{exact:true}).waitFor();
    await page.getByText('1 / 1 обязательных проверок завершено',{exact:true}).waitFor();
    assert.match(await page.locator('#verificationDetail').innerText(),/Расход: 1/);
    const passed=await get('/api/verification/plans/'+id);
    assert.equal(passed.current,true);assert.equal(passed.verdict.status,'passed');
    let arrive,release,done;
    const arrived=new Promise(resolve=>arrive=resolve),gate=new Promise(resolve=>release=resolve),finished=new Promise(resolve=>done=resolve);
    const stdout=passed.artifacts.find(item=>item.name.endsWith('stdout.txt'));
    const delayed=`**/api/verification/plans/${id}/artifacts/${stdout.id}`;
    await page.route(delayed,async route=>{const response=await route.fetch();arrive();await gate;await route.fulfill({response});done();});
    await page.getByRole('button',{name:'Открыть stdout.txt · попытка 1',exact:true}).click();
    await arrived;
    await page.getByRole('button',{name:'Открыть cases.jsonl · попытка 1',exact:true}).click();
    await page.locator('#reportPreview').filter({hasText:'"kind"'}).waitFor();
    const recent=await page.locator('#reportPreview').innerText();
    await page.getByRole('button',{name:'Закрыть проверку',exact:true}).focus();
    release();await finished;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#reportPreview').innerText(),recent,'Delayed old report must not replace the latest preview');
    assert.equal(await page.getByRole('button',{name:'Закрыть проверку',exact:true}).evaluate(element=>element===document.activeElement),true);
    await page.unroute(delayed);
    const artifact=passed.artifacts.find(item=>item.name.endsWith('junit.xml'));
    assert.ok(artifact);
    const downloadEvent=page.waitForEvent('download',{timeout:15000});
    await page.getByRole('button',{name:'Скачать junit.xml · попытка 1',exact:true}).click();
    const download=await downloadEvent;assert.equal(download.suggestedFilename(),'junit.xml');
    const response=await page.request.get(`${base}/api/verification/plans/${id}/artifacts/${artifact.id}`,{headers:{'X-FlyCode-Token':token}});
    assert.equal(response.status(),200);assert.match(response.headers()['content-disposition'],/attachment/);
    const original=fs.readFileSync(process.env.FLYCODE_SOURCE_FILE);
    fs.appendFileSync(process.env.FLYCODE_SOURCE_FILE,'\n# Owner edit after pass\n');
    await page.locator('#verificationResult').getByText('Нужна повторная проверка',{exact:true}).waitFor();
    fs.writeFileSync(process.env.FLYCODE_SOURCE_FILE,original);
    await page.locator('#verificationResult').getByText('Проверка пройдена',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'verification-passed.png')});
    fs.writeFileSync(process.env.FLYCODE_MODE_FILE,'slow');
    fs.rmSync(process.env.FLYCODE_CASE_MARKER,{force:true});
    await page.getByRole('button',{name:'Повторить проверку',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#verificationDetail').textContent.includes('Выполняется'));
    for(let index=0;index<40&&!fs.existsSync(process.env.FLYCODE_CASE_MARKER);index++)await new Promise(resolve=>setTimeout(resolve,100));
    assert.ok(fs.existsSync(process.env.FLYCODE_CASE_MARKER),'Actual sleeping case must start before Stop');
    await page.getByRole('button',{name:'Остановить проверку',exact:true}).click();
    await page.locator('#verificationResult').getByText('Проверка отменена',{exact:true}).waitFor();
    fs.writeFileSync(process.env.FLYCODE_MODE_FILE,'fail');
    await page.getByRole('button',{name:'Повторить проверку',exact:true}).click();
    await page.locator('#verificationResult').getByText('Проверка не пройдена',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Открыть stdout.txt · попытка 3',exact:true}).click();
    await page.getByText('Requested failure is retained',{exact:false}).first().waitFor();
    const failed=await get('/api/verification/plans/'+id);
    assert.equal(failed.attempts.length,3);assert.equal(failed.verdict.status,'failed');
    assert.equal(failed.spent,3);assert.equal(await page.locator('#verificationDetail img').count(),0);
    await page.screenshot({path:path.join(output,'verification-failed.png')});
    for(const width of [390,320]) {
      await page.setViewportSize({width,height:844});
      await page.locator('#verificationResult').getByText('Проверка не пройдена',{exact:true}).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(output,`verification-${width}.png`)});
      await page.getByRole('button',{name:'Закрыть проверку',exact:true}).click();
      await page.getByRole('button',{name:'Новый план',exact:true}).click();
      await page.getByLabel('Набор проверок').focus();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('dialog[open]').count(),0);
      await page.getByRole('button',{name:/Проверка проекта/}).click();
    }
    await page.getByRole('button',{name:'Закрыть проверку',exact:true}).click();
    // The mobile Stop must be reachable while an actual pytest case is sleeping.
    await page.getByRole('button',{name:'Новый план',exact:true}).click();
    await page.getByRole('button',{name:'Сохранить план',exact:true}).click();
    await page.getByRole('heading',{name:'Проверка проекта',exact:true}).waitFor();
    fs.writeFileSync(process.env.FLYCODE_MODE_FILE,'slow');
    for(const width of [390,320]) {
      await page.setViewportSize({width,height:844});fs.rmSync(process.env.FLYCODE_CASE_MARKER,{force:true});
      await page.getByRole('button',{name:width===390?'Запустить проверку':'Повторить проверку',exact:true}).click();
      for(let index=0;index<60&&!fs.existsSync(process.env.FLYCODE_CASE_MARKER);index++)await new Promise(resolve=>setTimeout(resolve,100));
      assert.ok(fs.existsSync(process.env.FLYCODE_CASE_MARKER));
      const stop=page.getByRole('button',{name:'Остановить проверку',exact:true});
      await stop.waitFor();const box=await stop.boundingBox();assert.ok(box.y>=0&&box.y+box.height<=844);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(output,`verification-stop-${width}.png`)});
      const before=Date.now();const reply=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().postDataJSON()?.operation==='cancel');await stop.click();
      const acknowledgement=await reply;
      if(acknowledgement.status()===409){
        const detail=(await acknowledgement.json()).detail;assert.equal(detail.code,'revision');
        await page.locator('#verificationError').filter({hasText:'Данные изменились'}).waitFor();
        await page.waitForFunction(revision=>document.querySelector('#verificationResult').textContent.includes(`версия плана: ${revision}`),detail.revision);
        await page.getByRole('button',{name:'Остановить проверку',exact:true}).click();
      } else assert.ok(acknowledgement.ok());
      await page.locator('#verificationResult').getByText('Проверка отменена',{exact:true}).waitFor();
      assert.ok(Date.now()-before<5000,'Stop must settle promptly');
    }
    // Losing the folder must retain only a keyboard-accessible cancel handle.
    fs.rmSync(process.env.FLYCODE_CASE_MARKER,{force:true});
    await page.getByRole('button',{name:'Повторить проверку',exact:true}).click();
    for(let index=0;index<60&&!fs.existsSync(process.env.FLYCODE_CASE_MARKER);index++)await new Promise(resolve=>setTimeout(resolve,100));
    assert.ok(fs.existsSync(process.env.FLYCODE_CASE_MARKER));
    const source=path.dirname(process.env.FLYCODE_SOURCE_FILE),displaced=source+'-displaced';
    fs.renameSync(source,displaced);fs.mkdirSync(source);fs.writeFileSync(path.join(source,'replacement.txt'),'Keep replacement bytes');
    try {
      const historical=page.getByRole('button',{name:'Остановить недоступную проверку',exact:true});
      await historical.waitFor();
      await page.locator('#notice').filter({hasText:'Папка проекта изменилась или недоступна.'}).waitFor();
      await historical.focus();await page.waitForTimeout(1900);
      assert.equal(await historical.evaluate(element=>element===document.activeElement),true,'Historical Stop must retain keyboard focus across refresh');
      await page.screenshot({path:path.join(output,'verification-historical-stop-320.png')});
      await page.keyboard.press('Enter');await historical.waitFor({state:'hidden'});
      assert.equal(fs.readFileSync(path.join(source,'replacement.txt'),'utf8'),'Keep replacement bytes');
    } finally {
      fs.unlinkSync(path.join(source,'replacement.txt'));fs.rmdirSync(source);fs.renameSync(displaced,source);
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>localStorage.setItem('flycode.desktop.theme','dark'));
    await page.goto(base+'/desktop');
    await page.getByRole('link',{name:'Проверки',exact:true}).click();
    assert.match(page.url(),/interface=desktop/);
    await page.locator('.verification-card').first().click();
    await page.locator('#verificationResult').getByText('Проверка не пройдена',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'verification-desktop.png')});
    assert.deepEqual(errors,[]);
    console.log('Real verification browser acceptance passed');
  } catch(error) {if(page)await page.screenshot({path:path.join(output,'failure.png')});throw error;}
  finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
