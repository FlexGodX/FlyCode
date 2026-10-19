// Real browser/HTTP/process controls; phase socket belongs only to the test runner.
const {chromium}=require(process.env.FLYCODE_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const output=process.env.FLYCODE_SCREENSHOTS,fd=Number(process.env.FLYCODE_PHASE_FD);
fs.mkdirSync(output,{recursive:true});
function read(length){const result=Buffer.alloc(length);let offset=0;while(offset<length){const count=fs.readSync(fd,result,offset,length-offset);assert.ok(count,'Test controller closed');offset+=count;}return result;}
function phase(value){const data=Buffer.from(JSON.stringify({phase:value})),header=Buffer.alloc(4);header.writeUInt32BE(data.length);fs.writeSync(fd,Buffer.concat([header,data]));const length=read(4).readUInt32BE();assert.ok(length<=8192);return JSON.parse(read(length).toString());}
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const report={case:process.env.FLYCODE_BROWSER_CASE,passed:false,pageerrors:[],stopRequests:[],attach_requests:0};
 let base=process.env.FLYCODE_UI_URL,token;
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(8000);
 page.on('pageerror',error=>report.pageerrors.push(error.message));
 page.on('request',r=>{if(new URL(r.url()).pathname==='/api/tasks/coordinator/attach')report.attach_requests++;});
 const bootstrap=async()=>{token=(await(await page.request.get(base+'/api/bootstrap')).json()).token;};
 const request=async(route,body)=>{const response=body===undefined?await page.request.get(base+route,{headers:{'X-FlyCode-Token':token}}):await page.request.post(base+route,{headers:{'X-FlyCode-Token':token},data:body});assert.ok(response.ok(),await response.text());return response.json();};
 const screenshot=async name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
 const row=id=>page.locator(`[data-stop-id="${id}"]`);
 try{
  await bootstrap();await page.goto(base+'/tasks');await page.getByRole('heading',{name:'Задачи',exact:true}).waitFor();await screenshot('before-controls');
  let status;
  if(report.case==='lost-stop')status=await request('/api/tasks/coordinator/attach',{});
  else{
   let automatic=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/api/tasks/coordinator/attach')automatic++;});
   assert.equal((await page.request.get(base+'/api/tasks/coordinator',{headers:{'X-FlyCode-Token':token}})).status(),409);
   await page.getByRole('button',{name:'Подключить выполнение',exact:true}).focus();
   assert.equal(automatic,0,'Loading Tasks must not attach or start a coordinator');
   const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks/coordinator/attach'&&r.request().method()==='POST');
   await page.keyboard.press('Enter');status=await(await response).json();
   assert.equal(automatic,1);
  }
  assert.deepEqual(Object.keys(status).sort(),['generation','phase','registration_id','session_id']);
  report.session=status;
  if(report.case!=='lost-stop')await page.getByText('Служба задач подключена',{exact:true}).waitFor();
  await screenshot('connected');
  if(report.case==='attach'){
   await page.reload();await page.getByText('Служба задач подключена',{exact:true}).waitFor();
   await page.goto('about:blank');phase('close-first');base=process.env.FLYCODE_SECOND_UI_URL;await bootstrap();await page.goto(base+'/tasks');
   await page.getByText('Служба задач подключена',{exact:true}).waitFor();assert.deepEqual(await request('/api/tasks/coordinator'),status);
  }else{
   let ids;
   if(report.case==='runtime'){
    await page.goto('about:blank');phase('close-first');base=process.env.FLYCODE_SECOND_UI_URL;await bootstrap();await page.goto(base+'/tasks');
    assert.deepEqual(await request('/api/tasks/coordinator'),status);phase('ready');
    const id=process.env.FLYCODE_TASK_ID;ids=[id];
    await page.locator('button.task-card').filter({has:page.getByText(process.env.FLYCODE_TASK_SHORT_ID,{exact:true})}).click();
    await page.getByRole('heading',{name:process.env.FLYCODE_TASK_TITLE,exact:true}).waitFor();
   }else{
    const project=(await request('/api/tasks/projects',{selector:'.'})).project_id;ids=[];
    for(let i=0;i<3;i++)ids.push((await request('/api/tasks',{project_id:project,task:{title:'Private task '+i,objective:'Private objective must disappear'},idempotency_key:'private-'+i})).task.id);
    base=process.env.FLYCODE_SECOND_UI_URL;await bootstrap();await page.goto(base+'/tasks');
    await page.getByRole('button',{name:/Private task 0/}).click();await page.getByRole('heading',{name:'Private task 0',exact:true}).waitFor();
    phase('close-first');
   }
   const lost=report.case==='lost-stop'||process.env.FLYCODE_ROOT_LOSS==='1';
   report.target_id=ids[0];
   if(lost){
    phase('replace-root');await row(ids[0]).waitFor();
    assert.equal(await page.locator('#taskDetail').evaluate(dialog=>dialog.open),false);
    assert.ok(!(await page.locator('body').innerText()).includes(report.case==='lost-stop'?'Private task 0':process.env.FLYCODE_TASK_TITLE));
    await page.reload();
    await row(ids[0]).waitFor();assert.equal((await page.request.get(base+'/api/tasks/'+ids[0],{headers:{'X-FlyCode-Token':token}})).status(),403);
    assert.ok(!(await page.locator('body').innerText()).includes('Private objective'));
    assert.equal(await page.getByRole('button',{name:'Продолжить',exact:true}).count(),0);
    await page.goto('about:blank');const restart=phase('restart-second');base=restart.url;await bootstrap();await page.goto(base+'/tasks');await row(ids[0]).waitFor();
    assert.deepEqual(await request('/api/tasks/coordinator'),status);
   }
   if(report.case==='lost-stop'){
    const id=ids[0];let lostReply=true;
    await page.route('**/api/tasks/'+id+'/stop',async route=>{
     report.stopRequests.push(route.request().postDataJSON());
     if(lostReply){lostReply=false;const response=await route.fetch();assert.equal(response.status(),200);await route.abort();}
     else await route.continue();
    });
    await row(id).getByRole('button',{name:'Приостановить',exact:true}).click();
    await row(id).getByRole('alert').waitFor();
    await row(id).getByRole('button',{name:'Подтвердить паузу',exact:true}).click();
    assert.equal(report.stopRequests.length,2);assert.deepEqual(report.stopRequests[0],report.stopRequests[1]);
    await row(id).getByText('Команда принята. Остановка подтверждается отдельно.',{exact:true}).waitFor();
    const other=ids[1];let collide=true;
    await page.route('**/api/tasks/'+other+'/stop',async route=>{
     if(collide){collide=false;const body=route.request().postDataJSON();await request('/api/tasks/'+other+'/stop',{...body,operation:'pause',idempotency_key:'external-pause'});}
     await route.continue();
    });
    await row(other).getByRole('button',{name:'Отменить задачу',exact:true}).click();
    await row(other).getByText(/Конфликт версии/).waitFor();
    assert.ok((await request('/api/tasks/stops')).items.some(item=>item.task_id===other&&item.state==='planned'));
    await row(other).getByRole('button',{name:'Отменить задачу',exact:true}).click();
    await page.waitForFunction(id=>!document.querySelector(`[data-stop-id="${id}"]`),other);
    const cancelled=ids[2],cancelBodies=[];let lostCancel=true;
    await page.route('**/api/tasks/'+cancelled+'/stop',async route=>{
     cancelBodies.push(route.request().postDataJSON());
     if(lostCancel){lostCancel=false;const response=await route.fetch();assert.equal(response.status(),200);await route.abort();}
     else await route.continue();
    });
    await row(cancelled).getByRole('button',{name:'Отменить задачу',exact:true}).click();await row(cancelled).getByRole('alert').waitFor();
    assert.ok(!(await request('/api/tasks/stops')).items.some(item=>item.task_id===cancelled),'Accepted cancel leaves discovery before acknowledgement');
    await row(cancelled).getByRole('button',{name:'Подтвердить отмену',exact:true}).click();
    await page.waitForFunction(id=>!document.querySelector(`[data-stop-id="${id}"]`),cancelled);
    assert.equal(cancelBodies.length,2);assert.deepEqual(cancelBodies[0],cancelBodies[1]);report.cancelRequests=cancelBodies;
   }else{
    const id=ids[0],container=lost?row(id):page.locator('#detailBody');
    const model=process.env.FLYCODE_MODEL_STOP==='1';
    let reads=Promise.resolve(),activeReads=0;
    if(model){
     report.reads=[];report.reads_at_stop=null;
     page.on('request',r=>{if(new URL(r.url()).pathname==='/api/tasks/'+id+'/stop'&&report.reads_at_stop===null)report.reads_at_stop=activeReads;});
     reads=Promise.allSettled(Array.from({length:32},async()=>{
      activeReads++;const started=performance.now();
      try{
       const reply=await page.request.get(base+'/api/tasks/coordinator',{headers:{'X-FlyCode-Token':token}}),body=await reply.json();
       report.reads.push({status:reply.status(),seconds:(performance.now()-started)/1000,code:body.detail?.code});
       if(reply.status()===200)assert.deepEqual(body,status);
       else{assert.equal(reply.status(),409);assert.equal(body.detail?.code,'attachment_identity');}
      }finally{activeReads--;}
     }));
    }
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks/'+id+'/stop'&&r.request().method()==='POST');
    const started=performance.now();await container.getByRole('button',{name:process.env.FLYCODE_STOP_OPERATION==='cancel'?'Отменить задачу':'Приостановить',exact:true}).click();
    const reply=await response;report.stop_seconds=(performance.now()-started)/1000;assert.equal(reply.status(),200);assert.ok(report.stop_seconds<1);
    report.receipt=await reply.json();assert.deepEqual(Object.keys(report.receipt).sort(),['paused','revision','state','task_id']);
    assert.equal(phase('verify-stop').confirmed,true);
    if(model){
     const outcomes=await reads;assert.ok(outcomes.every(outcome=>outcome.status==='fulfilled'),JSON.stringify(outcomes));
     assert.equal(report.reads.length,32);assert.ok(report.reads_at_stop>0);
     report.after_burst_status=await request('/api/tasks/coordinator');assert.deepEqual(report.after_burst_status,status);
     assert.equal(report.attach_requests,1);
     const body=reply.request().postDataJSON();
     const replay=await Promise.all(Array.from({length:6},()=>request('/api/tasks/'+id+'/stop',body)));
     assert.ok(replay.every(receipt=>JSON.stringify(receipt)===JSON.stringify(report.receipt)));
     report.replays=replay.length;report.replayed_body=body;
     const settled=phase('release-model');assert.equal(settled.confirmed,true);
     await page.reload();
     if(lost){
      if(settled.state==='cancelled'){await row(settled.root_task_id).waitFor();assert.equal(await row(id).count(),0);}
      else await row(id).getByText(`Версия ${settled.revision} · Ожидает повтора · Пауза`,{exact:true}).waitFor();
      assert.ok(!(await page.locator('body').innerText()).includes(process.env.FLYCODE_TASK_TITLE));
     }else{
      const card=page.locator('button.task-card').filter({has:page.getByText(process.env.FLYCODE_TASK_SHORT_ID,{exact:true})});
      await card.getByText(settled.state==='cancelled'?'Отменена':'Ожидает повтора · Пауза',{exact:true}).waitFor();
     }
     assert.equal(report.attach_requests,1);report.current_after_reload=true;
    }
   }
  }
  for(const width of [1440,390,320]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await screenshot('controls-'+width);}
  if(report.case==='lost-stop'){
   await row(report.target_id).getByRole('button',{name:'Отменить задачу',exact:true}).focus();await page.keyboard.press('Enter');
   await page.waitForFunction(id=>!document.querySelector(`[data-stop-id="${id}"]`),report.target_id);
  }
  assert.deepEqual(report.pageerrors,[]);report.passed=true;
 }finally{
  fs.writeFileSync(path.join(output,'browser-result.json'),JSON.stringify(report,null,2));await browser.close();
 }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
