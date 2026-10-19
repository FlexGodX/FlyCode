const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
(async()=>{
  const base=process.env.FLYCODE_UI_URL,id=process.env.FLYCODE_RESULT_TASK,out=process.env.FLYCODE_SCREENSHOTS;
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:"chrome",headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto(base+"/tasks");
    const bootstrap=await (await page.request.get(base+"/api/bootstrap")).json(),headers={"X-FlyCode-Token":bootstrap.token};
    const task=await (await page.request.get(base+"/api/tasks/"+id,{headers})).json();
    const materials=await (await page.request.get(base+"/api/tasks/"+id+"/materials",{headers})).json();
    await page.getByRole("button",{name:new RegExp(task.task.short_id)}).click();
    await page.getByRole("heading",{name:task.task.title,exact:true}).waitFor();
    await page.screenshot({path:path.join(out,"before-disclosure.png")});
    const summary=page.locator("#detailBody").getByText("Файлы результата",{exact:true});
    await summary.waitFor({timeout:5000});await summary.focus();await page.keyboard.press("Enter");
    const target=materials.items.find(item=>item.kind==="patch"&&item.stage==="target"&&item.name==="src/parser.py");
    const report=materials.items.find(item=>item.kind==="review"&&item.name.endsWith("/junit.xml"));
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:width===1440?1000:844});
      await page.locator(`[data-material-id="${target.id}"]`).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(out,`materials-${width}.png`)});
    }
    for(const [item,name] of [[target,"target.bin"],[report,"junit.bin"]]) {
      const downloadEvent=page.waitForEvent("download");
      await page.locator(`[data-material-id="${item.id}"]`).click();
      const download=await downloadEvent;await download.saveAs(path.join(out,name));
      assert.equal(await download.failure(),null);
    }
    assert.equal(fs.readFileSync(path.join(out,"target.bin"),"utf8"),"value = 1\n");
    assert.ok(fs.readFileSync(path.join(out,"junit.bin"),"utf8").includes("test_parse"));
    await page.route(`**/api/tasks/${id}/materials/${target.id}`,route=>route.fulfill({status:409,json:{detail:{code:"material_changed"}}}));
    await page.locator(`[data-material-id="${target.id}"]`).click();
    await page.getByText("Файл изменён или недоступен. Обновите список.",{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,"download-rejected-320.png")});
    await page.keyboard.press("Escape");assert.equal(await page.locator("#taskDetail").isVisible(),false);
    const other=await (await page.request.get(base+"/api/tasks/"+process.env.FLYCODE_OTHER_TASK,{headers})).json();
    for(const download of [false,true]) {
      const routePattern=`**/api/tasks/${id}/materials${download?"/"+target.id:""}`;
      await page.unroute(routePattern);
      let held,arrived;
      const pending=new Promise(resolve=>{arrived=resolve;});
      await page.route(routePattern,route=>{held=route;arrived();});
      await page.getByRole("button",{name:new RegExp(task.task.short_id)}).click();
      const disclosure=page.locator("#detailBody").getByText("Файлы результата",{exact:true});
      await disclosure.click();
      if(download)await page.locator(`[data-material-id="${target.id}"]`).click();
      await pending;
      await page.keyboard.press("Escape");
      await page.getByRole("button",{name:new RegExp(other.task.short_id)}).click();
      await page.locator("#detailBody").getByText(other.task.short_id,{exact:true}).waitFor();
      await held.fulfill({status:403,json:{detail:{code:"scope"}}});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.equal(await page.locator("#detailBody").getByText(other.task.short_id,{exact:true}).isVisible(),true,
        "An old material scope error must not hide the newer task");
      assert.equal(await page.locator("#detailBody").getByRole("button",{name:"Приостановить",exact:true}).isVisible(),true);
      await page.screenshot({path:path.join(out,`late-${download?"download":"catalogue"}-scope-320.png`)});
      await page.keyboard.press("Escape");await page.unroute(routePattern);
    }
    assert.deepEqual(errors,[]);console.log("Native historical material downloads and rejected-download UI passed");
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
