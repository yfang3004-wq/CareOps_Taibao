'use strict';
/* M3 experience layer. Existing workflow writes still use server commands and
   the M2 submission tracker. Draft bodies never enter browser Web Storage. */
const M3={
 ref:null,slot:null,baseline:'',uncertain:false,restored:false,
 reset(){this.ref=null;this.slot=null;this.baseline='';this.uncertain=false;this.restored=false;},
 form(){return document.querySelector('#dialog form');},
 supported(){return $('dialog').open&&['flow-form','command-form','record-form'].includes(this.form()?.id);},
 values(){return this.supported()?Object.fromEntries(new FormData(this.form())):{};},
 dirty(){return this.supported()&&JSON.stringify(this.values())!==this.baseline;},
 open(){this.reset();if(!this.supported())return;this.slot=crypto.randomUUID();this.baseline=JSON.stringify(this.values());
  const footer=this.form().querySelector('.form-footer');footer.insertAdjacentHTML('afterbegin',btn('save-draft','保存草稿'));
  footer.insertAdjacentHTML('beforebegin','<p class="sub" id="draft-status">尚未保存草稿。仅明确保存后，当前账户才可在重新登录时恢复；7天后过期。</p>');
 },
 payload(){
  const form=this.form(),values=this.values();let kind,context;
  if(form.id==='flow-form'){
   const t=S.formTemplates[Number(values.template)];delete values.template;kind='flow';context={workflow_kind:t.kind,template_id:t.id,template_version:t.version};
  }else if(form.id==='command-form'){
   kind='command';context={type:pendingCommand.type,instance_id:S.current.id,task_id:pendingCommand.taskId||''};
   if(context.task_id)delete values.task_id;
  }else{
   kind='record';const schema=S.schemas.find(s=>s.id===S.table);context={table:S.table,record_id:S.editingRecord?.[schema.idKey]||''};
  }
  return {kind,context,values,expected_draft_revision:this.ref?.revision||0,expected_workspace_revision:S.revision};
 },
 async save(){
  if(!this.supported())throw Error('此表单不支持业务草稿');
  let pending=S.tracker?.marker();if(pending)throw Error('先核对上次业务提交结果，不能把不确定结果另存为新草稿');
  const body=this.payload();let r;
  try{r=await api('/drafts/'+this.slot,'PUT',body);this.uncertain=false;}
  catch(e){this.uncertain=!e.status||e.status>=500;throw e;}
  this.ref={id:r.id,revision:r.revision};this.baseline=JSON.stringify(this.values());
  $('draft-status').textContent='草稿已保存到本机数据库 · 第'+r.revision+'版。尚未提交业务，不改变任务状态。';toast('仅当前账户可恢复的草稿已保存');
 }
};
const queueNames={mine:'我的待办',closure:'待我收口',handover:'待我接班',review:'待我复核',overdue:'超期事项',all:'全部授权待办'};
S.todayFilter={queue:'mine',kind:'',owner_user_id:'',due_before:'',page:1};
// 仅保留今日工作台重绘前的控件身份，不改变队列与业务参数。
function captureTodayFocus(){
 const node=document.activeElement,panel=node?.closest('.workspace-panel');
 if(!panel||!$('content').contains(panel))return null;
 let identity;
 if(node.dataset.action==='today-queue')identity={type:'queue',key:node.dataset.queue};
 else if(node.dataset.action==='today-page')identity={type:'page',index:[...panel.querySelectorAll('[data-action="today-page"]')].indexOf(node)};
 else if(node.dataset.action==='today-reset')identity={type:'reset'};
 else if(node.closest('#today-filter')){
  if(['kind','owner_user_id','due_before'].includes(node.name))identity={type:'field',name:node.name};
  else if(node.matches('button[type="submit"]'))identity={type:'submit'};
 }
 return identity?{node,panel,identity,epoch:S.authEpoch||0}:null;
}
async function renderToday(focus=captureTodayFocus()){
 let moved=false;
 // 请求等待期间主动移焦或点击其他位置后，不再恢复原控件。
 const trackFocus=e=>{if(e.target!==focus?.node&&e.target!==document.body)moved=true;};
 const trackPointer=e=>{if(!focus?.node.contains(e.target))moved=true;};
 if(focus){document.addEventListener('focusin',trackFocus);document.addEventListener('pointerdown',trackPointer);}
 try{
  const q=new URLSearchParams(S.todayFilter),r=await api('/today?'+q);S.today=r;S.revision=r.revision;
  const active=document.activeElement;
  const restore=focus&&!moved&&focus.epoch===(S.authEpoch||0)&&$('content').contains(focus.panel)&&
   (active===focus.node||(active===document.body&&(!focus.node.isConnected||focus.node.disabled)));
  const options=[['','全部负责人'],...peopleOptions()];
  $('content').innerHTML=renderTodayWorkspace(r,options);
  if(restore){
   const panel=$('content').querySelector('.workspace-panel'),id=focus.identity;
   let target;
   // 固定选择器后按字段比较，避免动态值进入选择器。
   if(id.type==='queue')target=[...panel.querySelectorAll('[data-action="today-queue"]')].find(n=>n.dataset.queue===id.key);
   else if(id.type==='page')target=[...panel.querySelectorAll('[data-action="today-page"]')][id.index];
   else if(id.type==='reset')target=panel.querySelector('[data-action="today-reset"]');
   else if(id.type==='submit')target=panel.querySelector('#today-filter button[type="submit"]');
   else if(id.type==='field')target=[...panel.querySelectorAll('#today-filter [name]')].find(n=>n.name===id.name);
   if(target&&!target.disabled)target.focus();else focusPageHeading();
  }
 }finally{
  if(focus){document.removeEventListener('focusin',trackFocus);document.removeEventListener('pointerdown',trackPointer);}
 }
}
function m3DetailHeader(i){
 const full=i.resident_id&&!i.minimal_repair;
 let x='';
 if(full)x+=btn('resident','查看长者详情与时间线',`data-id="${esc(i.resident_id)}"`,'link');
 if(i.status==='active'&&i.kind==='maintenance'&&full&&(reviewer()||S.user.role==='caregiver'&&i.tasks.some(t=>t.owner===actor()))){
  x+=`<div class="notice"><p>房间维修不能直接把原始记录发给后勤。先发布明确的必要信息，再分派或交接给指定后勤。</p><div class="actions">${btn('command','发布后勤必要信息','data-type="share_repair"')}${i.repair_access?btn('command','撤回后勤信息','data-type="revoke_repair"'):''}</div>${i.repair_access?`<p class="sub">已发布给 ${esc(who('account:'+i.repair_access.user_id))}，第 ${i.repair_access.cycle} 轮。当前发布不自动转移责任。</p>`:''}</div>`;
 }
 if(i.maintenance_brief){const b=i.maintenance_brief;x+=`<div class="notice"><h3>只提供维修必要信息</h3><dl class="fields">${[['location','位置'],['equipment','设备'],['fault','故障'],['access_window','入室安排'],['contact_role','联系岗位']].map(([k,n])=>`<dt>${n}</dt><dd>${esc(b[k])}</dd>`).join('')}</dl><p class="sub">不含长者编号、姓名、原始描述或完整照护资料。内容由发布人核对，不是自动匿名化。</p></div>`;}
 if(i.status==='active'&&i.completion_blockers?.length)x+=`<div class="notice warn"><h3>提交最终复核前还需完成</h3>${i.completion_blockers.map(b=>`<p>${b.task_id?btn('focus-task',b.message,`data-id="${esc(b.task_id)}"`,'link'):esc(b.message)}</p>`).join('')}</div>`;
 if(i.responsibility_events?.length)x+=`<details class="history"><summary>责任与授权变更记录（${i.responsibility_events.length}）</summary>${i.responsibility_events.map(e=>`<div class="record"><strong>${esc({share_repair:'发布维修必要信息',revoke_repair:'撤回后勤信息',takeover_task:'确认接续责任',assign_task:'重新分派任务'}[e.action]||'责任变更')}</strong><p>${esc(e.from_owner?who(e.from_owner)+' → '+who(e.to_owner):who(e.actor))}</p><p>${esc(e.reason)}</p><p class="sub">第${e.cycle}轮 · ${esc(e.at)}</p></div>`).join('')}</details>`;
 return x;
}
const ACCOUNT_FIELDS=new Set(['owner','completed_by','reviewer','reporter','sender','receiver','verified_by','author','assessor','approver']);
function humanFields(table,row){
 if(table==='residents')return ResidentProfile.details(row,S.recordDetailRow===row?'dialog':'profile');
 const schema=S.schemas.find(s=>s.id===table),fields=schema?.fields||[];
 return `<dl class="fields">${Object.entries(row).filter(([k])=>k!=='facility_id'&&!k.startsWith('_')).map(([k,v])=>`<dt>${esc(fields.find(f=>f.key===k)?.label||k)}</dt><dd class="break">${esc(ACCOUNT_FIELDS.has(k)&&String(v).startsWith('account:')?who(v):(v===''||v===null||v===undefined)?'—':v)}</dd>`).join('')}</dl>`;
}
function showRecordDetail(table,row){
 S.recordDetailRow=table==='residents'?row:null;
 dialog(tableNames[table],`${humanFields(table,row)}${table==='residents'&&reviewer()?btn('resident-profile-edit','完善档案',`data-id="${esc(row.resident_id)}"`,'primary'):''}${row.resident_id&&S.user.role!=='maintenance'?btn('resident','查看长者详情与时间线',`data-id="${esc(row.resident_id)}"`,'primary'):''}<p class="sub">这是原始登记内容；不由系统推断诊断、风险级别或服务是否真实完成。</p>`);
}
async function renderResident(){
 const r=await api('/residents/'+encodeURIComponent(S.residentId)+'/profile?page='+(S.residentPage||1));S.profile=r;S.revision=r.revision;
 const item=r.resident;
 $('content').innerHTML=`<div class="actions">${btn('nav','返回记录中心','data-page="records"')}${btn('refresh','刷新详情')}</div><section class="panel"><div class="panel-head"><div><h2>${esc(item.display_name)}</h2><p class="sub">${esc(item.resident_id)} · ${esc(item.unit)} · ${esc(item.bed)}</p></div><span class="chip">仅当前授权字段</span></div>${humanFields('residents',item)}${reviewer()?btn('resident-profile-edit','完善档案',`data-id="${esc(item.resident_id)}"`,'primary'):''}</section><div class="profile-grid"><section class="panel"><h2>事件、任务、交接与复核时间线</h2><p class="sub">${esc(r.note)} · ${esc(r.timezone)}</p>${r.timeline.map(e=>`<article class="timeline-item"><p class="sub">${esc(e.at||'时间未记录')} · ${esc(e.label)}</p><h3>${e.flow_id?btn('open-flow',e.title,`data-id="${esc(e.flow_id)}"`,'link'):e.table?btn('timeline-record',e.title,`data-table="${esc(e.table)}" data-id="${esc(e.entity_id)}"`,'link'):esc(e.title)}</h3><p>${esc(e.entity_id)} ${e.status?chip(e.status):''}</p>${e.actor?`<p class="sub">${esc(who(e.actor))}</p>`:''}${e.reason?`<p class="break">${esc(e.reason)}</p>`:''}</article>`).join('')||'<p class="empty">暂无可见时间线记录</p>'}<div class="actions">${btn('resident-page','上一页',`data-page="${r.page-1}" ${r.page<=1?'disabled':''}`)}<span>第${r.page}页 · 共${r.timeline_total}条</span>${btn('resident-page','下一页',`data-page="${r.page+1}" ${r.page*r.page_size>=r.timeline_total?'disabled':''}`)}</div></section><section class="panel"><h2>相关登记</h2>${Object.entries(r.related).map(([table,v])=>`<details class="related"><summary>${esc(tableNames[table])} · ${v.count} 条</summary>${v.items.map(row=>btn('profile-record',row[S.schemas.find(s=>s.id===table).idKey],`data-table="${table}" data-id="${esc(row[S.schemas.find(s=>s.id===table).idKey])}"`,'link')).join('')}${v.truncated?'<p class="sub">仅显示前50条；更多请到记录中心按编号查阅。</p>':''}</details>`).join('')}</section></div>`;
}
async function renderDrafts(){
 const r=await api('/drafts');S.draftList=r.items;
 $('content').innerHTML=`<section class="panel"><h2>仅属于当前账户的草稿</h2><p class="sub">明确保存后保留${r.retention_days}天，最多${r.limit}份未提交草稿。退出不会删除；换账户不可读取，恢复须重新核对权限与最新状态。数据库未加密，整库备份包含草稿。</p><div class="table-wrap"><table><thead><tr><th>类型</th><th>最后保存 UTC</th><th>状态</th><th>操作</th></tr></thead><tbody>${r.items.map(d=>`<tr><td>${esc({flow:'新建事项',command:'办理表单',record:'基础登记'}[d.kind]||'业务草稿')}</td><td>${esc(d.updated_at)}</td><td>${d.submitted?'已用于提交 · 正文已清除':d.recoverable?'待核对恢复':'权限或对象已变化 · 正文不展示'}</td><td>${d.recoverable?btn('restore-draft','核对并恢复',`data-id="${esc(d.id)}"`,'small primary'):''}${btn('delete-draft','删除草稿',`data-id="${esc(d.id)}" data-revision="${d.revision}"`,'small')}</td></tr>`).join('')||'<tr><td colspan="4"><p class="empty">没有属于本账户的有效草稿</p></td></tr>'}</tbody></table></div></section>`;
}
async function restoreDraft(id){
 const d=await api('/drafts/'+id);
 if(d.submitted){toast(d.note);return;}
 if(d.kind==='flow')await newFlow(d.context.workflow_kind);
 else if(d.kind==='command'){S.current={id:d.context.instance_id};S.page='detail';await loadPage();await commandForm(d.context.type,d.context.task_id);}
 else{
  S.table=d.context.table;const recordResponse=await api('/records/'+S.table);S.records=recordResponse.items;S.recordAllowedFields=recordResponse.allowed_fields;
  const schema=S.schemas.find(s=>s.id===S.table);const n=d.context.record_id?S.records.findIndex(r=>r[schema.idKey]===d.context.record_id):-1;
  if(d.context.record_id&&n<0)throw Error('原记录已不可见，不能恢复编辑');recordForm(n);
 }
 const f=M3.form();
 if(!M3.supported())throw Error('无法安全恢复此类表单');
 if(d.kind==='flow'){
  const ix=S.formTemplates.findIndex(t=>t.id===d.context.template_id&&t.version===d.context.template_version);
  if(ix<0)throw Error('原模板版本已不可用');f.elements.template.value=String(ix);
 }
 for(const [k,v] of Object.entries(d.values))if(f.elements[k])f.elements[k].value=v;
 // 恢复退回草稿时同步条件字段，不提交通过分支的隐藏任务。
 if(f.elements.decision)f.elements.decision.dispatchEvent(new Event('change',{bubbles:true}));
 M3.slot=d.id;M3.ref={id:d.id,revision:d.revision};M3.baseline=JSON.stringify(M3.values());M3.restored=true;M3.uncertain=false;S.revision=d.current_workspace_revision;
 $('draft-status').textContent=`恢复的是第${d.revision}版草稿（保存时工作区${d.workspace_revision}，当前${d.current_workspace_revision}）。不是自动提交，也不保证内容仍适用。`;
 f.querySelector('.form-footer').insertAdjacentHTML('beforebegin','<label class="notice warn"><input type="checkbox" required data-draft-recheck> 已核对当前事项、负责人及内容。确认后才允许提交。</label>');
}
// Protect unsaved business form edits; never block security-driven logout/expiry.
document.addEventListener('click',e=>{
 const b=e.target.closest('[data-action]');if(!b)return;
 const leaves=['close-dialog','nav','refresh','logout','password','open-flow','resident','new-flow','new-record','edit-record','resident-profile-edit','restore-draft'];
 if(leaves.includes(b.dataset.action)&&M3.dirty()&&!confirm('还有未保存的表单更改。离开将丢失这些更改；留在当前表单可点击“保存草稿”。确定离开？')){e.preventDefault();e.stopImmediatePropagation();return;}
 if(b.dataset.action==='close-dialog')M3.reset();
},true);
document.addEventListener('input',e=>{if(e.target.closest('#dialog form')&&M3.supported()&&$('draft-status'))$('draft-status').textContent=M3.dirty()?'有未保存更改；保存草稿不会提交业务。':M3.ref?'草稿已保存，尚未提交业务。':'尚未保存草稿。';});
document.addEventListener('submit',e=>{
 if(e.target===M3.form()&&M3.uncertain){e.preventDefault();e.stopImmediatePropagation();toast('草稿保存结果尚未确认。请先用“保存草稿”核对，避免重复创建。');}
},true);
$('dialog').addEventListener('cancel',e=>{if(M3.dirty()&&!confirm('还有未保存的更改，确定关闭？'))e.preventDefault();else M3.reset();});
window.addEventListener('beforeunload',e=>{if(M3.dirty()){e.preventDefault();e.returnValue='';}});
document.addEventListener('submit',async e=>{
 if(e.target.id!=='today-filter')return;e.preventDefault();
 try{Object.assign(S.todayFilter,Object.fromEntries(new FormData(e.target)),{page:1});await renderToday();}catch(err){toast(err.message);}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b)return;
 const a=b.dataset.action;
 if(!['today-queue','today-submit','today-page','today-reset','resident','resident-page','timeline-record','profile-record','save-draft','restore-draft','delete-draft','takeover','focus-task'].includes(a))return;
 const todayFocus=captureTodayFocus(),errorTarget=$('dialog').open?$('form-error'):null,errorEpoch=S.authEpoch||0;
 b.disabled=true;
 try{
  if(a==='today-queue'){S.todayFilter.queue=b.dataset.queue;S.todayFilter.page=1;await renderToday(todayFocus);}
  if(a==='today-submit'){S.current={id:b.dataset.id,revision:Number(b.dataset.revision)};await sendCommand('submit_workflow',{});}
  if(a==='today-page'){S.todayFilter.page=Number(b.dataset.page);await renderToday(todayFocus);}
  if(a==='today-reset'){S.todayFilter={queue:'mine',kind:'',owner_user_id:'',due_before:'',page:1};await renderToday(todayFocus);}
  if(a==='resident'){if($('dialog').open)$('dialog').close();M3.reset();S.residentId=b.dataset.id;S.residentPage=1;S.page='resident';await loadPage();}
  if(a==='resident-page'){S.residentPage=Number(b.dataset.page);await renderResident();}
  if(a==='profile-record'||a==='timeline-record'){
   const table=b.dataset.table,schema=S.schemas.find(s=>s.id===table);
   const rows=a==='profile-record'?S.profile.related[table].items:(await api('/records/'+table)).items;
   const row=rows.find(x=>x[schema.idKey]===b.dataset.id);if(!row)throw Error('记录不再可见');showRecordDetail(table,row);
  }
  if(a==='focus-task')$('task-'+b.dataset.id)?.scrollIntoView({behavior:'smooth',block:'center'});
  if(a==='takeover'){await commandForm('takeover_task');$('command-form').elements.handover_id.value=b.dataset.id;M3.baseline=JSON.stringify(M3.values());}
  if(a==='save-draft')await M3.save();
  if(a==='restore-draft')await restoreDraft(b.dataset.id);
  if(a==='delete-draft'&&confirm('删除这份草稿？不会删除已经提交的业务记录。')){await api('/drafts/'+b.dataset.id+'/delete','POST',{revision:Number(b.dataset.revision)});await renderDrafts();}
 }catch(err){if(errorEpoch===(S.authEpoch||0)){if(errorTarget)showFormError(err,errorTarget);else if(b.isConnected)toast(err.message);}}
 finally{if(b.isConnected)b.disabled=false;}
});
