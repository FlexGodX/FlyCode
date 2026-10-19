// Explicit owner task routes, exact replay and protected native Host transport.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
(async()=>{
  const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_OWNER_TASK,sender=process.env.FLYCODE_MESSAGE_SENDER,out=process.env.FLYCODE_SCREENSHOTS;
  assert.ok(base&&id&&sender&&out);fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:"chrome",headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.setDefaultTimeout(5000);
    page.on("pageerror",error=>errors.push(error.message));await page.goto(base+"/tasks");
    const bootstrap=await (await page.request.get(base+"/api/bootstrap")).json(),headers={"X-FlyCode-Token":bootstrap.token};
    const card=await (await page.request.get(base+"/api/tasks/"+id,{headers})).json();
    const open=async()=>{
      await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
      await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();
      try{await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();}
      catch(error){await page.screenshot({path:path.join(out,"failed-owner-load.png")});fs.writeFileSync(path.join(out,"failed-owner-load.json"),JSON.stringify({text:await page.locator("#ownerDialog").innerText(),errors},null,2));throw error;}
    };
    await open();await page.screenshot({path:path.join(out,"before-route-form.png")});
    await page.getByRole("heading",{name:"Связи между задачами",exact:true}).waitFor({timeout:1500});
    const save=()=>page.getByRole("button",{name:"Сохранить связи",exact:true});
    assert.equal(await save().isEnabled(),false);
    await page.getByLabel("Отправитель сообщения",{exact:true}).selectOption(sender);
    await page.getByLabel("Получатель сообщения",{exact:true}).selectOption(id);
    await page.getByRole("button",{name:"Добавить связь",exact:true}).click();
    assert.equal(await page.locator("[data-message-route]").count(),1);
    assert.equal(await save().isEnabled(),false,"Each new route set requires explicit confirmation");
    await page.getByLabel("Разрешаю указанные связи между задачами",{exact:true}).check();
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:width===1440?1000:844});await save().scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`route-confirm-${width}.png`)});
    }
    let lostBody,lostReceipt,arrive;const arrived=new Promise(resolve=>{arrive=resolve;});
    await page.route("**/api/tasks/owner",async route=>{
      if(route.request().postDataJSON().operation!=="message_routes")return route.continue();
      lostBody=route.request().postDataJSON();const response=await route.fetch();assert.ok(response.ok(),await response.text());
      lostReceipt=await response.json();await route.abort("failed");arrive();
    });
    await save().focus();await page.keyboard.press("Enter");await arrived;
    await page.getByRole("button",{name:"Повторить сохранённый запрос",exact:true}).waitFor();
    await page.unroute("**/api/tasks/owner");await page.reload();await open();
    let replay;page.on("request",request=>{if(new URL(request.url()).pathname==="/api/tasks/owner"&&request.method()==="POST")replay=request.postDataJSON();});
    await page.getByRole("button",{name:"Повторить сохранённый запрос",exact:true}).click();
    await page.getByText("Связи между задачами сохранены.",{exact:true}).waitFor();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    assert.deepEqual(replay,lostBody);assert.equal(await page.locator("[data-message-route]").count(),1);
    await page.getByRole("button",{name:"Отозвать все связи",exact:true}).click();
    assert.equal(await page.locator("[data-message-route]").count(),0);
    assert.equal(await save().isEnabled(),false);
    await page.getByLabel("Разрешаю указанные связи между задачами",{exact:true}).check();
    await save().click();await page.getByText("Связи между задачами отозваны.",{exact:true}).waitFor();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    await page.getByRole("heading",{name:"Связи между задачами",exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,"routes-revoked-320.png")});
    await page.keyboard.press("Escape");assert.equal(await page.locator("#ownerDialog").isVisible(),false);
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,"route-replay.json"),JSON.stringify({lostBody,lostReceipt,replay},null,2));
    console.log(JSON.stringify({passed:true,widths:[1440,390,320],exact_replay:true,revoked:true}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
