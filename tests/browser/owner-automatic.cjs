// Actual coordinator drives rejection, owner replan and verified completion.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
function phase(value){
 const fd=Number(process.env.FLYCODE_PHASE_FD),data=Buffer.from(JSON.stringify({phase:value})),header=Buffer.alloc(4);header.writeUInt32BE(data.length);fs.writeSync(fd,Buffer.concat([header,data]));
 const read=length=>{const result=Buffer.alloc(length);let offset=0;while(offset<length){const count=fs.readSync(fd,result,offset,length-offset);assert.ok(count);offset+=count;}return result;};
 const length=read(4).readUInt32BE();assert.ok(length<=8192);return JSON.parse(read(length).toString());
}
(async()=>{
 const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_OWNER_TASK,childId=process.env.FLYCODE_CHILD_TASK,out=process.env.FLYCODE_SCREENSHOTS;
 assert.ok(base&&id&&childId&&out);fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:"chrome",headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.setDefaultTimeout(8000);
 page.on("pageerror",error=>errors.push(error.message));
 try{
  await page.goto(base+"/tasks");
  const token=(await(await page.request.get(base+"/api/bootstrap")).json()).token,headers={"X-FlyCode-Token":token};
  const get=async route=>{const response=await page.request.get(base+route,{headers});assert.ok(response.ok(),await response.text());return response.json();};
  const card=await get("/api/tasks/"+id);
  await page.getByRole("button",{name:"Подключить выполнение",exact:true}).focus();await page.keyboard.press("Enter");
  await page.getByText("Служба задач подключена",{exact:true}).waitFor();assert.equal(phase("attached").attached,true);
  await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
  const open=async()=>{await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();};
  await open();
  const preview=(await get(`/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`)).result;
  for(const task of preview.tasks.filter(task=>task.kind!=="review"&&task.profile_ids.length))await page.locator(`[data-owner-prepare="${task.id}"]`).check();
  await page.getByLabel("Контроллер подготовки",{exact:true}).selectOption("heuristic_fsm");
  await page.getByRole("button",{name:"Подготовить папку",exact:true}).click();
  await page.getByText("Папка подготовлена. Выполнение ещё не разрешено.",{exact:true}).waitFor();
  await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
  await page.getByLabel("Разрешаю выполнение по этому плану",{exact:true}).check();
  await page.getByRole("button",{name:"Разрешить выполнение",exact:true}).focus();await page.keyboard.press("Enter");
  await page.getByText("План разрешён. Выполнение продолжит служба задач.",{exact:true}).waitFor();
  assert.equal(phase("rejected").stopped,true);
  await page.getByRole("button",{name:"Обновить план",exact:true}).click();
  await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
  const failed=await get("/api/tasks/"+id);assert.ok(failed.blockers.includes("child_review_failed"));
  await page.screenshot({path:path.join(out,"rejected-before-replan.png"),fullPage:true});
  await page.getByRole("heading",{name:"Изменение остановленного плана",exact:true}).waitFor({timeout:1500});
  for(const targetId of [id,childId]){
   await page.keyboard.press("Escape");await page.keyboard.press("Escape");
   const target=await get("/api/tasks/"+targetId);
   await page.getByRole("button",{name:new RegExp(target.task.short_id)}).click();
   await page.locator("#detailBody").getByRole("button",{name:"Приостановить",exact:true}).click();
   await page.locator("#detailBody").getByRole("button",{name:"Продолжить",exact:true}).waitFor();
   const paused=await get("/api/tasks/"+targetId);assert.equal(paused.task.paused,true);
   assert.equal(paused.task.current_attempt_id,target.task.current_attempt_id);assert.equal(paused.task.fence,target.task.fence);
   await open();
   assert.equal(await page.getByRole("heading",{name:"Изменение остановленного плана",exact:true}).count(),0,"Any paused branch must hide replan controls");
   await page.screenshot({path:path.join(out,`paused-${targetId===id?"parent":"child"}.png`)});
   await page.keyboard.press("Escape");
   await page.locator("#detailBody").getByRole("button",{name:"Продолжить",exact:true}).click();
   await page.locator("#detailBody").getByRole("button",{name:"Приостановить",exact:true}).waitFor();
   const resumed=await get("/api/tasks/"+targetId);assert.equal(resumed.task.paused,false);
   assert.equal(resumed.task.current_attempt_id,target.task.current_attempt_id);assert.equal(resumed.task.fence,target.task.fence);
   await open();await page.getByRole("heading",{name:"Изменение остановленного плана",exact:true}).waitFor({timeout:1500});
  }
  const child=preview.tasks.find(task=>task.id===childId),caption=child.title+" "+child.id.slice(0,6);
  await page.getByLabel("Изменить задачу "+caption,{exact:true}).check();
  await page.getByLabel("Результат · "+caption,{exact:true}).fill("Repair the parser under the exact owner-approved criteria.");
  await page.getByLabel("Разрешаю изменения плана",{exact:true}).check();
  const apply=page.getByRole("button",{name:"Применить изменения плана",exact:true});
  for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:width===1440?1000:844});await apply.scrollIntoViewIfNeeded();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:path.join(out,`rejected-replan-${width}.png`)});
  }
  await apply.focus();await page.keyboard.press("Enter");
  await page.getByText("Изменения плана приняты. Расходы и срок сохранены.",{exact:true}).waitFor();
  assert.equal(phase("complete").current,true);
  await page.keyboard.press("Escape");
  await page.locator("#detailBody").getByText("Изменения применены",{exact:false}).first().waitFor();
  const completed=await get("/api/tasks/"+id);assert.equal(completed.integration.current,true);assert.equal(completed.completion.current,true);
  for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:width===1440?1000:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator("#detailBody").getByText("Изменения применены",{exact:false}).first().scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(out,`automatic-integrated-${width}.png`)});
  }
  assert.equal(phase("invalidate").changed,true);
  const invalidated=await get("/api/tasks/"+id);assert.equal(invalidated.integration.current,false);assert.equal(invalidated.completion.current,false);
  fs.writeFileSync(path.join(out,"automatic.json"),JSON.stringify({failed,completed,invalidated},null,2));
  assert.deepEqual(errors,[]);console.log("Automatic rejected review → explicit browser replan → fresh review → current integration passed");
 }catch(error){await page.screenshot({path:path.join(out,"failure.png"),fullPage:true});fs.writeFileSync(path.join(out,"failure.html"),await page.content());throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
