// Native owner workflow controls; execution uses the actual elected Host.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
function phase(value){
  const fd=Number(process.env.FLYCODE_PHASE_FD),data=Buffer.from(JSON.stringify({phase:value})),header=Buffer.alloc(4);header.writeUInt32BE(data.length);fs.writeSync(fd,Buffer.concat([header,data]));
  const read=length=>{const result=Buffer.alloc(length);let offset=0;while(offset<length){const count=fs.readSync(fd,result,offset,length-offset);assert.ok(count);offset+=count;}return result;};
  const length=read(4).readUInt32BE();assert.ok(length<=8192);return JSON.parse(read(length).toString());
}
(async()=>{
  const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_OWNER_TASK,out=process.env.FLYCODE_SCREENSHOTS;
  assert.ok(base&&id&&out,"Owner fixture identity is required");
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:"chrome",headless:true});
  let page;
  try {
    page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.setDefaultTimeout(8000);
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto(base+"/tasks");
    const bootstrap=await (await page.request.get(base+"/api/bootstrap")).json(),headers={"X-FlyCode-Token":bootstrap.token};
    const response=await page.request.get(base+"/api/tasks/"+id,{headers});assert.ok(response.ok());
    const card=await response.json();assert.equal(card.task.id,id);
    await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();
    await page.getByRole("heading",{name:card.task.title,exact:true}).waitFor();
    await page.screenshot({path:path.join(out,"before-owner-controls.png")});
    const open=page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true});
    await open.waitFor({timeout:1500});
    let heldPreview,arrive;const arrived=new Promise(resolve=>{arrive=resolve;});
    await page.route("**/api/tasks/owner/*?*",route=>{heldPreview=route;arrive();});
    await open.focus();await page.keyboard.press("Enter");await arrived;
    await page.getByRole("dialog",{name:"План выполнения",exact:true}).waitFor();
    await page.locator("#ownerStops summary").click({timeout:1500});
    assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="pause"]`).isEnabled(),true,"Stop must be available before preview loads");
    await heldPreview.continue();await page.unroute("**/api/tasks/owner/*?*");
    await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:width===1440?1000:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`owner-preview-${width}.png`)});
    }
    await page.keyboard.press("Escape");assert.equal(await page.locator("#ownerDialog").isVisible(),false);
    assert.equal(await page.locator("#taskDetail").isVisible(),true);
    if(["commands","lifecycle"].includes(process.env.FLYCODE_OWNER_CASE)){
      const openPlan=async()=>{
        await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();
        await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
      };
      const preview=(await (await page.request.get(`${base}/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`,{headers})).json()).result;
      const writers=preview.tasks.filter(task=>task.kind!=="review"&&task.profile_ids.length);
      assert.equal(writers.length,2);
      const choosePreparation=async()=>{
        for(const task of writers)await page.locator(`[data-owner-prepare="${task.id}"]`).check();
        await page.getByLabel("Контроллер подготовки",{exact:true}).selectOption("heuristic_fsm");
      };
      await openPlan();
      const prepare=page.getByRole("button",{name:"Подготовить папку",exact:true});
      await prepare.waitFor({timeout:1500});await choosePreparation();
      let held,arrive;const arrived=new Promise(resolve=>{arrive=resolve;});
      await page.route("**/api/tasks/owner",route=>{
        if(route.request().postDataJSON().operation==="prepare"){held=route;arrive();}else route.continue();
      });
      await prepare.click();await arrived;
      await page.locator("#ownerStops summary").click();
      const stop=page.locator(`[data-owner-stop="${id}"][data-operation="pause"]`);
      assert.equal(await stop.isEnabled(),true,"Owner preparation must not disable Stop");
      let stopHeld,stopReply,stopArrive;const stopArrived=new Promise(resolve=>{stopArrive=resolve;});
      await page.route(`**/api/tasks/${id}/stop`,async route=>{stopHeld=route;stopReply=await route.fetch();assert.ok(stopReply.ok(),await stopReply.text());stopArrive();});
      const stopStart=Date.now();await stop.click();await stopArrived;
      assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="cancel"]`).isEnabled(),false,"Both actions must share the pending Stop state");
      await page.keyboard.press("Escape");await openPlan();await page.locator("#ownerStops summary").click();
      assert.equal(await stop.isEnabled(),false,"Reopening must retain the shared pending Stop");
      assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="cancel"]`).isEnabled(),false);
      await stopHeld.fulfill({response:stopReply});await page.unroute(`**/api/tasks/${id}/stop`);
      await page.locator("#ownerStops").getByText("Команда принята. Остановка подтверждается отдельно.",{exact:true}).waitFor({timeout:3000});
      assert.ok(Date.now()-stopStart<3000);
      assert.equal(await stop.isEnabled(),true);assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="cancel"]`).isEnabled(),true);
      await page.screenshot({path:path.join(out,"stop-during-prepare-320.png")});
      await held.continue();
      await page.waitForFunction(()=>!Object.keys(sessionStorage).some(key=>key.startsWith("flycode-owner-v1:")));
      assert.equal(await page.locator("#ownerError").isVisible(),false,"A stale response must not overwrite the reopened dialog");
      assert.equal(await page.getByLabel("Контроллер подготовки",{exact:true}).isEnabled(),true,"Settling the previous request must unlock the same workflow");
      await page.unroute("**/api/tasks/owner");
      await page.keyboard.press("Escape");
      await page.locator("#detailBody").getByRole("button",{name:"Продолжить",exact:true}).click();
      await page.locator("#detailBody").getByRole("button",{name:"Приостановить",exact:true}).waitFor();
      await openPlan();await choosePreparation();
      let lostBody,lostReceipt,lost;
      const lostResponse=new Promise(resolve=>{lost=resolve;});
      await page.route("**/api/tasks/owner",async route=>{
        if(route.request().postDataJSON().operation!=="prepare")return route.continue();
        lostBody=route.request().postDataJSON();const response=await route.fetch();
        assert.ok(response.ok(),await response.text());lostReceipt=await response.json();
        await route.abort("failed");lost();
      });
      await page.getByRole("button",{name:"Подготовить папку",exact:true}).click();await lostResponse;
      await page.getByRole("button",{name:"Повторить сохранённый запрос",exact:true}).waitFor();
      await page.unroute("**/api/tasks/owner");
      await page.reload();
      await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();await openPlan();
      let replayBody;
      page.on("request",request=>{if(new URL(request.url()).pathname==="/api/tasks/owner"&&request.method()==="POST"&&request.postDataJSON().operation==="prepare")replayBody=request.postDataJSON();});
      await page.getByRole("button",{name:"Повторить сохранённый запрос",exact:true}).click();
      await page.getByText("Папка подготовлена. Выполнение ещё не разрешено.",{exact:true}).waitFor();
      await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
      assert.deepEqual(replayBody,lostBody,"Reload must replay saved revisions/binding IDs/key, not a new request");
      const currentResponse=await page.request.get(`${base}/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`,{headers});assert.ok(currentResponse.ok(),await currentResponse.text());
      const current=(await currentResponse.json()).result;
      for(const [taskId,bindingId] of Object.entries(lostReceipt.result.binding_ids))assert.equal(current.tasks.find(task=>task.id===taskId).binding_id,bindingId);
      assert.equal(await page.getByRole("button",{name:"Разрешить выполнение",exact:true}).isEnabled(),false);
      await page.getByLabel("Разрешаю выполнение по этому плану",{exact:true}).check();
      for(const width of [1440,390,320]){
        await page.setViewportSize({width,height:width===1440?1000:844});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.getByRole("button",{name:"Разрешить выполнение",exact:true}).scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,`owner-confirm-${width}.png`)});
      }
      await page.getByRole("button",{name:"Разрешить выполнение",exact:true}).focus();await page.keyboard.press("Enter");
      await page.getByText("План разрешён. Выполнение продолжит служба задач.",{exact:true}).waitFor();
      await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
      assert.equal(await page.getByRole("button",{name:"Добавить ветвь",exact:true}).count(),0,"Graph edits require the stopped parent checkpoint");
      const approved=await (await page.request.get(base+"/api/tasks/"+id,{headers})).json();
      assert.ok(approved.lineage.pipeline_id);assert.equal(approved.task.state,"ready");
      fs.writeFileSync(path.join(out,"native-commands.json"),JSON.stringify({lostBody,lostReceipt,replayBody,pipeline_id:approved.lineage.pipeline_id},null,2));
      if(process.env.FLYCODE_OWNER_CASE==="lifecycle"){
        const checkpoint=phase("parent-wait");assert.equal(checkpoint.stopped,true);
        await page.getByRole("button",{name:"Обновить план",exact:true}).click();
        await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
        await page.getByRole("button",{name:"Добавить ветвь",exact:true}).click({timeout:1500});
        const child=writers.find(task=>task.id!==id);
        await page.getByRole("button",{name:"Добавить ветвь",exact:true}).click();
        await page.getByRole("group",{name:"Новая ветвь 1",exact:true}).getByRole("button",{name:"Убрать ветвь",exact:true}).click();
        await page.getByRole("button",{name:"Добавить ветвь",exact:true}).click();
        await page.getByRole("group",{name:"Новая ветвь 3",exact:true}).waitFor({timeout:1500});
        assert.equal(await page.getByRole("group",{name:"Новая ветвь 2",exact:true}).count(),1,"Removed branches must not reuse another helper's label");
        assert.equal(await page.getByLabel(`Зависимость задачи ${child.title} ${child.id.slice(0,6)} от новой ветви 2`,{exact:true}).count(),1);
        await page.getByRole("group",{name:"Новая ветвь 3",exact:true}).getByLabel("Зависимость от новой ветви 2",{exact:true}).check();
        for(const number of [2,3])await page.getByRole("group",{name:`Новая ветвь ${number}`,exact:true}).getByRole("button",{name:"Убрать ветвь",exact:true}).click();
        await page.getByRole("button",{name:"Добавить ветвь",exact:true}).click();
        await page.getByLabel("Изменить задачу "+child.title+" "+child.id.slice(0,6),{exact:true}).check();
        await page.getByLabel("Результат · "+child.title+" "+child.id.slice(0,6),{exact:true}).fill("Repair the parser under the exact owner-approved criteria.");
        const helperDependency=page.getByLabel(`Зависимость задачи ${child.title} ${child.id.slice(0,6)} от новой ветви 4`,{exact:true});
        const helperFirst=process.env.FLYCODE_HELPER_FIRST==="1";
        await helperDependency.check({timeout:1500});if(!helperFirst)await helperDependency.uncheck();
        await page.getByLabel("Название новой ветви",{exact:true}).fill("Additional owner branch");
        await page.getByLabel("Результат новой ветви",{exact:true}).fill(`Set second value to 2 ${helperFirst?"before":"after"} the verified parser repair.`);
        await page.getByLabel("Шаблон исполнителя",{exact:true}).selectOption(child.id);
        await page.getByLabel("Критерии новой ветви",{exact:true}).selectOption({label:"Explicit additional branch criteria"});
        await page.getByRole("group").filter({has:page.getByLabel("Название новой ветви",{exact:true})}).getByText("Зависимости",{exact:true}).click();
        const childDependency=page.getByLabel("Зависимость новой ветви · "+child.title+" "+child.id.slice(0,6),{exact:true});
        await childDependency.check();if(helperFirst)await childDependency.uncheck();
        await page.getByLabel("Разрешаю изменения плана",{exact:true}).check();
        const replan=page.getByRole("button",{name:"Применить изменения плана",exact:true});
        for(const width of [1440,390,320]){await page.setViewportSize({width,height:width===1440?1000:844});await replan.scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,`owner-replan-${width}.png`)});}
        await replan.focus();await page.keyboard.press("Enter");
        await page.getByText("Изменения плана приняты. Расходы и срок сохранены.",{exact:true}).waitFor();
        await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
        const revised=(await(await page.request.get(`${base}/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`,{headers})).json()).result;
        assert.equal(revised.tasks.length,7);assert.equal(revised.graph_revision,1);
        const addedWriter=revised.tasks.find(task=>task.title==="Additional owner branch");
        if(helperFirst){assert.deepEqual(revised.tasks.find(task=>task.id===child.id).dependencies,[addedWriter.id]);assert.deepEqual(addedWriter.dependencies,[]);}
        if(process.env.FLYCODE_COMPLETION_GATE==="1")await page.evaluate(({id,token})=>{
          window.gatedRead=false;window.gatedErrors=[];
          window.gatedRefresh=setInterval(()=>{if(window.gatedRead)return;window.gatedRead=true;
            fetch(`/api/tasks/${id}`,{headers:{"X-FlyCode-Token":token}}).then(response=>{if(!response.ok)throw new Error(String(response.status));return response.json();}).catch(error=>window.gatedErrors.push(error.message)).finally(()=>{window.gatedRead=false;});
          },50);
        },{id,token:bootstrap.token});
        assert.equal(phase("complete").current,true);
        if(process.env.FLYCODE_COMPLETION_GATE==="1"){
          await page.evaluate(()=>clearInterval(window.gatedRefresh));await page.waitForFunction(()=>!window.gatedRead);
          assert.deepEqual(await page.evaluate(()=>window.gatedErrors),[]);
        }
        await page.keyboard.press("Escape");
        await page.locator("#detailBody").getByText("Изменения применены",{exact:false}).first().waitFor();
        const completed=await(await page.request.get(base+"/api/tasks/"+id,{headers})).json();assert.equal(completed.integration.current,true);assert.equal(completed.completion.current,true);
        for(const width of [1440,390,320]){await page.setViewportSize({width,height:width===1440?1000:844});await page.locator("#detailBody").getByText("Изменения применены",{exact:false}).first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`owner-integrated-${width}.png`)});}
        assert.equal(phase("regressions").passed,true);
        assert.equal(phase("invalidate").changed,true);
        const invalidated=await(await page.request.get(base+"/api/tasks/"+id,{headers})).json();assert.equal(invalidated.integration.current,false);assert.equal(invalidated.completion.current,false);
        assert.equal(phase("historical-materials").passed,true);
        fs.writeFileSync(path.join(out,"native-lifecycle.json"),JSON.stringify({checkpoint,revised,completed,invalidated},null,2));
      }
    }
    if(process.env.FLYCODE_OWNER_CASE==="recovery"){
      const openPlan=async()=>{await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();};
      await openPlan();
      const previewResponse=await page.request.get(`${base}/api/tasks/owner/${card.task.workflow_id}?project_id=${card.task.project_id}`,{headers});assert.ok(previewResponse.ok(),await previewResponse.text());
      const preview=(await previewResponse.json()).result,writers=preview.tasks.filter(task=>task.kind!=="review"&&task.profile_ids.length),bodies=[];
      for(const task of writers)await page.locator(`[data-owner-prepare="${task.id}"]`).check();
      await page.getByLabel("Контроллер подготовки",{exact:true}).selectOption("heuristic_fsm");
      let partial=true;
      await page.route("**/api/tasks/owner",async route=>{
        const body=route.request().postDataJSON();bodies.push(body);
        if(partial){partial=false;const response=await route.fetch();assert.ok(response.ok(),await response.text());const outcome=await response.json();
          await route.fulfill({status:409,contentType:"application/json",body:JSON.stringify({detail:{code:"owner_prepare_failed",result:{...outcome.result,partial:true}}})});
        }else await route.continue();
      });
      await page.getByRole("button",{name:"Подготовить папку",exact:true}).click();
      await page.getByText("Подготовлена часть папок. Повторите сохранённый запрос, чтобы продолжить.",{exact:true}).waitFor();
      await page.reload();await page.getByRole("button",{name:new RegExp(card.task.short_id)}).click();await openPlan();
      await page.getByRole("button",{name:"Повторить сохранённый запрос",exact:true}).click();
      await page.getByText("Папка подготовлена. Выполнение ещё не разрешено.",{exact:true}).waitFor();
      await page.locator("#ownerStatus").getByText("План загружен",{exact:true}).waitFor();
      assert.equal(bodies.length,2);assert.deepEqual(bodies[0],bodies[1]);await page.unroute("**/api/tasks/owner");
      // Controlled delayed scope failure must not erase a subsequently opened dialog.
      for(const task of writers)await page.locator(`[data-owner-prepare="${task.id}"]`).check();
      await page.getByLabel("Контроллер подготовки",{exact:true}).selectOption("heuristic_fsm");
      let held,arrive;const arrived=new Promise(resolve=>{arrive=resolve;});
      await page.route("**/api/tasks/owner",route=>{held=route;arrive();});
      await page.getByRole("button",{name:"Подготовить папку",exact:true}).click();await arrived;
      await page.keyboard.press("Escape");await openPlan();
      await held.fulfill({status:403,contentType:"application/json",body:JSON.stringify({detail:{code:"scope"}})});await page.unroute("**/api/tasks/owner");
      // Observe completion of the old response, then ensure the new context remains intact.
      await page.waitForFunction(()=>!sessionStorage.getItem(Object.keys(sessionStorage).find(key=>key.startsWith("flycode-owner-v1:"))));
      assert.equal(await page.locator("#ownerBody form").count(),2);assert.equal(await page.locator("#ownerError").isVisible(),false);
      await page.locator("#ownerStops summary").click();assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="cancel"]`).isEnabled(),true);
      await page.screenshot({path:path.join(out,"controlled-recovery-320.png")});
      await page.keyboard.press("Escape");
      let catalogueHeld,catalogueArrive;const catalogueArrived=new Promise(resolve=>{catalogueArrive=resolve;});
      await page.route("**/api/verification/catalogue?*",route=>{catalogueHeld=route;catalogueArrive();});
      await page.locator("#detailBody").getByRole("button",{name:"План выполнения",exact:true}).click();await catalogueArrived;
      await page.locator("#ownerStops summary").click();assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="cancel"]`).isEnabled(),true);
      await catalogueHeld.fulfill({status:403,contentType:"application/json",body:JSON.stringify({detail:{code:"scope"}})});await page.unroute("**/api/verification/catalogue?*");
      await page.locator("#ownerStatus").getByText("План недоступен",{exact:true}).waitFor();
      assert.equal(await page.locator("#ownerBody").innerText(),"");assert.ok(!(await page.locator("#ownerStops").innerText()).includes(card.task.title));
      await page.locator("#ownerStops summary").click();assert.equal(await page.locator(`[data-owner-stop="${id}"][data-operation="pause"]`).isEnabled(),true);
      fs.writeFileSync(path.join(out,"controlled-recovery.json"),JSON.stringify({partial_response:"controlled after real complete prepare",bodies,stale_scope:"controlled"},null,2));
    }
    assert.deepEqual(errors,[]);
    console.log("Native owner read-only preview and responsive keyboard dialog passed");
  }catch(error){if(page){await page.screenshot({path:path.join(out,"failure.png")});fs.writeFileSync(path.join(out,"failure.html"),await page.content());}throw error;}
  finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
