"use strict";
(() => {
  const $=selector=>document.querySelector(selector);
  const active=new Set(['queued','preparing','running','evaluating','cancelling','reconciling']);
  const states={draft:'Черновик',queued:'В очереди',preparing:'Подготовка',running:'Выполняется',evaluating:'Оценка результата',completed:'Завершена',blocked:'Заблокирована',cancelling:'Останавливается',cancelled:'Отменена',interrupted:'Прервана',reconciling:'Подтверждение остановки'};
  const stages={baseline:'Исходная версия',target:'Изменения',regression:'Регрессии',integration:'Объединённый результат'};
  const outcomes={passed:'Пройден',failed:'Не пройден',error:'Ошибка',skipped:'Пропущен',xfail:'Ожидаемый сбой',xpass:'Неожиданный успех',inconclusive:'Недостаточно данных',cancelled:'Отменён',invalidated:'Результат устарел'};
  const reasons={subject_changed:'Файлы проекта изменились после проверки.',project_changed:'Папка проекта изменилась или недоступна.',evidence_changed:'Отчёт изменён или недоступен.',paused:'Выполнение приостановлено.',deadline:'Время исчерпано.',budget:'Бюджет исчерпан.',attempt_limit:'Лимит попыток исчерпан.',verification_environment_changed:'Среда изменилась. Подготовьте новый снимок.',ownership_unknown:'Остановка ещё не подтверждена.',fence:'Результат получен после отзыва права на выполнение.',verification_profile_changed:'Набор проверок изменился.',scope:'Нет доступа к проекту.'};
  let token, catalogue={subjects:[],profiles:[]},projects=[],plans=[],selected=null,current=null,sequence=0,signature='',refreshing=false,again=false,stream=null,serviceBusy=false,serviceConnected=false;
  let createIntent=null,listSignature='',stopSignature='',previewSequence=0;const pending=new Map(),busy=new Set();
  const node=(tag,text,className)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;};
  const name=id=>projects.find(item=>item.project_id===id)?.name||'Локальный проект';
  const reason=code=>reasons[code]||'Проверка недоступна. Проверьте доступ к проекту и повторите действие.';
  function problem(error,target='#notice') {$(target).hidden=false;$(target).textContent=error.message;}
  async function api(url,body) {
    const response=await fetch(url,{headers:{'X-FlyCode-Token':token,'Content-Type':'application/json'},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok){const code=data.detail?.code,error=new Error(code==='revision'?'Данные изменились. Проверьте новый статус и повторите действие.':code?reason(code):'Не удалось выполнить запрос. Повторите действие.');error.code=code;throw error;}return data;
  }
  function status(plan) {
    if(active.has(plan.state))return [states[plan.state],plan.state];
    if(plan.state==='cancelled')return ['Проверка отменена','cancelled'];
    if(plan.verdict?.status==='passed'&&!plan.current)return ['Нужна повторная проверка','stale'];
    if(plan.state==='interrupted'||plan.state==='blocked')return [states[plan.state],plan.state];
    return plan.verdict?[{passed:'Проверка пройдена',failed:'Проверка не пройдена',cancelled:'Проверка отменена',invalidated:'Нужна повторная проверка',inconclusive:'Недостаточно данных'}[plan.verdict.status]||plan.verdict.status,plan.verdict.status]:[states[plan.state],plan.state];
  }
  function renderPlans() {
    $('#newPlan').disabled=!catalogue.profiles.some(profile=>catalogue.subjects.some(subject=>subject.project_id===profile.project_id));
    const next=JSON.stringify([plans,projects]);if(listSignature===next)return;listSignature=next;
    const focused=document.activeElement?.closest('.verification-card')?.dataset.plan;
    const area=$('#verificationPlans');area.replaceChildren();
    if(!plans.length){const empty=node('div',undefined,'task-empty');empty.append(node('h2','Планов проверок пока нет'),node('p','Выберите сохранённый снимок проекта и набор проверок. Создание плана не запускает инструменты.'));area.append(empty);}
    for(const plan of plans){const card=node('button',undefined,'verification-card'),[text,state]=status(plan),label=node('span',text,'verification-status');card.dataset.plan=plan.id;label.dataset.status=state;card.append(node('strong',plan.title),node('small',name(plan.project_id)),label,node('small',`Версия файлов ${plan.subject_hash.slice(0,12)} · расход ${plan.spent} / ${plan.budget.max_tool_calls}`));card.onclick=()=>open(plan.id).catch(problem);area.append(card);}
    if(focused)area.querySelector(`[data-plan="${focused}"]`)?.focus();
  }
  function renderStops(handles) {
    const rows=new Map(handles.filter(item=>!plans.some(plan=>plan.id===item.plan_id)).map(item=>[item.plan_id,item]));
    for(const [id,intent] of pending)if(intent.operation==='cancel'&&!plans.some(plan=>plan.id===id)&&!rows.has(id))rows.set(id,{plan_id:id,revision:intent.expected_revision,state:'unknown'});
    const next=JSON.stringify([[...rows.values()],[...busy]]);if(stopSignature===next)return;stopSignature=next;
    const focused=document.activeElement?.dataset.stop;
    const area=$('#verificationStops');area.replaceChildren();
    for(const handle of rows.values()){const row=node('article',undefined,'stop-handle'),button=node('button','Остановить недоступную проверку');button.dataset.stop=handle.plan_id;row.append(node('h2',`Проверка ${handle.plan_id.slice(0,8)}`),node('p','Папка недоступна. Сохранено только право отмены.'));button.onclick=()=>mutate({id:handle.plan_id,revision:handle.revision},'cancel').catch(problem);button.disabled=busy.has(handle.plan_id);row.append(button);area.append(row);}area.hidden=!area.childElementCount;
    if(focused)area.querySelector(`[data-stop="${focused}"]`)?.focus();
  }
  async function service(attach=false) {
    if(serviceBusy)return;serviceBusy=true;$('#attachCoordinator').disabled=true;
    try{await api(attach?'/api/tasks/coordinator/attach':'/api/tasks/coordinator',attach?{}:undefined);serviceConnected=true;$('#coordinatorStatus').textContent='Служба проверок подключена';$('#attachCoordinator').hidden=true;}
    catch(error){serviceConnected=false;$('#coordinatorStatus').textContent=attach?error.message:'Служба проверок недоступна';$('#attachCoordinator').hidden=false;}
    finally{serviceBusy=false;$('#attachCoordinator').disabled=false;renderActions();}
  }
  function profileOptions() {
    const select=$('#planProfile');select.replaceChildren();for(const profile of catalogue.profiles.filter(item=>item.project_id===$('#planProject').value))select.add(new Option(profile.title,profile.id));subjectOptions();
  }
  function subjectOptions() {
    const profile=catalogue.profiles.find(item=>item.id===$('#planProfile').value),area=$('#planSubjects');area.replaceChildren();if(!profile)return;
    for(const stage of new Set(profile.scope==='registered-suite'?['target']:profile.checks.map(check=>check.stage))){const label=node('label',`Снимок · ${stages[stage]}`),select=node('select');select.name=stage;select.required=true;for(const subject of catalogue.subjects.filter(item=>item.project_id===profile.project_id))select.add(new Option(`Версия ${subject.subject_hash.slice(0,12)} · ${subject.id.slice(0,8)}`,subject.id));label.append(select);area.append(label);}
  }
  function newPlan() {
    createIntent=null;$('#planError').textContent='';$('#planProject').replaceChildren();const ids=new Set(catalogue.profiles.map(item=>item.project_id));for(const id of ids)$('#planProject').add(new Option(name(id),id));profileOptions();$('#planDialog').showModal();$('#planProfile').focus();
  }
  async function savePlan(event) {
    event.preventDefault();const profile=catalogue.profiles.find(item=>item.id===$('#planProfile').value);if(!profile)return;
    const body={subject_ids:Object.fromEntries([...$('#planSubjects').querySelectorAll('select')].map(select=>[select.name,select.value])),profile_id:profile.id,profile_version:profile.version,budget:{max_tool_calls:Number($('#planTools').value),max_attempts:Number($('#planAttempts').value),wall_seconds:Number($('#planMinutes').value)*60}};
    const serialized=JSON.stringify(body);if(createIntent?.serialized!==serialized)createIntent={serialized,key:crypto.randomUUID()};
    const submit=event.submitter;submit.disabled=true;
    try{const plan=await api('/api/verification/plans',{...body,idempotency_key:createIntent.key});createIntent=null;$('#planDialog').close();await refresh();await open(plan.id);}
    catch(error){problem(error,'#planError');}finally{submit.disabled=false;}
  }
  async function mutate(plan,operation) {
    if(busy.has(plan.id))return;busy.add(plan.id);
    let intent=pending.get(plan.id);if(intent?.operation!==operation)intent={operation,expected_revision:plan.revision,idempotency_key:crypto.randomUUID()};pending.set(plan.id,intent);renderActions();
    try{await api(`/api/verification/plans/${plan.id}/commands`,intent);pending.delete(plan.id);$('#verificationError').textContent='';}
    catch(error){if(error.code==='revision')pending.delete(plan.id);problem(error,selected===plan.id?'#verificationError':'#notice');}
    finally{busy.delete(plan.id);await refresh();renderActions();}
  }
  function renderActions() {
    const focused=document.activeElement?.dataset.operation,area=$('#verificationActions');area.replaceChildren();if(!current)return;
    if(!serviceConnected){const attach=node('button','Подключить выполнение');attach.disabled=serviceBusy;attach.dataset.operation='attach';attach.onclick=()=>service(true);area.append(attach);}
    const actions=[];if(current.state==='draft')actions.push(['start','Запустить проверку']);else if(!active.has(current.state))actions.push(['rerun','Повторить проверку']);
    if(active.has(current.state))actions.push(['cancel','Остановить проверку']);
    const intent=pending.get(current.id);if(intent&&!actions.some(([operation])=>operation===intent.operation))actions.unshift([intent.operation,'Повторить команду']);
    for(const [operation,text] of actions){const button=node('button',text,operation==='cancel'?'verification-stop':'primary');button.dataset.operation=operation;button.disabled=busy.has(current.id)||(operation==='rerun'&&(current.attempts.length>=current.budget.max_attempts||current.spent>=current.budget.max_tool_calls));button.onclick=()=>mutate(current,operation);area.append(button);}
    if(focused)area.querySelector(`[data-operation="${focused}"]`)?.focus();
  }
  function renderDetail(plan) {
    current=plan;renderActions();const next=JSON.stringify(plan);if(signature===next)return;signature=next;
    $('#verificationTitle').textContent=plan.title;const area=$('#verificationResult');area.replaceChildren();const [text,state]=status(plan),label=node('p',text,'verification-status');label.dataset.status=state;area.append(label);
    for(const blocker of plan.blockers)area.append(node('p',reason(blocker),'verification-blocker'));
    const latest=plan.attempts.at(-1),required=plan.checks.filter(check=>check.required),completed=required.filter(check=>latest&&Array.from({length:plan.reruns+1},(_,index)=>index+1).every(number=>latest.checks.some(call=>call.check_id===check.id&&call.number===number&&call.complete))).length;
    area.append(node('p',`${completed} / ${required.length} обязательных проверок завершено`),node('p',`Расход: ${plan.spent} / ${plan.budget.max_tool_calls} · резерв: ${plan.reserved} · попытки: ${plan.attempts.length} / ${plan.budget.max_attempts}`,'verification-meta'),node('p',`Версия файлов: ${plan.subject_hash.slice(0,16)} · версия плана: ${plan.revision}`,'verification-meta'),node('p','Локальные проверки. Вызовов модели нет.','verification-meta'));
    if(plan.deadline_at)area.append(node('p',`Общий срок: ${new Date(plan.deadline_at*1000).toLocaleString('ru-RU')}`,'verification-meta'));
    area.append(node('h3','Что проверяется'));
    if(plan.acceptance){area.append(node('p',`${plan.acceptance.title||'Критерии приёмки'} · версия ${plan.acceptance.version}`,'verification-meta'));for(const criterion of plan.acceptance.criteria){const box=node('div',undefined,'verification-check');box.append(node('strong',criterion.title||criterion.id),node('p',`${criterion.required?'Обязательный':'Дополнительный'} · ${criterion.check_ids.join(', ')}`,'verification-meta'));if(plan.verdict?.criteria[criterion.id])box.append(node('p',outcomes[plan.verdict.criteria[criterion.id]]||plan.verdict.criteria[criterion.id]));area.append(box);}}
    for(const check of plan.checks){const box=node('div',undefined,'verification-check');box.append(node('strong',`${stages[check.stage]} · ${check.id}`),node('p',`${check.required?'Обязательная':'Дополнительная'} · ${check.timeout} с · ${check.targets.join(', ')||'Все тесты сохранённого проекта'}${check.expected_outcome==='failed'?' · Ожидается исходный сбой':''}`,'verification-meta'));area.append(box);}
    const explanations={'Registered pytest check passed on the captured subject':'Все зарегистрированные тесты прошли на сохранённой версии проекта.','Mandatory assertions failed':'Обязательные проверки обнаружили ошибки.'};
    if(plan.verdict?.reasons.length){area.append(node('h3','Причины результата'));for(const message of plan.verdict.reasons)area.append(node('p',explanations[message]||message));}
    for(const attempt of [...plan.attempts].reverse()){
      const box=node('details',undefined,'verification-attempt');box.open=attempt===latest;box.append(node('summary',`Попытка ${attempt.number} · ${attempt.verdict?outcomes[attempt.verdict.status]||attempt.verdict.status:states[attempt.state]}`));
      for(const check of attempt.checks){const entry=node('div',undefined,'verification-check');entry.append(node('strong',`${check.check_id} · запуск ${check.number}`),node('p',`${states[check.state]||check.state}${check.duration!==null?` · ${check.duration.toFixed(2)} с`:''}${check.error_class?` · ${check.error_class}`:''}${check.late?' · Запоздалый результат':''}`,'verification-meta'));const list=node('ul');for(const item of check.cases)list.append(node('li',`${item.id} · ${outcomes[item.outcome]||item.outcome}${item.flaky?' · Нестабильный результат':''}`));entry.append(list);box.append(entry);}
      const artifacts=node('div',undefined,'verification-artifacts');for(const artifact of plan.artifacts.filter(item=>item.execution_id===attempt.id)){const filename=artifact.name.split('/').at(-1);for(const download of [false,true]){const button=node('button',`${download?'Скачать':'Открыть'} ${filename} · попытка ${attempt.number}`);button.onclick=()=>report(plan.id,artifact,download).catch(error=>problem(error,'#verificationError'));artifacts.append(button);}}box.append(artifacts);area.append(box);
    }
  }
  async function report(id,artifact,download) {
    const request=download?null:++previewSequence,relevant=()=>selected===id&&(download||request===previewSequence);
    try{const response=await fetch(`/api/verification/plans/${id}/artifacts/${artifact.id}`,{headers:{'X-FlyCode-Token':token}});if(!response.ok)throw new Error('Отчёт изменён или недоступен.');
      const blob=await response.blob();if(download){const url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=artifact.name.split('/').at(-1);link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      else{const text=await blob.text();if(!relevant())return;$('#reportPreview').textContent=text;$('#reportPreview').hidden=false;$('#reportPreview').focus();}
    }catch(error){if(relevant())throw error;}
  }
  async function detail(id) {
    const request=++sequence;const plan=await api(`/api/verification/plans/${id}`);if(selected!==id||request!==sequence||!$('#verificationDetail').open)return;renderDetail(plan);
  }
  async function open(id) {
    selected=id;signature='';current=null;previewSequence++;$('#verificationTitle').textContent='Загружаем…';$('#verificationResult').replaceChildren();$('#verificationError').textContent='';$('#reportPreview').hidden=true;$('#reportPreview').textContent='';renderActions();if(!$('#verificationDetail').open)$('#verificationDetail').showModal();await detail(id);watch(id);
  }
  async function watch(id) {
    stream?.abort();const control=new AbortController();stream=control;
    try{const response=await fetch(`/api/verification/plans/${id}/events`,{headers:{'X-FlyCode-Token':token},signal:control.signal});if(!response.ok||!response.body)return;const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(selected===id&&!control.signal.aborted){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});let index;while((index=buffer.indexOf('\n\n'))!==-1){const frame=buffer.slice(0,index);buffer=buffer.slice(index+2);if(frame.includes('event: change'))refresh();}if(buffer.length>1048576)break;}await reader.cancel();}catch{/* Polling preserves read-only recovery; commands are never retried automatically. */}
  }
  async function refresh() {
    if(refreshing){again=true;return;}refreshing=true;
    try{do{again=false;const rows=[],handles=[];let after=0;do{const page=await api(`/api/verification/plans?after=${after}&limit=100`);rows.push(...page.items);after=page.next_cursor;}while(after!==null);after=0;do{const page=await api(`/api/verification/stops?after=${after}&limit=100`);handles.push(...page.items);after=page.next_cursor;}while(after!==null);[catalogue,projects]=await Promise.all([api('/api/verification/catalogue'),api('/api/tasks/projects')]);plans=rows;renderPlans();renderStops(handles);if(selected&&$('#verificationDetail').open){try{await detail(selected);}catch(error){if(error.code==='scope'){selected=null;current=null;sequence++;$('#verificationResult').replaceChildren();$('#verificationDetail').close();stream?.abort();}else throw error;}}$('#connection').textContent='Данные сохранены в общем рабочем пространстве';}while(again);}catch(error){problem(error);$('#connection').textContent='Данные могут быть устаревшими';}finally{refreshing=false;}
  }
  $('#newPlan').onclick=newPlan;$('#planForm').onsubmit=savePlan;$('#planProject').onchange=profileOptions;$('#planProfile').onchange=subjectOptions;$('#attachCoordinator').onclick=()=>service(true);
  for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$('#'+button.dataset.close).close();
  $('#verificationDetail').addEventListener('close',()=>{selected=null;current=null;sequence++;previewSequence++;stream?.abort();});
  if(new URLSearchParams(location.search).get('interface')==='desktop'){$('#verificationChats').href='/desktop';$('.tasks-brand').href='/desktop';$('.tasks-brand small').textContent='DESKTOP';try{if(localStorage.getItem('flycode.desktop.theme')==='dark')document.body.dataset.theme='dark';}catch{}}
  (async()=>{try{token=(await api('/api/bootstrap')).token;await refresh();await service();setInterval(refresh,1500);setInterval(service,5000);}catch(error){problem(error);}})();
})();
