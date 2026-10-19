// Native result states come from the same HTTP/CLI/Host workflow.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
(async()=>{
  const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_RESULT_TASK,stage=process.env.FLYCODE_RESULT_STAGE,out=process.env.FLYCODE_SCREENSHOTS;
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:"chrome",headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto(base+"/tasks");
    const bootstrap=await (await page.request.get(base+"/api/bootstrap")).json();
    const headers={"X-FlyCode-Token":bootstrap.token};
    const response=await page.request.get(base+"/api/tasks/"+id,{headers});assert.ok(response.ok());
    const card=await response.json();
    await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
    await page.getByRole("heading",{name:card.task.title,exact:true}).waitFor();
    await page.screenshot({path:path.join(out,"before-assert.png")});
    const expected={pending:"Ожидает другой этап или возобновление ветви",wait:"Ожидает дочерних задач: 1",child:"Задача проверена",integrated:"Изменения применены",stale:"Результат workflow устарел"}[stage];
    await page.locator("#detailBody").getByText(expected,{exact:true}).waitFor({timeout:5000});
    if(stage==="stale")assert.equal(await page.locator("#detailBody").getByText(/^Ожидает дочерних задач:/).count(),0,
      "A historical continuation checkpoint is not a new active wait");
    if(stage==="child")await page.locator("#detailBody").getByText("Интеграция ещё не выполнена",{exact:true}).waitFor();
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:width===1440?1000:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`detail-${width}.png`)});
      await page.keyboard.press("Escape");
      await page.getByRole("button",{name:"Список",exact:true}).click();
      const button=page.getByRole("button",{name:new RegExp(card.task.short_id)});
      await button.getByText(expected,{exact:true}).waitFor();
      await page.screenshot({path:path.join(out,`list-${width}.png`)});
      await button.focus();await page.keyboard.press("Enter");
      await page.locator("#detailBody").getByText(expected,{exact:true}).waitFor();
    }
    if(stage==="integrated") {
      // Controlled render regression for a legitimate completed/current=false
      // projection; the native late-evidence race is checked independently.
      await page.route("**/api/tasks/"+id,async route=>{
        const response=await route.fetch(),projection=await response.json();
        projection.completion=null;projection.integration.current=false;
        projection.blockers=["result_invalidated"];
        await route.fulfill({response,json:projection});
      });
      await page.keyboard.press("Escape");
      await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
      await page.locator("#detailBody").getByText("Результат workflow устарел",{exact:true}).first().waitFor();
      await page.screenshot({path:path.join(out,"controlled-completed-not-current-320.png")});
    }
    if(stage==="pending")assert.equal(await page.locator("#detailBody").getByRole("button",{name:"Поставить в очередь",exact:true}).count(),0,
      "The approved pipeline owns pending child scheduling");
    if(stage==="wait") {
      await page.keyboard.press("Escape");
      const child=await (await page.request.get(base+"/api/tasks/"+card.wait.children[0],{headers})).json();
      await page.getByRole("button",{name:new RegExp(child.task.short_id)}).click();
      await page.getByRole("heading",{name:child.task.title,exact:true}).waitFor();
      assert.equal(await page.locator("#detailBody").getByRole("button",{name:"Поставить в очередь",exact:true}).count(),0,
        "The approved pipeline owns pending child scheduling");
      await page.screenshot({path:path.join(out,"pending-child-320.png")});
    }
    if(stage==="stale") {
      const start=Date.now();
      await page.locator("#detailBody").getByRole("button",{name:"Приостановить",exact:true}).click();
      await page.locator("#detailBody").getByRole("button",{name:"Продолжить",exact:true}).waitFor({timeout:3000});
      const paused=await (await page.request.get(base+"/api/tasks/"+id,{headers})).json();
      assert.equal(paused.task.paused,true);assert.ok(Date.now()-start<3000);
      await page.screenshot({path:path.join(out,"stop-320.png")});
    }
    assert.deepEqual(errors,[]);console.log("Native task result browser stage passed: "+stage);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
