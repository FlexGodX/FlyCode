"use strict";
(() => {
  const $ = selector => document.querySelector(selector);
  const names = {planned:"Черновик",ready:"В очереди",assigned:"Назначена",running:"В работе",awaiting_review:"На проверке",succeeded:"Готово",blocked:"Заблокирована",retry_wait:"Ожидает повтора",failed:"Ошибка",cancelled:"Отменена",stopping:"Остановка…",reconciling:"Подтверждение остановки",idle:"Свободен"};
  const reasons = {missing_objective:"Нет описания результата",missing_criteria:"Нет критериев",permission:"Нет разрешения",missing_budget:"Нет бюджета",paused:"Приостановлена",root_not_active:"Ожидает запуска цели",dependency:"Ожидает зависимости",stop_unconfirmed:"Остановка ещё не подтверждена",budget_exhausted:"Бюджет исчерпан",wall_deadline:"Время исчерпано"};
  const columns = {drafts:"Черновики",queue:"Очередь",in_progress:"В работе",review:"На проверке",done:"Готово",blocked:"Ожидание",failed:"Ошибки",cancelled:"Отменённые"};
  let token, projects=[], cards=[], agents=[], view="board", selected=null, detail=null;
  const pendingMutations=new Map();
  let detailRequest=0, historyRequest=0;
  let refreshing=false, refreshAgain=false, historyAfter=null, historyLimit=50, historyComplete=false, delivery=null, streamTimer, refreshTimer;
  function node(tag,text,className) {const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
  function showError(error,target="#notice") {$(target).textContent=error.message;$(target).hidden=false;}
  function errorText(data,status) {
    const code=data.detail?.code;
    return code==="revision"?"Конфликт версии. Данные обновлены; повторите действие после проверки.":
      code==="incomplete"?"Не хватает критериев, разрешения или бюджета. Задача остаётся черновиком.":
      code==="scope"?"Проект недоступен. Обновите страницу и проверьте папку.":
      code?`Команда отклонена: ${code}.`:typeof data.detail==="string"?data.detail:`Ошибка сервера (${status}).`;
  }
  async function api(url,body) {
    const response=await fetch(url,{headers:{"X-FlyCode-Token":token,"Content-Type":"application/json"},...(body===undefined?{}:{method:"POST",body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw new Error(errorText(data,response.status));return data;
  }
  function mutationKey(slot,payload) {
    const serialized=JSON.stringify(payload),previous=pendingMutations.get(slot);
    if(previous?.serialized===serialized)return previous.key;
    const key=crypto.randomUUID();pendingMutations.set(slot,{serialized,key});return key;
  }
  const projectName=id=>projects.find(project=>project.project_id===id)?.name||id;
  const label=value=>names[value]||value;
  const date=value=>value?new Date(value*1000).toLocaleString("ru-RU"):"—";
  function empty(title,description) {const box=node("div",undefined,"task-empty");box.append(node("h2",title),node("p",description));return box;}
  function taskButton(card) {
    const task=card.task, button=node("button",undefined,"task-card");button.type="button";
    button.append(node("small",task.short_id),node("strong",task.title),node("small",projectName(task.project_id)),node("span",label(task.state)+(task.paused?" · Пауза":""),"task-state"));
    button.append(node("small",`Приоритет ${task.priority}${task.archived?" · Архив":""}`));
    if(card.blockers.length)button.append(node("span",card.blockers.map(value=>reasons[value]||value).join(" · "),"blocker"));
    if(card.attempt)button.append(node("small",`Попытка ${card.attempt.id.slice(0,8)} · ${label(card.attempt.state)}`));
    if(card.last_event)button.append(node("small",`Последнее действие: ${card.last_event.kind} · ${date(card.last_event.occurred_at)}`));
    button.onclick=()=>openTask(task.id).catch(showError);return button;
  }
  function render() {
    const content=$("#taskContent");content.replaceChildren();$("#filters").hidden=view==="agents";
    if(view==="agents") {
      if(!agents.length)content.append(empty("Экземпляров агентов пока нет","Здесь появятся зарегистрированные исполнители и их текущие попытки."));
      for(const entry of agents) {
        const box=node("article",undefined,"agent-card"),instance=entry.instance,attempt=entry.attempt;
        box.append(node("h2",`Экземпляр ${instance.id.slice(0,8)} · ${label(instance.state)}`));
        box.append(node("p",`Профиль ${instance.profile_id} · версия ${instance.profile_version}`));
        if(attempt) {
          box.append(node("p",`${attempt.profile_snapshot.role} · ${attempt.profile_snapshot.model_id||attempt.profile_snapshot.model_ref} · ${label(attempt.state)}`));
          box.append(node("p",`Потрачено: ${attempt.spent.model_calls} вызовов модели, ${attempt.spent.tool_calls} инструментов. Резерв: ${attempt.reserved.model_calls} / ${attempt.reserved.tool_calls}.`));
          box.append(node("p",`Начало: ${date(attempt.started_at)} · Подтверждение остановки: ${attempt.stop_confirmed?"получено":"не получено"}`));
        }
        if(entry.task) {const button=node("button",`${entry.task.short_id} · ${entry.task.title}`);button.onclick=()=>openTask(entry.task.id).catch(showError);box.append(button);}
        content.append(box);
      }
      return;
    }
    const query=$("#query").value.trim().toLocaleLowerCase();
    const filtered=cards.filter(({task})=>(!$("#projectFilter").value||task.project_id===$("#projectFilter").value)&&(!$("#stateFilter").value||task.state===$("#stateFilter").value)&&(!$("#priorityFilter").value||task.priority===Number($("#priorityFilter").value))&&(!query||`${task.short_id}\n${task.title}\n${task.objective}`.toLocaleLowerCase().includes(query)));
    filtered.sort((a,b)=>b.task.priority-a.task.priority||a.task.created_at-b.task.created_at||a.task.id.localeCompare(b.task.id));
    if(!filtered.length) {content.append(empty(cards.length?"Ничего не найдено":"Задач пока нет",cards.length?"Измените поиск или фильтры.":"Создайте черновик для одного из локальных проектов."));return;}
    const container=node("div",undefined,view==="board"?"task-board":"task-list");content.append(container);
    if(view==="list") {for(const card of filtered)container.append(taskButton(card));return;}
    for(const [column,title] of Object.entries(columns)) {
      const group=filtered.filter(card=>card.column===column);if(!group.length)continue;
      const section=node("section",undefined,"task-column"),heading=node("h2",title);heading.append(node("span",group.length));section.append(heading);
      for(const card of group)section.append(taskButton(card));container.append(section);
    }
  }
  function projectOptions() {
    const filter=$("#projectFilter"),draft=$("#draftProject"),previous=filter.value,draftPrevious=draft.value;
    filter.replaceChildren(new Option("Все проекты",""));draft.replaceChildren();
    const seen=new Set();for(const project of projects) {
      if(project.status==="unavailable")continue;
      if(project.project_id&&!seen.has(project.project_id)){filter.add(new Option(project.name,project.project_id));seen.add(project.project_id);}
      draft.add(new Option(project.name,project.selector));
    }
    filter.value=previous;draft.value=[...draft.options].some(option=>option.value===draftPrevious)?draftPrevious:draft.options[0]?.value||"";
  }
  async function refresh() {
    if(refreshing){refreshAgain=true;return;}refreshing=true;
    try {
      do {
        refreshAgain=false;
        const items=[];let after=0;
        do {const page=await api(`/api/tasks?limit=100&archived=${$("#archivedFilter").checked}&after=${after}`);items.push(...page.items);after=page.next_cursor;}while(after!==null);
        const current=await api("/api/agents/activity");
        projects=await api("/api/tasks/projects");cards=items;agents=current;projectOptions();render();
        if(selected&&$("#taskDetail").open)await loadDetail(selected);
      } while(refreshAgain);
    } catch(error) {showError(error);$("#connection").textContent="Данные могут быть устаревшими";}
    finally {refreshing=false;}
  }
  function scheduleRefresh() {clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,150);}
  async function loadDetail(id) {
    const request=++detailRequest,card=await api(`/api/tasks/${id}`);if(request!==detailRequest||selected!==id||!$("#taskDetail").open)return;
    detail=card;const task=card.task,body=$("#detailBody");$("#detailTitle").textContent=task.title;body.replaceChildren();
    const dl=node("dl");
    const values={"Номер":task.short_id,"Проект":projectName(task.project_id),"Состояние":label(task.state)+(task.paused?" · Пауза":""),"Приоритет":task.priority,"Версия":task.revision,"Описание":task.objective||"—","Критерии":task.acceptance?JSON.stringify(task.acceptance,null,2):"Нет критериев","Разрешение":task.permissions?.approved?"Выдано":"Не выдано","Бюджет":task.budget?`${task.budget.model_calls} моделей / ${task.budget.tool_calls} инструментов`:"Не задан","Блокировки":card.blockers.map(value=>reasons[value]||value).join(" · ")||"—","Результат":task.result_id||"Не опубликован"};
    if(card.attempt)Object.assign(values,{"Попытка":`${card.attempt.id} · ${label(card.attempt.state)}`,"Исполнитель":card.attempt.instance_id,"Модель":card.attempt.profile_snapshot.model_id||card.attempt.profile_snapshot.model_ref,"Расход":`${card.attempt.spent.model_calls} моделей / ${card.attempt.spent.tool_calls} инструментов`,"Остановка":card.attempt.stop_confirmed?"Подтверждена":"Не подтверждена"});
    for(const [key,value] of Object.entries(values))dl.append(node("dt",key),node("dd",String(value)));body.append(dl);
    const actions=node("div",undefined,"task-actions");
    const options=[];
    if(task.state==="planned")options.push(["enqueue","Поставить в очередь"]);
    if(!["succeeded","failed","cancelled"].includes(task.state))options.push([task.paused?"resume":"pause",task.paused?"Продолжить":"Приостановить"],["cancel","Отменить задачу"]);
    if(["failed","retry_wait"].includes(task.state))options.push(["retry","Повторить"]);
    if(["succeeded","failed","cancelled"].includes(task.state))options.push(["reopen","Открыть заново"],["archive",task.archived?"Уже в архиве":"В архив"]);
    for(const [operation,title] of options){const button=node("button",title);button.disabled=operation==="archive"&&task.archived;button.onclick=()=>command(operation);actions.append(button);}body.append(actions);
    await loadHistory(id,false);
  }
  async function openTask(id) {
    selected=id;detail=null;historyLimit=50;historyComplete=false;$("#detailTitle").textContent="Загружаем…";$("#detailBody").replaceChildren();$("#taskHistory").replaceChildren();$("#detailError").textContent="";$("#commentText").value="";
    if(!$("#taskDetail").open)$("#taskDetail").showModal();await loadDetail(id);
  }
  async function loadHistory(id,more) {
    const request=++historyRequest,limit=historyLimit+(more?50:0),followTail=historyComplete;
    let after=0, events=[];
    do {
      const page=await api(`/api/tasks/${id}/events?limit=50&after=${after}`);
      events.push(...page.items);after=page.next_cursor;
    } while(after!==null&&(followTail||events.length<limit));
    if(request!==historyRequest||selected!==id||!$("#taskDetail").open)return;
    $("#taskHistory").replaceChildren();
    for(const event of events) {
      const row=node("article",undefined,"event-row");row.append(node("small",`#${event.sequence} · ${event.kind} · ${date(event.occurred_at)}`));
      if(event.data.text)row.append(node("p",event.data.text));
      else if(event.data.changes&&Object.keys(event.data.changes).length)row.append(node("p",JSON.stringify(event.data.changes)));
      $("#taskHistory").append(row);
    }
    historyLimit=Math.max(limit,events.length);historyComplete=after===null;historyAfter=after;$("#moreHistory").hidden=historyAfter===null;
  }
  async function command(operation) {
    if(!detail)return;const id=selected,revision=detail.task.revision;$("#detailError").textContent="";
    const buttons=[...$("#detailBody").querySelectorAll("button")];buttons.forEach(button=>button.disabled=true);
    try {const body={operation,expected_revision:revision};await api(`/api/tasks/${id}/commands`,{...body,idempotency_key:mutationKey("command:"+id,body)});pendingMutations.delete("command:"+id);await refresh();}
    catch(error) {if(selected===id)showError(error,"#detailError");await refresh();}
    finally {buttons.forEach(button=>button.disabled=false);}
  }
  async function listen() {
    try {
      const response=await fetch(`/api/tasks/events${delivery===null?"":`?after=${delivery}`}`,{headers:{"X-FlyCode-Token":token}});
      if(!response.ok)throw new Error("Не удалось подключить обновления");
      $("#connection").textContent="Обновления подключены";
      const reader=response.body.getReader(),decoder=new TextDecoder();let pending="";
      while(true){
        const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});
        let boundary;while((boundary=pending.indexOf("\n\n"))>=0){
          const block=pending.slice(0,boundary);pending=pending.slice(boundary+2);if(block.startsWith(":"))continue;
          const lines=block.split("\n"),id=lines.find(line=>line.startsWith("id: "))?.slice(4),kind=lines.find(line=>line.startsWith("event: "))?.slice(7);
          if(kind==="error")throw new Error("Обновления приостановлены: проверьте доступ к проекту");
          if(id!==undefined&&/^\d+$/.test(id))delivery=id;
          if(kind==="snapshot"||kind==="change")scheduleRefresh();
        }
      }
      throw new Error("Связь прервана");
    } catch(error) {$("#connection").textContent=`${error.message}. Переподключаемся…`;}
    streamTimer=setTimeout(listen,2000);
  }
  for(const [state,title] of Object.entries(names))if(!["idle","stopping","reconciling"].includes(state))$("#stateFilter").add(new Option(title,state));
  $("#filters").onsubmit=event=>event.preventDefault();$("#filters").oninput=event=>event.target.id==="archivedFilter"?refresh():render();
  for(const button of document.querySelectorAll("[data-view]"))button.onclick=()=>{view=button.dataset.view;for(const tab of document.querySelectorAll("[data-view]"))tab.setAttribute("aria-pressed",String(tab===button));render();};
  for(const button of document.querySelectorAll("[data-close]"))button.onclick=()=>document.getElementById(button.dataset.close).close();
  $("#taskDetail").onclose=()=>{selected=null;detail=null;detailRequest++;historyRequest++;};
  $("#newDraft").onclick=()=>{$("#draftError").textContent="";$("#draftDialog").showModal();};
  $("#draftForm").onsubmit=async event=>{
    event.preventDefault();const submit=event.submitter;submit.disabled=true;$("#draftError").textContent="";
    try {
      const selector=$("#draftProject").value;
      const project=projects.find(value=>value.selector===selector);if(!project)throw new Error("Выберите доступный проект");
      const id=project.project_id||(await api("/api/tasks/projects",{selector})).project_id;
      const body={project_id:id,task:{title:$("#draftName").value,objective:$("#draftObjective").value,priority:Number($("#draftPriority").value)}};
      const card=await api("/api/tasks",{...body,idempotency_key:mutationKey("draft",body)});pendingMutations.delete("draft");
      $("#draftDialog").close();$("#draftForm").reset();await refresh();await openTask(card.task.id);
    }catch(error){showError(error,"#draftError");}finally{submit.disabled=false;}
  };
  $("#commentForm").onsubmit=async event=>{
    event.preventDefault();const id=selected,text=$("#commentText").value,submit=event.submitter;if(!id||!text.trim())return;submit.disabled=true;
    try {const body={text};await api(`/api/tasks/${id}/comments`,{...body,idempotency_key:mutationKey("comment:"+id,body)});pendingMutations.delete("comment:"+id);if(selected===id){$("#detailError").textContent="";$("#commentText").value="";await loadHistory(id,false);}}
    catch(error){showError(error,"#detailError");}finally{submit.disabled=false;}
  };
  $("#moreHistory").onclick=()=>loadHistory(selected,true).catch(error=>showError(error,"#detailError"));
  window.addEventListener("pagehide",()=>{clearTimeout(streamTimer);clearTimeout(refreshTimer);});
  (async()=>{try{token=(await(await fetch("/api/bootstrap")).json()).token;await refresh();$("#newDraft").disabled=false;listen();}catch(error){showError(error);$("#connection").textContent="Не удалось загрузить задачи";}})();
})();
