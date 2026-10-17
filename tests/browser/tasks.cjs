// Real Chromium + canonical HTTP store, without model or tool execution.
const {chromium} = require(process.env.FLYCODE_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const base = process.env.FLYCODE_UI_URL;
  const output = process.env.FLYCODE_SCREENSHOTS;
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({channel:"chrome", headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on("pageerror", error=>{errors.push(error.message);console.error("PAGE ERROR",error.message);});
    page.on("console", message=>{if(message.type()==="error")console.error("BROWSER",message.text());});
    page.on("response", response=>{if(response.status()>=400)console.error("HTTP",response.status(),new URL(response.url()).pathname);});
    await page.goto(base);
    await page.waitForFunction(()=>!document.querySelector('#projectSelect').disabled);
    await page.screenshot({path:path.join(output,'classic-before.png')});
    await page.getByRole('link',{name:'Задачи',exact:true}).click();
    await page.getByRole('heading',{name:'Задачи',exact:true}).waitFor();
    await page.getByText('Задач пока нет',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'tasks-empty.png')});
    await page.getByRole('button',{name:'Новая задача',exact:true}).click();
    await page.getByLabel('Заголовок').fill('Fix arithmetic overflow');
    await page.getByLabel('Описание результата').fill('Preserve all input bytes.\nAdd bounded arithmetic.');
    await page.getByLabel('Приоритет задачи').selectOption('3');
    let loseCreatedReply=true;
    await page.route('**/api/tasks',async route=>{
      if(route.request().method()==='POST'&&loseCreatedReply){
        loseCreatedReply=false;const response=await route.fetch();assert.equal(response.status(),201);await route.abort();
      } else await route.continue();
    });
    await page.getByRole('button',{name:'Сохранить черновик',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#draftError').textContent.length>0);
    await page.getByRole('button',{name:'Сохранить черновик',exact:true}).click();
    await page.getByRole('heading',{name:'Fix arithmetic overflow',exact:true}).waitFor();
    const bootstrap=await (await page.request.get(base+'/api/bootstrap')).json();
    const headers={'X-FlyCode-Token':bootstrap.token};
    const request=async(route,body)=>{
      const response=body===undefined?await page.request.get(base+route,{headers}):await page.request.post(base+route,{headers,data:body});
      assert.ok(response.ok(),await response.text());return response.json();
    };
    const cards=await request('/api/tasks');
    assert.equal(cards.items.length,1);
    const task=cards.items[0].task;
    assert.equal(task.state,'planned');assert.equal(task.priority,3);
    assert.equal(cards.items[0].attempt,null);
    assert.match(await page.locator('#taskDetail').innerText(),/Нет критериев/);
    assert.deepEqual(await request('/api/agents/activity'),[]);
    assert.deepEqual(await request('/api/runs'),[]);
    await page.getByLabel('Комментарий').fill('<img src=x onerror=alert(1)> Literal feedback');
    let loseCommentReply=true;
    await page.route('**/api/tasks/*/comments',async route=>{
      if(loseCommentReply){loseCommentReply=false;const response=await route.fetch();assert.equal(response.status(),201);await route.abort();}
      else await route.continue();
    });
    await page.getByRole('button',{name:'Добавить комментарий'}).click();
    await page.waitForFunction(()=>document.querySelector('#detailError').textContent.length>0);
    await page.getByRole('button',{name:'Добавить комментарий'}).click();
    await page.getByText('<img src=x onerror=alert(1)> Literal feedback',{exact:true}).waitFor();
    assert.equal(await page.locator('#taskDetail img').count(),0);
    await page.waitForFunction(()=>document.querySelector('#commentText').value==='');
    assert.equal(await page.locator('#detailError').innerText(),'','Accepted retry must clear its previous transport error');
    await request('/api/tasks/'+task.id+'/comments',{text:'External feedback',idempotency_key:'external-comment'});
    await page.getByText('External feedback',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'task-detail.png')});
    await page.reload();
    await page.getByRole('button',{name:new RegExp(task.short_id)}).click();
    await page.getByText('External feedback',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    for(let index=0;index<55;index++)await request('/api/tasks/'+task.id+'/comments',{text:`Feedback ${index}`,idempotency_key:`page-${index}`});
    await page.getByRole('button',{name:new RegExp(task.short_id)}).click();
    await page.getByRole('button',{name:/события/}).click();
    await page.getByText('Feedback 54',{exact:true}).waitFor();
    await request('/api/tasks/'+task.id+'/comments',{text:'Feedback after pagination',idempotency_key:'after-pagination'});
    await page.getByText('Feedback after pagination',{exact:true}).waitFor();
    await page.getByText('Feedback 54',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    await page.getByRole('button',{name:'Список',exact:true}).click();
    await page.getByLabel('Поиск задач').fill('arithmetic');
    assert.equal(await page.locator('.task-card').count(),1);
    await page.getByLabel('Фильтр состояния').selectOption('succeeded');
    await page.getByText('Ничего не найдено',{exact:true}).waitFor();
    await page.getByLabel('Фильтр состояния').selectOption('');
    await page.getByRole('button',{name:new RegExp(task.short_id)}).click();
    let collide=true;
    await page.route('**/api/tasks/*/commands',async route=>{
      if(collide){
        collide=false;const body=route.request().postDataJSON();
        await request('/api/tasks/'+task.id+'/commands',{operation:'set_priority',expected_revision:body.expected_revision,idempotency_key:'external-priority',changes:{priority:1}});
      }
      await route.continue();
    });
    await page.getByRole('button',{name:'Отменить задачу',exact:true}).click();
    await page.getByText(/Конфликт версии/).waitFor();
    assert.equal((await request('/api/tasks/'+task.id)).task.state,'planned');
    await page.getByRole('button',{name:'Отменить задачу',exact:true}).click();
    await page.locator('#taskDetail dd').getByText('Отменена',{exact:true}).waitFor();
    assert.equal((await request('/api/tasks/'+task.id)).task.state,'cancelled');
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    await page.screenshot({path:path.join(output,'tasks-list.png')});
    await page.getByRole('button',{name:'Агенты',exact:true}).click();
    await page.getByText('Экземпляров агентов пока нет',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'agents-empty.png')});
    await page.getByRole('button',{name:'Доска',exact:true}).click();
    for(const width of [390,320]){
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(output,`tasks-${width}.png`)});
      await page.getByRole('button',{name:new RegExp(task.short_id)}).click();
      await page.getByRole('heading',{name:'Fix arithmetic overflow',exact:true}).waitFor();
      await page.locator('#taskDetail dd').getByText('Отменена',{exact:true}).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(output,`task-detail-${width}.png`)});
      await page.keyboard.press('Escape');
      await page.getByRole('button',{name:'Новая задача',exact:true}).click();
      await page.getByLabel('Заголовок').fill('Keyboard draft');
      await page.keyboard.press('Escape');
      assert.equal((await request('/api/tasks')).items.length,1);
    }
    // Hold an actual old HTTP response across closing/reopening the same task.
    await page.route('**/api/tasks/events*',route=>route.abort());
    await page.goto(base+'/tasks');
    const race=await request('/api/tasks',{project_id:task.project_id,task:{title:'Race draft'},idempotency_key:'race-draft'});
    let held,release,oldDone;
    const arrived=new Promise(resolve=>held=resolve),gate=new Promise(resolve=>release=resolve),done=new Promise(resolve=>oldDone=resolve);
    let hold=true;
    await page.route('**/api/tasks/'+race.task.id,async route=>{
      if(hold){hold=false;const response=await route.fetch();held();await gate;await route.fulfill({response});oldDone();}
      else await route.continue();
    });
    await page.reload();
    await page.getByRole('button',{name:new RegExp(race.task.short_id)}).click();
    await arrived;
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    await request('/api/tasks/'+race.task.id+'/commands',{operation:'set_priority',expected_revision:1,idempotency_key:'race-priority',changes:{priority:3}});
    await page.getByRole('button',{name:new RegExp(race.task.short_id)}).click();
    await page.getByRole('heading',{name:'Race draft',exact:true}).waitFor();
    assert.equal(await page.locator('#detailBody dd').nth(3).innerText(),'3');
    release();await done;
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#detailBody dd').nth(3).innerText(),'3','Delayed old response must not regress priority');
    assert.equal(await page.locator('#detailBody dd').nth(4).innerText(),'2','Delayed old response must not regress revision');
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    // Controlled valid API projection: blocked tasks need prerequisites, not retry.
    await page.route('**/api/tasks/'+race.task.id,async route=>{
      const response=await route.fetch(),card=await response.json();
      card.task.state='blocked';card.column='blocked';card.blockers=['dependency'];
      await route.fulfill({response,json:card});
    });
    await page.getByRole('button',{name:new RegExp(race.task.short_id)}).click();
    await page.locator('#taskDetail dd').getByText('Заблокирована',{exact:true}).waitFor();
    assert.equal(await page.locator('#detailBody').getByRole('button',{name:'Повторить',exact:true}).count(),0,'Blocked tasks cannot retry until their prerequisite changes');
    await page.getByRole('button',{name:'Закрыть карточку'}).click();
    await page.getByRole('link',{name:'Чаты',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#projectSelect').disabled);
    assert.equal(new URL(page.url()).pathname,'/');
    await page.setViewportSize({width:1440,height:1000});
    await page.getByRole('link',{name:'Desktop-интерфейс',exact:true}).click();
    await page.getByRole('link',{name:'Классический интерфейс',exact:true}).waitFor();
    await page.getByRole('link',{name:'Задачи',exact:true}).click();
    assert.equal(new URL(page.url()).pathname,'/tasks');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'task-identity.json'),JSON.stringify({task_id:task.id,project_id:task.project_id,short_id:task.short_id}));
    console.log('Canonical Tasks browser acceptance passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
