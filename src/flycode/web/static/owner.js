"use strict";
(() => {
  const $=selector=>document.querySelector(selector),dialog=$("#ownerDialog");
  const states={planned:"Черновик",ready:"В очереди",assigned:"Назначена",running:"В работе",awaiting_review:"На проверке",succeeded:"Готово",blocked:"Ожидание",retry_wait:"Ожидает повтора",failed:"Ошибка",cancelled:"Отменена"};
  const blockers={missing_objective:"Добавьте описание результата",missing_criteria:"Выберите критерии",missing_permission:"Требуется разрешение",missing_budget:"Задайте бюджет",missing_agent_profile:"Выберите профиль исполнителя",missing_source_binding:"Подготовьте папку",waiting_children:"Ожидает дочерние задачи",child_review_failed:"Проверка дочерней задачи не пройдена",child_failed:"Дочерняя задача не завершена",paused:"Приостановлена"};
  const stages={baseline:"До изменений",target:"После изменений",regression:"Проверка регрессий",integration:"Общая проверка"};
  let context=null,request=0,fieldId=0;
  function node(tag,text,className){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
  function current(saved,generation){return context===saved&&request===generation&&dialog.open;}
  function errorMessage(error){return error.code==="revision"?"План изменился. Обновите его и проверьте условия.":error.code==="scope"||error.code==="project_changed"?"Папка проекта недоступна или изменилась. Сохранённые кнопки остановки остаются доступны.":error.message;}
  const storageKey=saved=>`flycode-owner-v1:${saved.projectId}:${saved.workflowId}`;
  function pending(saved){
    try{const value=JSON.parse(sessionStorage.getItem(storageKey(saved))||"null");return value?.projectId===saved.projectId&&value?.workflowId===saved.workflowId&&typeof value.body==="string"?value:null;}catch{return null;}
  }
  function forget(saved,record){if(pending(saved)?.body===record.body)sessionStorage.removeItem(storageKey(saved));}
  function sync(saved){
    if(context!==saved||!dialog.open)return;
    const record=pending(saved),locked=Boolean(record||saved.busy||saved.loading);
    $("#ownerRetry").hidden=$("#ownerDiscard").hidden=!record;
    $("#ownerRetry").disabled=Boolean(saved.busy||saved.loading);$("#ownerDiscard").disabled=Boolean(saved.busy||saved.loading);
    $("#ownerReload").disabled=Boolean(saved.loading);
    for(const update of saved.controls||[])update(locked);
  }
  function field(title,input){
    const box=node("div",undefined,"owner-field"),label=node("label",title);input.id=`owner-field-${++fieldId}`;label.htmlFor=input.id;
    box.append(...(input.type==="checkbox"?[input,label]:[label,input]));return box;
  }
  function number(value,min,max){const input=node("input");input.type="number";input.min=min;input.max=max;input.step=1;input.value=value;input.required=true;return input;}
  function select(options,placeholder="Выберите"){
    const input=node("select");input.required=true;input.add(new Option(placeholder,""));
    for(const [value,title] of options)input.add(new Option(title,value));
    if(options.length===1)input.value=options[0][0];return input;
  }
  async function submit(saved,body){
    if(saved.busy||pending(saved))return;
    const record={projectId:saved.projectId,workflowId:saved.workflowId,body:JSON.stringify({...body,idempotency_key:crypto.randomUUID()})};
    try{sessionStorage.setItem(storageKey(saved),JSON.stringify(record));}
    catch{$("#ownerError").textContent="Не удалось сохранить запрос для повтора. Проверьте доступ к памяти браузера.";$("#ownerError").hidden=false;return;}
    await send(saved,record);
  }
  async function send(saved,record){
    if(saved.busy)return;saved.busy=true;const generation=request;sync(saved);$("#ownerError").hidden=true;
    try{
      const outcome=await saved.api("/api/tasks/owner",JSON.parse(record.body));forget(saved,record);
      if(!current(saved,generation))return;
      const messages={prepared:"Папка подготовлена. Выполнение ещё не разрешено.",approved:"План разрешён. Выполнение продолжит служба задач.",replanned:"Изменения плана приняты. Расходы и срок сохранены."};
      $("#ownerReceipt").textContent=messages[outcome.result.kind];await load(saved);await saved.refresh();
    }catch(error){
      if(!error.result?.partial&&["revision","idempotency","invalid_fields","scope","project_changed","verification_profile_changed","verification_profile_scope"].includes(error.code))forget(saved,record);
      if(current(saved,generation)){
        $("#ownerError").textContent=error.result?.partial?"Подготовлена часть папок. Повторите сохранённый запрос, чтобы продолжить.":error.status?errorMessage(error):"Ответ не получен. Сохранён точный запрос для повтора.";
        $("#ownerError").hidden=false;
        if(["scope","project_changed"].includes(error.code)){$("#ownerBody").replaceChildren();stopRows(saved,(saved.preview?.tasks||[saved.card.task]).map(task=>({id:task.id,revision:task.revision,state:task.state,title:`Задача ${task.id.slice(0,6)}`})));}
      }
    }finally{saved.busy=false;if(context?.projectId===saved.projectId&&context?.workflowId===saved.workflowId)sync(context);}
  }
  function wireForm(saved,form,button,ready,body){
    saved.controls.push(locked=>{for(const input of form.elements)input.disabled=locked;button.disabled=locked||!ready();});
    form.oninput=()=>sync(saved);
    form.onsubmit=event=>{event.preventDefault();if(!form.reportValidity()||!ready())return;
      try{submit(saved,body());}catch(error){$("#ownerError").textContent=error.message;$("#ownerError").hidden=false;}};
  }
  function prepareForm(saved,preview,writers){
    const form=node("form",undefined,"owner-form"),selection=new Map();form.append(node("h3","1. Подготовка папки"));
    form.append(node("p","Выберите исполнителей и контроллер. Это закрепит исходную папку; попытки начнутся после отдельного разрешения."));
    for(const task of writers){const input=node("input");input.type="checkbox";input.checked=!task.binding_id;input.dataset.ownerPrepare=task.id;selection.set(task.id,input);form.append(field(task.title+(task.binding_id?" · папка уже закреплена":" · папка не подготовлена"),input));}
    const brain=select([["functional","Functional Fly"],["heuristic_fsm","Эвристический контроллер"]]),seed=number(0,0,4294967295);
    form.append(field("Контроллер подготовки",brain),field("Начальное состояние контроллера",seed));
    const button=node("button","Подготовить папку");button.type="submit";form.append(button);
    wireForm(saved,form,button,()=>Boolean(brain.value)&&[...selection.values()].some(value=>value.checked),()=>({
      operation:"prepare",project_id:preview.project_id,project_revision:preview.project_revision,
      workflow_id:preview.workflow_id,expected_revision:preview.workflow_revision,
      workers:Object.fromEntries(writers.filter(task=>selection.get(task.id).checked).map(task=>[task.id,{
        expected_revision:task.revision,expected_binding_id:task.binding_id,brain:brain.value,seed:Number(seed.value)}]))}));
    return form;
  }
  function approvalForm(saved,preview,writers,root){
    const form=node("form",undefined,"owner-form"),reviews=preview.tasks.filter(task=>task.kind==="review"),pairs=new Map(),profiles=new Map(),policies=new Map();
    form.append(node("h3","2. Разрешение выполнения"),node("p",`План объединяет ${writers.length} ветви, независимую проверку каждой и общую интеграцию. Условия и бюджет каждой задачи сохранены на сервере.`));
    const integrator=select(reviews.filter(task=>task.dependencies.length).map(task=>[task.id,task.title]));
    form.append(field("Общая интеграция",integrator));
    for(const task of writers){
      const row=node("fieldset"),review=select(reviews.filter(value=>!value.dependencies.length&&value.criteria_id===task.criteria_id&&value.criteria_version===task.criteria_version).map(value=>[value.id,value.title]));
      row.append(node("legend",task.title));pairs.set(task.id,review);form.append(row);row.append(field("Независимая проверка",review));
      const retries=select([["0","Без повторов"],["1","Один повтор после замечаний"],["2","Два повтора после замечаний"]]);retries.value="0";
      for(const option of retries.options)if(Number(option.value)>0){const count=Number(option.value)+1+Number(task.id===root.id);option.disabled=!task.budget||Math.min(task.budget.model_calls,task.budget.tool_calls)<count;}
      row.append(field("Исправление после проверки",retries));
      const budgets=node("div",undefined,"owner-budget-fields"),model=number(1,1,60),tools=number(1,1,120),checks=number(4,1,120);budgets.hidden=true;
      budgets.append(field("Вызовов модели на попытку",model),field("Инструментов на попытку",tools),field("Инструментов на проверку",checks));row.append(budgets);
      retries.onchange=()=>{budgets.hidden=retries.value==="0";sync(saved);};policies.set(task.id,{retries,model,tools,checks});
    }
    const criteria=node("details");criteria.append(node("summary","Критерии и бюджет этапов"));
    for(const task of preview.tasks){
      const options=preview.profiles.filter(profile=>profile.available&&profile.criteria_id===task.criteria_id&&profile.criteria_version===task.criteria_version);
      const input=select(options.map(profile=>[profile.id,`${profile.title} · повторных прогонов: ${profile.reruns}`]));profiles.set(task.id,input);
      criteria.append(field(`Проверка · ${task.title} · ${task.id.slice(0,6)}`,input));
      criteria.append(node("p",task.budget?`Лимит: ${task.budget.model_calls} вызовов модели / ${task.budget.tool_calls} инструментов.`:"Бюджет не задан."));
    }
    form.append(criteria);
    const steps=number(1,1,Math.max(1,Math.min(root.budget?.model_calls||0,root.budget?.tool_calls||0)-1));form.append(field("Шагов родителя перед ожиданием ветвей",steps));
    const confirm=node("input");confirm.type="checkbox";confirm.required=true;form.append(field("Разрешаю выполнение по этому плану",confirm));
    const button=node("button","Разрешить выполнение","primary");button.type="submit";form.append(button);
    const complete=preview.tasks.every(task=>task.criteria_id&&task.permission_approved&&task.budget&&task.profile_ids.length&&!task.blockers.includes("missing_objective"));
    wireForm(saved,form,button,()=>confirm.checked&&complete&&Boolean(integrator.value)&&[...pairs.values(),...profiles.values()].every(value=>value.value),()=>{
      const selected=Object.fromEntries([...profiles].map(([id,input])=>[id,preview.profiles.find(profile=>profile.id===input.value)]));
      const reruns=new Set(Object.values(selected).map(value=>value.reruns));if(reruns.size!==1)throw new Error("Выберите критерии с одинаковым числом повторных прогонов.");
      const retry_policies={};for(const [id,policy] of policies)if(Number(policy.retries.value))retry_policies[id]={max_retries:Number(policy.retries.value),writer_budget:{model_calls:Number(policy.model.value),tool_calls:Number(policy.tools.value)},reviewer_budget:{model_calls:0,tool_calls:Number(policy.checks.value)}};
      return {operation:"approve_parent",project_id:preview.project_id,project_revision:preview.project_revision,
        workflow_id:preview.workflow_id,expected_revision:preview.workflow_revision,
        task_revisions:Object.fromEntries(preview.tasks.map(task=>[task.id,task.revision])),pairs:Object.fromEntries([...pairs].map(([id,input])=>[id,input.value])),integrator_id:integrator.value,
        profiles:Object.fromEntries(Object.entries(selected).map(([id,profile])=>[id,{id:profile.id,version:profile.version}])),preparation_steps:Number(steps.value),reruns:[...reruns][0],retry_policies};
    });
    return form;
  }
  function replanForm(saved,preview,catalogue,writers,root){
    const form=node("form",undefined,"owner-form"),edits=new Map(),additions=[];
    let nextBranch=0;
    form.append(node("h3","Изменение остановленного плана"),node("p","Изменения доступны, когда родитель ожидает ветви, а все попытки подтверждённо остановлены. Уже потраченный бюджет и срок сохраняются. Сервер повторно проверит состояние всех попыток."));
    const children=writers.filter(task=>task.id!==root.id),full=id=>saved.fullTasks.get(id);
    const check=(title,parent)=>{const input=node("input");input.type="checkbox";parent.append(field(title,input));return input;};
    const text=(value,max,area=false)=>{const input=node(area?"textarea":"input");input.value=value;input.maxLength=max;input.required=true;return input;};
    const dependencies=(parent,title,exclude)=>{
      const box=node("details"),options=new Map();box.append(node("summary","Зависимости"));
      for(const task of children)if(task.id!==exclude)options.set(task.id,check(`${title} · ${task.title} ${task.id.slice(0,6)}`,box));
      parent.append(box);return options;
    };
    const criteria=(parent,title,task)=>{
      const options=preview.profiles.filter(profile=>profile.available&&(!task||profile.criteria_id===task.criteria_id&&profile.criteria_version===task.criteria_version));
      const input=select(options.filter(profile=>catalogue.profiles.some(value=>value.id===profile.id&&value.scope==="acceptance-contract")).map(profile=>[profile.id,profile.title]));
      parent.append(field(title,input));return input;
    };
    const profiles=new Map(),registered=node("details");registered.append(node("summary","Зарегистрированные критерии текущих задач"));
    for(const task of preview.tasks)profiles.set(task.id,criteria(registered,`Проверка · ${task.title} · ${task.id.slice(0,6)}`,task));
    form.append(registered);
    for(const task of children){
      if(["succeeded","cancelled"].includes(task.state))continue;
      const row=node("fieldset"),caption=`${task.title} ${task.id.slice(0,6)}`,enabled=check(`Изменить задачу ${caption}`,row),objective=text(full(task.id).objective,20000,true);
      row.append(field(`Результат · ${caption}`,objective));const deps=dependencies(row,`Зависимость задачи ${caption}`,task.id);
      for(const [id,input] of deps)input.checked=task.dependencies.includes(id);
      row.append(node("p","Изменится только выбранная задача и её проверка; исходные доказательства сохраняются."));form.append(row);edits.set(task.id,{row,task,enabled,objective,deps});
    }
    const added=node("div"),add=node("button","Добавить ветвь");add.type="button";form.append(added,add);
    const confirm=check("Разрешаю изменения плана",form),button=node("button","Применить изменения плана","primary");button.type="submit";form.append(button);
    const changed=()=>[...edits.values()].some(edit=>edit.enabled.checked)||additions.length;
    add.onclick=()=>{
      if(additions.length>=Math.min(30,Math.floor((64-preview.tasks.length)/2)))return;
      const branchNumber=++nextBranch;
      const row=node("fieldset"),template=select(writers.map(task=>[task.id,`${task.title} ${task.id.slice(0,6)}`])),reviewer=select([]),profile=criteria(row,"Критерии новой ветви"),title=text("",200),objective=text("",20000,true);
      row.prepend(node("legend",`Новая ветвь ${branchNumber}`));row.append(field("Название новой ветви",title),field("Результат новой ветви",objective),field("Шаблон исполнителя",template),field("Шаблон независимой проверки",reviewer));
      const permission=node("p");row.append(permission);
      template.onchange=()=>{
        const task=preview.tasks.find(task=>task.id===template.value);reviewer.replaceChildren(new Option("Выберите",""));
        const matches=preview.tasks.filter(value=>value.kind==="review"&&!value.dependencies.length&&value.criteria_id===task?.criteria_id&&value.criteria_version===task?.criteria_version);
        for(const item of matches)reviewer.add(new Option(`${item.title} ${item.id.slice(0,6)}`,item.id));if(matches.length===1)reviewer.value=matches[0].id;
        const allowed=task&&full(task.id).permissions;
        permission.textContent=allowed?`Инструменты: ${allowed.tools.join(", ")}. Чтение: ${allowed.read_scopes.join(", ")}. Запись: ${allowed.write_scopes.join(", ")||"не разрешена"}. Разрешения берутся из выбранного шаблона.`:"Выберите шаблон, чтобы проверить разрешения.";sync(saved);
      };
      const reviewTitle=text("Review additional branch",200),reviewObjective=text("Review the additional branch under the selected registered criteria.",20000,true);
      row.append(field("Название независимой проверки",reviewTitle),field("Результат независимой проверки",reviewObjective));
      const budgets=node("div",undefined,"owner-budget-fields"),model=number(1,1,60),tools=number(1,1,120),checks=number(4,1,120);
      budgets.append(field("Вызовов модели новой ветви",model),field("Инструментов новой ветви",tools),field("Инструментов независимой проверки",checks));row.append(budgets);
      const deps=dependencies(row,"Зависимость новой ветви"),prior=new Map();
      for(const previous of additions)prior.set(previous,check(`Зависимость от новой ветви ${previous.number}`,row));
      const remove=node("button","Убрать ветвь");remove.type="button";row.append(remove);added.append(row);
      const record={number:branchNumber,row,title,objective,template,reviewer,profile,reviewTitle,reviewObjective,model,tools,checks,deps,prior};additions.push(record);
      for(const edit of edits.values())edit.deps.set(record,check(`Зависимость задачи ${edit.task.title} ${edit.task.id.slice(0,6)} от новой ветви ${branchNumber}`,edit.row));
      remove.onclick=()=>{additions.splice(additions.indexOf(record),1);row.remove();for(const choices of [...additions.map(other=>other.prior),...[...edits.values()].map(edit=>edit.deps)]){const input=choices.get(record);if(input){input.parentElement.remove();choices.delete(record);}}sync(saved);};sync(saved);
    };
    const dependenciesValue=options=>[...options].filter(([,input])=>input.checked).map(([id])=>typeof id==="string"?id:`new:${additions.indexOf(id)}`);
    const profileRefs=()=>Object.fromEntries([...profiles].map(([id,input])=>{const profile=preview.profiles.find(value=>value.id===input.value);return [id,{id:profile.id,version:profile.version}];}));
    wireForm(saved,form,button,()=>confirm.checked&&changed()&&[...profiles.values()].every(input=>input.value)&&additions.every(item=>item.template.value&&item.reviewer.value&&item.profile.value),()=>{
      const changes={};for(const [id,edit] of edits)if(edit.enabled.checked){
        const deps=dependenciesValue(edit.deps),values={};if(edit.objective.value!==full(id).objective)values.objective=edit.objective.value;
        if(JSON.stringify(deps)!==JSON.stringify(edit.task.dependencies))values.dependencies=deps;if(!Object.keys(values).length)throw new Error("Выбранная задача не изменена.");changes[id]=values;
      }
      const clone=(id,values)=>{const task=full(id);return {kind:task.kind,permissions:task.permissions,capabilities:task.capabilities,allowed_profile_ids:task.allowed_profile_ids,priority:task.priority,parent_id:root.id,...values};};
      const new_branches=additions.map(item=>{
        const profile=catalogue.profiles.find(value=>value.id===item.profile.value),dependencies=[...dependenciesValue(item.deps),...[...item.prior].filter(([,input])=>input.checked).map(([ref])=>`new:${additions.indexOf(ref)}`)];
        const acceptance=profile.acceptance;
        return {template_writer_id:item.template.value,profile:{id:profile.id,version:profile.version},
          writer:clone(item.template.value,{title:item.title.value,objective:item.objective.value,acceptance,dependencies,budget:{model_calls:Number(item.model.value),tool_calls:Number(item.tools.value)}}),
          reviewer:clone(item.reviewer.value,{title:item.reviewTitle.value,objective:item.reviewObjective.value,acceptance,dependencies:[],budget:{model_calls:0,tool_calls:Number(item.checks.value)}})};
      });
      return {operation:"replan",project_id:preview.project_id,project_revision:preview.project_revision,pipeline_id:preview.pipeline_id,
        expected_revision:preview.workflow_revision,task_revisions:Object.fromEntries(preview.tasks.map(task=>[task.id,task.revision])),changes,new_branches,profiles:profileRefs()};
    });return form;
  }
  function stopRows(saved,tasks){
    const box=$("#ownerStops");box.replaceChildren();saved.stopUpdates=new Map();
    const active=tasks.filter(task=>!["succeeded","failed","cancelled"].includes(task.state));
    if(!active.length)return;
    const disclosure=node("details"),list=node("div");disclosure.append(node("summary","Приостановить или отменить задачу"));
    for(const task of active){
      const row=node("div",undefined,"owner-stop-row"),message=node("p"),buttons=[];row.append(node("strong",task.title));
      const update=()=>{const state=saved.stopState(task.id);for(const button of buttons)button.disabled=Boolean(state.pending?.busy);message.textContent=state.pending?.busy?"Отправляем запрос остановки…":state.message?.text||"";};
      for(const [operation,title] of [["pause","Приостановить"],["cancel","Отменить"]]){
        const button=node("button",title);button.type="button";button.dataset.ownerStop=task.id;button.dataset.operation=operation;
        button.setAttribute("aria-label",`${title} ${task.title} ${task.id.slice(0,6)}`);
        buttons.push(button);
        button.onclick=async()=>{
          const state=saved.stopState(task.id),revision=state.handle?.revision||task.revision;
          try{await saved.stopCommand(task.id,operation,revision);}
          catch(error){message.textContent=error.message;}
          finally{if(context===saved&&dialog.open)update();}
        };
        row.append(button);
      }
      saved.stopUpdates.set(task.id,update);update();row.append(message);list.append(row);
    }
    disclosure.append(list);box.append(disclosure);
  }
  function render(saved,preview,catalogue){
    const body=$("#ownerBody");body.replaceChildren();saved.controls=[];
    body.append(node("p","Просмотр показывает условия общего плана. Подготовка папки и разрешение выполнения — отдельные команды.","owner-intro"));
    const writers=preview.tasks.filter(task=>task.kind!=="review"&&task.profile_ids.length),root=preview.tasks.find(task=>task.parent_id===null);
    if(!preview.pipeline_id){if(writers.length)body.append(prepareForm(saved,preview,writers));if(root&&writers.length>=2)body.append(approvalForm(saved,preview,writers,root));}
    else {body.append(node("p",`План разрешён · версия графа ${preview.graph_revision}. Привязки закреплены выбранным планом.`));
      if(root?.state==="blocked"&&root.blockers.some(reason=>["waiting_children","child_review_failed","child_failed"].includes(reason))&&!preview.tasks.some(task=>saved.fullTasks.get(task.id)?.paused)&&preview.graph_revision<2)body.append(replanForm(saved,preview,catalogue,writers,root));}
    const tasks=node("details");tasks.append(node("summary",`Задачи и условия (${preview.tasks.length})`));
    for(const task of preview.tasks){
      const box=node("article",undefined,"owner-task");box.dataset.ownerTask=task.id;
      box.append(node("h4",task.title),node("p",`${states[task.state]||task.state} · версия ${task.revision}`));
      box.append(node("p",task.budget?`Бюджет: ${task.budget.model_calls} вызовов модели / ${task.budget.tool_calls} инструментов.`:"Бюджет не задан."));
      box.append(node("p",task.permission_approved?"Разрешение выдано":"Требуется разрешение"));
      for(const reason of task.blockers)box.append(node("p",blockers[reason]||reason,"owner-blocker"));
      if(task.criteria_id)box.append(node("p",`Критерии: ${task.criteria_id} · версия ${task.criteria_version}`));
      tasks.append(box);
    }
    body.append(tasks);
    const criteria=node("details"),list=node("div");criteria.append(node("summary","Зарегистрированные проверки"));
    for(const profile of preview.profiles){
      const item=node("article",undefined,"owner-task");item.append(node("h4",profile.title));
      item.append(node("p",profile.available?`Доступна · дополнительных прогонов: ${profile.reruns}`:"Недоступна. Обновите зарегистрированные проверки."));
      const full=catalogue.profiles.find(value=>value.id===profile.id);
      for(const criterion of full?.acceptance?.criteria||[])item.append(node("p",criterion.title||`Обязательная проверка: ${criterion.id}`));
      for(const check of full?.checks||[])item.append(node("p",`${stages[check.stage]||check.stage}: ${(check.targets||[]).join(", ")}`));
      list.append(item);
    }
    criteria.append(list);body.append(criteria);
    stopRows(saved,preview.tasks);
  }
  async function load(saved){
    const generation=++request;saved.loading=true;$("#ownerStatus").textContent="Загружаем план…";$("#ownerError").hidden=true;sync(saved);
    $("#ownerBody").replaceChildren();$("#ownerReload").disabled=true;
    try{
      const [outcome,catalogue]=await Promise.all([
        saved.api(`/api/tasks/owner/${saved.workflowId}?project_id=${saved.projectId}`),
        saved.api(`/api/verification/catalogue?project_id=${saved.projectId}`)]);
      if(!current(saved,generation))return;
      saved.fullTasks=new Map();
      if(outcome.result.pipeline_id){const cards=await Promise.all(outcome.result.tasks.map(task=>saved.api(`/api/tasks/${task.id}`)));if(!current(saved,generation))return;for(const card of cards)saved.fullTasks.set(card.task.id,card.task);}
      saved.preview=outcome.result;saved.catalogue=catalogue;render(saved,outcome.result,catalogue);$("#ownerStatus").textContent="План загружен";return true;
    }catch(error){if(current(saved,generation)){
      $("#ownerError").textContent=errorMessage(error);$("#ownerError").hidden=false;$("#ownerStatus").textContent="План недоступен";
      if(["scope","project_changed"].includes(error.code))stopRows(saved,(saved.preview?.tasks||[saved.card.task]).map(task=>({id:task.id,revision:task.revision,state:task.state,title:`Задача ${task.id.slice(0,6)}`})));
    }}
    finally{if(current(saved,generation)){saved.loading=false;sync(saved);}}
  }
  document.addEventListener("flycode-owner-open",event=>{
    const saved={...event.detail,projectId:event.detail.card.task.project_id,workflowId:event.detail.card.task.workflow_id};context=saved;
    stopRows(saved,[saved.card.task]);$("#ownerReceipt").textContent=pending(saved)?"Сохранён запрос без подтверждённого ответа. Его можно повторить с прежними версиями.":"";if(!dialog.open)dialog.showModal();load(saved);
  });
  document.addEventListener("flycode-stop-change",event=>{if(context&&dialog.open)context.stopUpdates?.get(event.detail.taskId)?.();});
  $("#ownerReload").onclick=()=>{if(context)load(context);};
  $("#ownerClose").onclick=()=>dialog.close();
  $("#ownerRetry").onclick=()=>{if(context){const record=pending(context);if(record)send(context,record);else load(context);}};
  $("#ownerDiscard").onclick=async()=>{const saved=context,record=saved&&pending(saved);if(record&&await load(saved)&&context===saved){forget(saved,record);$("#ownerReceipt").textContent="Текущее состояние обновлено. Проверьте условия перед новой командой.";sync(saved);}};
  dialog.addEventListener("close",()=>{context=null;request++;$("#ownerBody").replaceChildren();$("#ownerStops").replaceChildren();});
})();
