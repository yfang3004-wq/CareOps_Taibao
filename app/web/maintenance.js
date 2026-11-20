'use strict';
/* 独立公共维修工作台：只使用服务端投影和合法动作，保留原提交回执／草稿。 */
const Maintenance={
 pages:new Set(['maint-work','maint-locations','maint-history','maint-drafts','detail']),
 isRole(){return Boolean(S.user&&['maint_executor','maint_supervisor'].includes(S.user.role));},
 actions(item=S.current){return Array.isArray(item?.allowed_actions)?item.allowed_actions:[];},
 listActions:[],filter:'',
 reset(){this.listActions=[];this.filter='';},
 stage(i){return CareOpsMaintenanceStatus.label(i);},
 location(i){return i.subject?.location||i.location||'位置未登记';},
 shell(){
  if(!this.pages.has(S.page))S.page='maint-work';
  const menus=[['maint-work','待维修'],['maint-locations','位置列表'],['maint-history','维修记录']];
  const title=S.page==='detail'?'维修详情':S.page==='maint-drafts'?'恢复维修草稿':menus.find(x=>x[0]===S.page)?.[1];
  $('app').innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<nav>${menus.map(([p,n])=>btn('nav',n,`data-page="${p}"`,S.page===p?'active':'')).join('')}</nav><div class="side-bottom"><p class="ui-facility-name">${esc(S.facility.name)}</p><span class="sub">${esc(S.facility.timezone)} · 维修专岗</span></div></aside><main class="main"><header class="top"><div><p class="overline">PUBLIC MAINTENANCE</p><h1>${esc(title)}</h1></div><div class="actions ui-toolbar" role="toolbar" focusgroup="toolbar wrap" aria-label="维修工作台操作"><div class="ui-btn-group" role="group" aria-label="当前身份"><span class="chip">${esc(S.user.display_name)} · ${esc(labels[S.user.role])}</span>${btn('demo-switch-open','切换身份','','small')}</div><span class="ui-toolbar-sep" role="separator" aria-orientation="vertical"></span><div class="ui-btn-group" role="group" aria-label="会话安全">${btn('password','改密','','small')}${btn('logout','退出','','small')}</div></div></header><div data-pending-placeholder></div><section id="content"><p class="loading">正在读取授权维修…</p></section></main></div>`;
 },
 async readList(){
  const r=await api('/role-workbenches/maintenance');S.revision=r.revision;S.flows=r.items;
  this.listActions=Array.isArray(r.allowed_actions)?r.allowed_actions:[];return r;
 },
 async readDetail(id){
  const r=await api('/workflows/'+encodeURIComponent(id));S.revision=r.revision;S.current=r.item;return r;
 },
 async render(){
  if(S.page==='maint-drafts'){await renderDrafts();return;}
  if(S.page==='detail'){await this.readDetail(S.current.id);this.detail();return;}
  const r=await this.readList();
  const actions=`${S.page==='maint-history'?btn('maint-drafts','恢复我的草稿'):''}${this.listActions.includes('start')?btn('maint-create','登记公共维修','','primary'):''}${btn('refresh','刷新')}`;
  if(S.page==='maint-locations'){
   const places=Array.isArray(S.user.units)?S.user.units.filter(x=>x&&x!=='*'):[];
   $('content').innerHTML=`<section class="panel"><h2>已授权位置列表</h2><p class="notice">暂无已确认平面图与稳定空间关联；下列是精确位置文本，不推断楼层布局或房间床位。</p>${places.map(p=>`<article class="record"><h3>${esc(p)}</h3><p class="sub">当前可见已登记维修 ${S.flows.filter(i=>this.location(i)===p).length} 件</p>${S.flows.filter(i=>this.location(i)===p).map(i=>btn('maint-open',i.title,`data-id="${esc(i.id)}"`,'link')).join('')}</article>`).join('')||'<p class="empty">没有已授权位置，请联系授权负责人。</p>'}${actions}</section>`;return;
  }
  const history=S.page==='maint-history';
  const items=S.flows.filter(i=>(history||i.status!=='closed')&&(!this.filter||[i.title,this.location(i),i.owner,...(i.tasks||[]).map(t=>t.owner)].join(' ').includes(this.filter)));
  const stageCounts=Object.fromEntries(CareOpsMaintenanceStatus.stages.map(x=>[x.label,0]));S.flows.forEach(i=>{stageCounts[this.stage(i)]++;});
  $('content').innerHTML=`${history?'':`<div class="maint-summary" aria-label="授权范围阶段计数"><span class="muted">当前授权范围</span>${Object.entries(stageCounts).map(([k,v])=>`<span class="repair-status-pill">${esc(k)} ${v}</span>`).join('')}</div>`}<section class="panel"><div class="panel-head"><div><h2>${history?'维修历史与当前记录':'本人／授权范围维修'}</h2><p class="sub">只统计当前服务端可见记录；暂缓不是已修好，不自动生成等待类别或跟进安排。</p></div><div class="actions">${actions}</div></div><label class="maintenance-search">查找位置、事项或账户<input class="search" id="maint-filter" value="${esc(this.filter)}" maxlength="200"></label><p class="sub">数据库版本 ${esc(r.revision)} · 位置台账关联待确认</p><div class="table-wrap maintenance-table-wrap"><table class="maintenance-table"><thead><tr><th>位置／故障</th><th>维修状态</th><th>执行者／整单负责人</th><th>期限</th><th>轮次</th></tr></thead><tbody>${items.map(i=>`<tr><td data-label="位置／故障"><div class="maintenance-cell">${btn('maint-open',i.title,`data-id="${esc(i.id)}"`,'link')}<div>${esc(this.location(i))}</div></div></td><td data-label="维修状态"><div class="maintenance-cell"><span class="repair-status-pill" data-stage="${CareOpsMaintenanceStatus.key(i)}">${esc(this.stage(i))}</span>${CareOpsMaintenanceStatus.note(i)?`<small class="sub">${esc(CareOpsMaintenanceStatus.note(i))}</small>`:''}</div></td><td data-label="执行／整单"><div class="maintenance-cell">${esc(who(i.tasks?.[0]?.owner))}<br><small>整单：${esc(who(i.owner))}</small></div></td><td data-label="约定期限"><div class="maintenance-cell">${esc(i.tasks?.[0]?.due_at||i.subject?.due_at||'未约定')}</div></td><td data-label="轮次"><div class="maintenance-cell">${esc(i.cycle)}</div></td></tr>`).join('')||'<tr><td colspan="5" class="maintenance-empty"><p class="empty">当前范围／筛选下没有已登记事项。</p></td></tr>'}</tbody></table></div></section>`;
 },
 // 历史展示只使用投影记录；按来源分组，不推断现场发生顺序。
 history(i){
  const groups=[
   ['核验依据',i.evidence||[],e=>({title:`第${e.cycle}轮 · ${e.kind==='reference'?'来源引用':'核验说明'}`,body:e.value,actor:e.actor,at:e.at})],
   ['复核记录',i.reviews||[],v=>({title:`第${v.cycle}轮 · ${v.scope==='task'?'检查修理结果':'验收维修'} · ${{approve:'通过',reject:'退回',waive:'免做'}[v.decision]||v.decision}`,body:v.reason,actor:v.actor,at:v.at})],
   ['责任安排',i.responsibility_events||[],v=>({title:`第${v.cycle??'未知'}轮 · ${who(v.from_owner)} → ${who(v.to_owner)}`,body:v.reason,at:v.at,actor:v.actor})]
  ];
  return groups.map(([title,rows,map])=>`<section class="maint-history"><h3>${esc(title)}</h3>${rows.map(row=>{const e=map(row);return `<article class="timeline-item"><p class="label">${esc(e.at||'时间未登记')} · ${esc(who(e.actor))}</p><strong>${esc(e.title)}</strong><p class="break">${esc(e.body||'未登记')}</p></article>`;}).join('')||`<p class="sub">暂无${esc(title)}。</p>`}</section>`).join('');
 },
 detail(){
  const i=S.current,acts=this.actions(i),task=i.tasks?.[0];
  const taskActions=[['start_task',task?.status==='暂缓'?'继续维修':'接单并开始'],['submit_task','登记修理结果'],['set_task_exception','暂时不能处理'],['add_evidence','补充核验依据'],['review_task','核对维修结果'],['assign_task','分派维修'],['waive_task','主管批准免做']];
  const endActions=[['submit_workflow','提交验收'],['review_workflow','确认验收'],['reopen_workflow','重新处理']];
  const buttons=taskActions.filter(([a])=>acts.includes(a)&&task).map(([a,n])=>btn('maint-command',n,`data-type="${a}" data-task="${esc(task.task_id)}"`,['start_task','submit_task','review_task'].includes(a)?'primary':'')).join('')+endActions.filter(([a])=>acts.includes(a)).map(([a,n])=>btn('maint-command',n,`data-type="${a}"`,'primary')).join('');
  $('content').innerHTML=`<div class="actions maint-page-head">${btn('nav','返回待维修','data-page="maint-work"')}${btn('refresh','刷新')}</div>
  <section class="panel"><div class="panel-head"><div><p class="label">${esc(i.id)} · 第 ${esc(i.cycle)} 轮</p><h2>${esc(i.title)}</h2><p class="sub">${esc(this.location(i))}</p></div><span class="repair-status-pill" data-stage="${CareOpsMaintenanceStatus.key(i)}">${esc(this.stage(i))}</span></div>
   ${CareOpsMaintenanceStatus.progress(i,esc)}${CareOpsMaintenanceStatus.note(i)?`<p class="repair-stage-note">${esc(CareOpsMaintenanceStatus.note(i))}</p>`:''}
   <div class="maint-next"><h3>当前账户的下一步</h3><p class="sub">当前只显示可办理的动作。维修人员报告已修好后进入“待验收”，验收通过才算已完成。等待配件或退回继续归入“处理中”。</p><div class="actions">${buttons||'<p class="sub">当前账户没有可执行动作；可刷新或联系负责岗。</p>'}</div></div>
   ${i.completion_blockers?.length?`<div class="notice warn">${i.completion_blockers.map(x=>`<p>${esc(x.message)}</p>`).join('')}</div>`:''}
  </section>
  <div class="maint-detail-grid"><div>
   <section class="panel maint-facts"><h2>故障与本次结果</h2><p class="break">${esc(i.subject?.description||'故障说明未登记')}</p>${task?`<dl class="fields"><dt>任务建立时间</dt><dd>${esc(task.created_at||'时间未登记')}</dd><dt>首次明确接单</dt><dd>${esc(task.accepted_at||'尚未接单')} · ${esc(who(task.accepted_by))}</dd><dt>实际完成时间</dt><dd>${esc(task.performed_at||'未知／未登记')}</dd><dt>实际时间来源</dt><dd>${esc(task.performed_source||'未登记')}</dd><dt>结果提交时间</dt><dd>${esc(task.completed_at||'尚未提交')} · ${esc(who(task.completed_by))}</dd><dt>结果检查通过</dt><dd>${esc(task.reviewed_at||'尚未通过检查')} · ${esc(who(task.reviewer))}</dd><dt>本次结果／暂缓原因</dt><dd class="break">${esc(task.result||'尚未登记')}</dd></dl>`:'<p class="sub">没有可见任务明细。</p>'}<p class="sub">实际修完时间与系统结果提交时间分开；未知不补造。</p></section>
   <section class="panel"><h2>核验依据与复核历史</h2><p class="sub">文字／受控来源引用，不是已上传照片。按来源分组保留可见记录；当前字段清空不代表旧事实删除。</p>${this.history(i)}</section>
  </div><aside class="panel maint-context" aria-label="维修责任与上下文"><h2>责任与上下文</h2><dl class="fields"><dt>任务执行者</dt><dd>${esc(who(task?.owner))}</dd><dt>整单负责人</dt><dd>${esc(who(i.owner))}</dd><dt>当前位置</dt><dd>${esc(this.location(i))}</dd><dt>人工优先级</dt><dd>${esc(i.subject?.priority||'未登记')}</dd><dt>约定期限</dt><dd>${esc(task?.due_at||i.subject?.due_at||'未约定')}</dd><dt>设备编号</dt><dd>${esc(i.subject?.equipment_id||'未建立稳定设备编号')}</dd><dt>当前轮次</dt><dd>${esc(i.cycle)}</dd><dt>最终关闭时间</dt><dd>${esc(i.closed_at||'尚未最终关闭')}</dd></dl><details class="repair-extra-details"><summary>内部核对说明</summary><p class="sub">系统保留现有结果复核、负责人提交与最终验收事实；它们不新增可见维修状态。交验收时间未知时不从其他动作推算。</p></details></aside></div>`;
 },
 async newFlow(){
  // 重新读取授权，避免缓存中的创建动作或位置列表被当成当前权限。
  await this.readList();if(!this.listActions.includes('start'))throw Error('当前账户未获登记维修授权。');
  const templates=S.templates.filter(t=>t.kind==='maintenance');if(!templates.length)throw Error('没有已发布维修模板。');
  S.formTemplates=templates;S.formKind='maintenance';S.formQuick=true;
  const places=(S.user.units||[]).filter(x=>x&&x!=='*');if(!places.length)throw Error('没有明确已授权公共位置。');
  const f=`<input type="hidden" name="template" value="0"><input type="hidden" name="owner_user_id" value="${esc(S.user.id)}"><input type="hidden" name="resident_id" value="">`+selectField('location','公共位置',[['','请选择已授权位置'],...places.map(x=>[x,x])],'')+field('description','故障说明','','textarea',true,true)+field('equipment_id','设备编号（可选）','','text',false)+field('due_at','人工约定期限（可选）','','text',false)+selectField('priority','人工优先级',[['普通','普通'],['优先','优先']],'普通');
  dialog('登记公共维修',formMarkup('flow-form',f,`<p class="sub">${esc(templates[0].title)} v${esc(templates[0].version)} · 整单负责人和初始执行者为当前账户；分派后整单负责人不自动改变。无真实期限留空。</p>`));
 },
 async commandForm(type,taskId=''){
  if(!this.actions().includes(type))throw Error('服务器未授权当前动作；请刷新状态。');
  pendingCommand={type,taskId};
  if(type==='start_task'){await sendCommand(type,{task_id:taskId});return;}
  if(type==='submit_workflow'){await sendCommand(type,{});return;}
  let f='';
  const hasEvidence=(S.current.evidence||[]).some(e=>e.task_id===taskId&&e.cycle===S.current.cycle);
  if(type==='submit_task')f=field('result','怎么修／实际结果','','textarea',true,true)+selectField('evidence_kind','核验依据类型',[['note','文字说明'],['reference','受控来源引用']],'note')+field('evidence_value',hasEvidence?'补充核验说明或来源引用（已有本轮依据，可空）':'核验说明或来源引用','','textarea',!hasEvidence,true)+field('performed_at','可靠实际完成时间（可选，YYYY-MM-DD HH:mm）','','text',false)+field('performed_source','实际时间来源（与时间成对；未知都空）','','text',false);
  if(['review_task','review_workflow'].includes(type))f=selectField('decision',type==='review_task'?'检查决定':'验收决定',[['approve','通过'],['reject','退回']],'approve')+field('reason','核验意见（通过和退回均必填）','','textarea',true,true);
  if(type==='review_workflow')f+=`<label data-reject-only hidden>退回重做项目<select name="task_id" disabled>${(S.current.tasks||[]).map(t=>`<option value="${esc(t.task_id)}">${esc(t.title)}</option>`).join('')}</select></label>`;
  if(type==='set_task_exception')f='<input type="hidden" name="status" value="暂缓">'+field('reason','暂时不能处理的实际原因','','textarea',true,true)+'<p class="sub wide">目前仅暂缓原因；等待类别／跟进安排尚未实现，不自动取消必要义务。</p>';
  if(type==='add_evidence')f=selectField('kind','依据类型',[['note','文字说明'],['reference','受控来源引用']],'note')+field('value','核验依据','','textarea',true,true);
  if(type==='assign_task'){
   S.directory=await api('/directory');const people=S.directory.filter(u=>['maint_executor','maint_supervisor'].includes(u.role));
   f=selectField('owner_user_id','范围内新执行者',[['','请选择'],...people.map(u=>[u.id,u.display_name])],'')+field('reason','分派理由','','textarea',true,true)+'<p class="sub wide">分派不等于对方接单，整单负责人不变。</p>';
  }
  if(type==='reopen_workflow')f=selectField('task_id','重新处理的原项目',(S.current.tasks||[]).map(t=>[t.task_id,t.title]))+field('reason','重新处理原因','','textarea',true,true)+'<p class="sub wide">重新处理新增轮次，不是修改历史错字或恢复暂缓。</p>';
  if(type==='waive_task')f=field('reason','主管批准免做理由','','textarea',true,true);
  if(!f)throw Error('此动作未纳入公共维修样板。');
  const titles={submit_task:'登记修理结果',review_task:'核对维修结果',review_workflow:'确认验收',set_task_exception:'暂时不能处理',add_evidence:'补充核验依据',assign_task:'分派维修',reopen_workflow:'重新处理',waive_task:'主管批准免做'};
  dialog(titles[type],formMarkup('command-form',f,'<p class="sub">保存实际事实；无照片上传。提交账户和登记时间由服务器记录。</p>'));
  if(type==='review_workflow'){
   const form=$('command-form'),sync=()=>{const reject=form.elements.decision.value==='reject';form.querySelector('[data-reject-only]').hidden=!reject;form.elements.task_id.disabled=!reject;form.elements.task_id.required=reject;};
   form.elements.decision.addEventListener('change',sync);sync();M3.baseline=JSON.stringify(M3.values());
  }
 },
 async handle(action,b){
  if(!this.isRole())return false;
  if(action==='maint-drafts'){S.page='maint-drafts';await loadPage();return true;}
  if(action==='maint-create'){await this.newFlow();return true;}
  if(action==='maint-open'){S.current={id:b.dataset.id};S.page='detail';await loadPage();return true;}
  if(action==='maint-command'){b.disabled=true;try{await this.commandForm(b.dataset.type,b.dataset.task||'');}finally{if(b.isConnected)b.disabled=false;}return true;}
  if(action==='refresh-form'){
   if(S.current&&S.page==='detail')await this.readDetail(S.current.id);else await this.readList();
   toast('当前授权与版本已刷新；核对输入后再提交。');return true;
  }
  return false;
 },
 prepareValues(form,values){
  if(!this.isRole())return values;
  if(form.id==='flow-form'){
   if(!(S.user.units||[]).includes(values.location)||values.location==='*')throw Error('请选择明确已授权公共位置。');
   values.title=String(values.description||'').trim().slice(0,60)||'公共维修';
  }
  if(form.id==='command-form'&&pendingCommand?.type==='submit_task'&&Boolean(values.performed_at?.trim())!==Boolean(values.performed_source?.trim()))throw Error('实际完成时间与来源须同时填写；未知时都留空。');
  if(form.id==='command-form'&&!this.actions().includes(pendingCommand?.type))throw Error('当前动作不再可用，请刷新核对。');
  return values;
 }
};
document.addEventListener('change',async e=>{if(Maintenance.isRole()&&e.target.id==='maint-filter'){Maintenance.filter=e.target.value.trim();try{await loadPage();}catch(err){toast(err.message);}}});
