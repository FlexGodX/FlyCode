// Owner controls admit work; only the detached coordinator dispatches workers.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
function phase(value){
  const fd=Number(process.env.FLYCODE_PHASE_FD),data=Buffer.from(JSON.stringify({phase:value})),header=Buffer.alloc(4);header.writeUInt32BE(data.length);fs.writeSync(fd,Buffer.concat([header,data]));
  const read=length=>{const result=Buffer.alloc(length);let offset=0;while(offset<length){const count=fs.readSync(fd,result,offset,length-offset);assert.ok(count);offset+=count;}return result;};
  const length=read(4).readUInt32BE();assert.ok(length<=8192);return JSON.parse(read(length).toString());
}
(async()=>{
  const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_OWNER_TASK,a=process.env.FLYCODE_CHILD_TASK,b=process.env.FLYCODE_SECOND_TASK,out=process.env.FLYCODE_SCREENSHOTS;
  assert.ok(base&&id&&a&&b&&out);fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:"chrome",headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.setDefaultTimeout(10000);
  page.on("pageerror",error=>errors.push(error.message));
  try{
    await page.goto(base+"/tasks");
    const token=(await(await page.request.get(base+"/api/bootstrap")).json()).token,headers={"X-FlyCode-Token":token};
    const get=async url=>{const response=await page.request.get(base+url,{headers});assert.ok(response.ok(),await response.text());return response.json();};
    const card=await get("/api/tasks/"+id);
    await page.getByRole("button",{name:"Подключить выполнение",exact:true}).click();
    await page.getByText("Служба задач подключена",{exact:true}).waitFor();assert.equal(phase("attached").attached,true);
    await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
    await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    const preview=(await get(`/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`)).result;
    const writers=preview.tasks.filter(task=>task.kind!=="review"&&task.profile_ids.length);assert.equal(writers.length,3);
    for(const task of writers)await page.locator(`[data-owner-prepare="${task.id}"]`).check();
    await page.getByLabel("Контроллер подготовки",{exact:true}).selectOption("heuristic_fsm");
    await page.getByRole("button",{name:"Подготовить папку",exact:true}).click();
    await page.getByText("Папка подготовлена. Выполнение ещё не разрешено.",{exact:true}).waitFor();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    await page.getByLabel("Разрешаю выполнение по этому плану",{exact:true}).check();
    await page.getByRole("button",{name:"Разрешить выполнение",exact:true}).click();
    await page.getByText("План разрешён. Выполнение продолжит служба задач.",{exact:true}).waitFor();
    assert.equal(phase("workers-waiting").waiting,true);
    await page.getByRole("button",{name:"Обновить план",exact:true}).click();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    for(const [sender,recipient] of [[a,b],[b,a]]){
      await page.getByLabel("Отправитель сообщения",{exact:true}).selectOption(sender);
      await page.getByLabel("Получатель сообщения",{exact:true}).selectOption(recipient);
      await page.getByRole("button",{name:"Добавить связь",exact:true}).click();
    }
    await page.getByLabel("Разрешаю указанные связи между задачами",{exact:true}).check();
    await page.getByRole("button",{name:"Сохранить связи",exact:true}).click();
    await page.getByText("Связи между задачами сохранены.",{exact:true}).waitFor();
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,"automatic-routes-granted.png")});
    const result=phase("exchange");assert.equal(result.current,true);
    await page.keyboard.press("Escape");
    await page.locator("#detailBody").getByText("Изменения применены",{exact:false}).first().waitFor();
    const completed=await get("/api/tasks/"+id);assert.equal(completed.integration.current,true);assert.equal(completed.completion.current,true);
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:width===1440?1000:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`automatic-message-completion-${width}.png`)});
    }
    await page.goto("about:blank");assert.equal(phase("disconnected").idle,true);
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,"automatic-messages.json"),JSON.stringify({passed:true,...result},null,2));
  }catch(error){await page.screenshot({path:path.join(out,"automatic-message-failure.png")});throw error;}
  finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
