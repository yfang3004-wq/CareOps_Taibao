'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={user:null,csrf:'',facility:null,page:'today',revision:0,flows:[],current:null,directory:[],schemas:[],templates:[],table:'residents',filter:'',importPreview:null};
const labels={admin:'机构管理员',supervisor:'主管',caregiver:'照护人员',maintenance:'后勤人员',viewer:'只读观察员',maint_executor:'维修执行者',maint_supervisor:'维修主管'};
const flowLabels={active:'办理中',pending_review:'待最终复核',closed:'已关闭'};
const kinds={incident:'事件协作',maintenance:'设施维修',rectification:'检查整改'};
const tableNames={residents:'长者主档',stays:'在院状态',assessments:'评估登记',plans:'照护计划',tasks:'任务',incidents:'事件',handovers:'交接',followups:'后续评价',workorders:'维修单',findings:'检查发现',corrections:'整改行动'};
const reviewer=()=>S.user&&['admin','supervisor'].includes(S.user.role);
const actor=()=>`account:${S.user.id}`;
const who=id=>S.directory.find(u=>`account:${u.id}`===id)?.display_name || id || '—';
let toastTimer;
function toast(msg){$('toast').textContent=msg;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
async function api(path,method='GET',body){
 const epoch=S.authEpoch||0;const opts={method,credentials:'same-origin',headers:{}};
 if(method!=='GET'){opts.headers['Content-Type']='application/json';opts.headers['X-CSRF-Token']=S.csrf;opts.body=JSON.stringify(body??{});}
 let r;try{r=await fetch('/api'+path,opts);}catch(e){throw Error('无法连接本机服务。此次操作未确认保存，请保留输入，恢复服务后刷新核对。');}
 const data=await r.json().catch(()=>({detail:'服务返回异常'}));
 if(epoch!==(S.authEpoch||0))throw Error('身份已变化；丢弃旧请求结果，请在当前账户下重新读取。');
 if(!r.ok){if(r.status===401&&path!=='/auth/login')showLogin('会话已失效。只有明确保存的账户草稿可重新登录后恢复；未保存输入已清除。');const e=Error(typeof data.detail==='string'?data.detail:'输入未通过校验');e.status=r.status;throw e;}
 return data;
}
// 错误仅反馈到原表单；旧请求不向后来打开的对话框写入内容。
function showFormError(error,element=$('form-error')){
 if(!element||!element.isConnected)return false;
 const ownerDialog=element.closest('dialog');if(ownerDialog&&!ownerDialog.open)return false;
 const message=typeof error==='string'?error:error?.message||'';
 element.textContent=message+(error?.status===409?'\n请先刷新状态并核对，再决定是否重新提交。':'');
 element.setAttribute('role','alert');element.setAttribute('tabindex','-1');
 if(element.textContent.trim())element.focus();
 return true;
}
function focusPageHeading(){
 if(!S.user||$('dialog').open)return;
 const heading=$('app').querySelector('main h1');
 if(heading){heading.setAttribute('tabindex','-1');heading.focus();}
}
function brand(){return '<div class="brand"><span class="brandmark">C</span><div><strong>CareOps Desk</strong><br><small>LOCAL WORKSPACE · 0.4</small></div></div>';}
function btn(action,label,attrs='',cls=''){return `<button type="button" data-action="${esc(action)}" ${attrs} class="${esc(cls)}">${esc(label)}</button>`;}
function chip(status){return `<span class="chip ${['待复核','pending_review','待接收','暂缓','已取消'].includes(status)?'warn':''}">${esc(flowLabels[status]||status||'未填写')}</span>`;}
function showLogin(error=''){
 S.authEpoch=(S.authEpoch||0)+1;if(typeof Departments!=='undefined')Departments.reset();if(typeof Maintenance!=='undefined')Maintenance.reset();if(typeof M3!=='undefined')M3.reset();S.profile=null;S.recordDetailRow=null;S.recordAllowedFields=null;S.recordCanWrite=null;S.today=null;S.editingRecord=null;S.editingUser=null;S.residentId=null;S.pendingDraft=null;S.draftList=[];S.formTemplates=[];if(typeof pendingCommand!=='undefined')pendingCommand=null;
 $('dialog-content').textContent='';
 S.user=null;S.csrf='';S.current=null;S.flows=[];S.directory=[];S.records=[];S.accounts=[];S.tableFile=null;S.inspectedFile=null;S.tablePreview=null;S.importPreview=null;S.tracker=null;if($('dialog').open)$('dialog').close();
 $('app').innerHTML=`<main class="login-wrap"><div class="login-head">${brand()}</div><section class="panel login-card"><h1>工作台登录</h1><p class="sub" style="margin-bottom:18px">栖云颐养 · 金陵国际康养社区（CCRC 演示版）</p><form id="login-form"><div class="form-grid"><label>账号<input name="username" required autocomplete="username" maxlength="60" placeholder="输入工作账号"></label><label>密码<input type="password" name="password" required autocomplete="current-password" maxlength="128" placeholder="输入密码"></label></div><p class="error" id="login-error" role="alert" tabindex="-1">${esc(error)}</p><button class="primary" type="submit">进入工作台</button></form><div id="demo-quick-login"></div></section></main>`;
 loadDemoQuickAccounts();
}
async function loadDemoQuickAccounts(){
 if(typeof fetch!=='function')return;
 try{
  const r=await fetch('/api/auth/demo-accounts',{credentials:'same-origin'});
  const d=await r.json();
  const box=$('demo-quick-login');
  if(!box||!d?.enabled||!Array.isArray(d.accounts)||!d.accounts.length)return;
  S.demoAccounts=d.accounts;
  box.innerHTML=`<div class="login-demo-block"><p class="login-demo-title">一键切换演示岗位</p><div class="login-demo-grid">${d.accounts.map(a=>`<button type="button" class="login-role-btn" data-action="demo-switch-exec" data-username="${esc(a.username)}"><strong>${esc(a.display_name.split('（')[0])}</strong><span>${esc(a.role_label)}</span></button>`).join('')}</div></div>`;
 }catch{}
}
function shell(){
 if(typeof Departments!=='undefined'&&Departments.enabled()){Departments.shell();return;}
 if(typeof Maintenance!=='undefined'&&Maintenance.isRole()){Maintenance.shell();return;}
 const menus=[['today','今日工作台'],['flows','流程工作台'],['records','记录中心']];
 if(S.user.role!=='viewer')menus.push(['drafts','我的草稿']);
 if(reviewer())menus.push(['imports','资料导入与备份']);
 if(S.user.role==='admin')menus.push(['users','账号与权限']);
 if(reviewer()&&S.user.units.includes('*'))menus.push(['audit','服务端审计']);
 $('app').innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<nav>${menus.map(([p,n])=>btn('nav',n,`data-page="${p}"`,S.page===p?'active':'')).join('')}</nav><div class="side-bottom"><p class="ui-facility-name">${esc(S.facility.name)}</p><span class="sub">${esc(S.facility.timezone)}</span></div></aside><main class="main"><header class="top"><div><p class="overline">CARE OPERATIONS / LOCAL SERVICE</p><h1>${esc(S.page==='detail'?'流程详情':S.page==='resident'?'长者详情':menus.find(m=>m[0]===S.page)?.[1]||'工作区')}</h1></div><div class="actions ui-toolbar" role="toolbar" focusgroup="toolbar wrap" aria-label="会话操作栏"><div class="ui-btn-group" role="group" aria-label="当前身份"><span class="chip">${esc(S.user.display_name)} · ${labels[S.user.role]}</span>${btn('demo-switch-open','切换身份','','small')}</div><span class="ui-toolbar-sep" role="separator" aria-orientation="vertical"></span><div class="ui-btn-group" role="group" aria-label="会话安全">${btn('password','改密','','small')}${btn('logout','退出','','small')}</div></div></header><div data-pending-placeholder></div><section id="content"><p class="loading">正在读取已授权记录…</p></section></main></div>`;
}
function wireToolbars(root=document){
 if(typeof HTMLElement==='undefined'||'focusGroup' in HTMLElement.prototype||!root?.querySelectorAll)return;
 root.querySelectorAll('[role="toolbar"][focusgroup]').forEach(bar=>{
  if(bar.dataset.rovingWired==='1')return;
  bar.dataset.rovingWired='1';
  const items=()=>[...bar.querySelectorAll('button:not([disabled])')].filter(b=>b.getClientRects().length);
  const sync=()=>{const list=items();if(!list.length)return;const cur=list.find(b=>b.getAttribute('tabindex')==='0')||list[0];list.forEach(b=>b.setAttribute('tabindex',b===cur?'0':'-1'));};
  sync();
  bar.addEventListener('keydown',e=>{
   if(!['ArrowRight','ArrowLeft','Home','End'].includes(e.key))return;
   const list=items();if(!list.length)return;
   const idx=Math.max(0,list.indexOf(document.activeElement));
   const next=e.key==='Home'?0:e.key==='End'?list.length-1:(idx+(e.key==='ArrowRight'?1:-1)+list.length)%list.length;
   e.preventDefault();
   list.forEach((b,n)=>b.setAttribute('tabindex',n===next?'0':'-1'));
   list[next].focus();
  });
 });
}
async function loadPage(){
 shell();
 try{
 if(typeof Departments!=='undefined'&&Departments.enabled()&&S.page.startsWith('dept-'))await Departments.render();
 else if(typeof Maintenance!=='undefined'&&Maintenance.isRole())await Maintenance.render();
 else if(S.page==='today')await renderToday();
 else if(S.page==='drafts')await renderDrafts();
 else if(S.page==='resident')await renderResident();
 else if(S.page==='flows')await renderFlows();
 else if(S.page==='detail')await renderDetail(S.current.id);
 else if(S.page==='records')await renderRecords();
 else if(S.page==='users')await renderUsers();
 else if(S.page==='imports')await renderImports();
 else if(S.page==='audit')await renderAudit();
 wireToolbars();
 updatePendingNotices();return true;
 }catch(e){if(e.status===401){showLogin(e.message);return false;} $('content').innerHTML=`<p class="error">${esc(e.message)}</p>${btn('refresh','刷新')}`;return false;}
}
async function init(){
 const me=await api('/auth/me');S.user=me.user;S.csrf=me.csrf;S.facility=me.facility;
 S.tracker=new CareSubmission(api,{getItem:k=>sessionStorage.getItem(k),setItem:(k,v)=>sessionStorage.setItem(k,v),removeItem:k=>sessionStorage.removeItem(k)},S.user.id);
 S.directory=await api('/directory');S.schemas=(await (await fetch('/assets/fields.json')).json()).schemas;
 S.templates=await api('/templates');S.page=typeof Departments!=='undefined'&&Departments.enabled()?'dept-overview':typeof Maintenance!=='undefined'&&Maintenance.isRole()?'maint-work':'today';await loadPage();
}
// 院长/总管理进入“后勤维修”时，只展示四阶段维修，不混入事件、检查整改状态。
function renderMaintenanceAdminList(data){
 const MS=CareOpsMaintenanceStatus;
 const maintenance=(data.items||[]).filter(i=>i.kind==='maintenance');
 const counts=Object.fromEntries(MS.stages.map(s=>[s.key,0]));
 maintenance.forEach(i=>{counts[MS.key(i)]++;});
 const filtered=maintenance.filter(i=>!S.filter||MS.key(i)===S.filter);
 const select=`<label>维修状态 <select id="flow-filter" class="filter"><option value="">全部状态</option>${MS.stages.map(s=>`<option value="${s.key}" ${S.filter===s.key?'selected':''}>${s.label}</option>`).join('')}</select></label>`;
 $('content').innerHTML=`<div class="repair-stage-summary" aria-label="维修状态统计">${MS.stages.map(s=>`<span class="repair-status-pill" data-stage="${s.key}">${s.label} ${counts[s.key]}</span>`).join('')}</div>
  <section class="panel"><div class="panel-head"><h2>设施维修</h2><div class="actions"><div class="ui-btn-group" role="group" aria-label="维修列表操作">${btn('refresh','刷新')}</div>${S.user.role!=='viewer'?btn('quick-repair','+ 快速报修','','primary'):''}</div></div>
  <div class="filters" style="margin-bottom:12px">${select}</div>
  <div class="table-wrap maintenance-table-wrap"><table class="maintenance-table"><thead><tr><th>报修事项</th><th>维修状态</th><th>负责人</th><th>约定期限</th><th>操作</th></tr></thead><tbody>${filtered.map(i=>`<tr><td data-label="报修事项">${esc(i.title)}<small class="sub">${esc(i.id)} · ${esc(i.minimal_repair?'房间维修':i.resident_id||'公共事项')}</small></td><td data-label="维修状态"><span class="repair-status-pill" data-stage="${MS.key(i)}">${MS.label(i)}</span>${MS.note(i)?`<small class="sub">${esc(MS.note(i))}</small>`:''}</td><td data-label="负责人">${esc(who(i.owner))}</td><td data-label="约定期限">${esc(i.repair_due_at||'未约定')}</td><td data-label="操作">${btn('open-flow','查看／办理',`data-id="${esc(i.id)}"`,'small')}</td></tr>`).join('')||'<tr><td colspan="5"><p class="empty">当前筛选没有维修事项。</p></td></tr>'}</tbody></table></div></section>`;
}
async function renderFlows(){
 const data=await api('/workflows');S.flows=data.items;S.revision=data.revision;
 if(S.departmentMaintenance){renderMaintenanceAdminList(data);return;}
 const active=S.flows.filter(i=>i.status==='active').length,pending=S.flows.filter(i=>i.status==='pending_review').length;
 const items=S.flows.filter(i=>!S.filter||i.status===S.filter);
 const create=S.user.role!=='viewer';
 const allowed=S.templates.filter((t,n,a)=>a.findIndex(x=>x.kind===t.kind)===n);
 $('content').innerHTML=`<div class="stats">${[['已授权流程',S.flows.length],['办理中',active],['待最终复核',pending],['已关闭',S.flows.length-active-pending]].map(([n,v])=>`<div class="stat"><p class="label">${n}</p><strong>${v}</strong></div>`).join('')}</div><section class="panel"><div class="panel-head"><h2>协作流程</h2><div class="actions">${create?`<div class="ui-btn-group" role="group" aria-label="新建流程">${allowed.map(t=>btn('new-flow','新建'+kinds[t.kind],`data-kind="${t.kind}"`,t.kind==='incident'?'primary':'')).join('')}</div>`:''}</div></div><div class="filters"><label>状态 <select id="flow-filter" class="filter"><option value="">全部</option>${Object.entries(flowLabels).map(([k,v])=>`<option value="${k}" ${S.filter===k?'selected':''}>${v}</option>`).join('')}</select></label><div class="ui-btn-group" role="group" aria-label="刷新操作">${btn('refresh','刷新记录')}</div></div><div class="table-wrap"><table><thead><tr><th>事项</th><th>类型 / 轮次</th><th>状态</th><th>任务</th><th>负责人</th></tr></thead><tbody>${items.map(i=>`<tr><td>${btn('open-flow',i.title,`data-id="${esc(i.id)}"`,'link')}<div class="sub">${esc(i.id)} · ${esc(i.minimal_repair?'房间维修 · 无长者资料权限':i.resident_id||'公共事项')}</div></td><td>${kinds[i.kind]}<div class="sub">v${esc(i.template_version)} · 第${i.cycle}轮</div></td><td>${chip(i.status)}</td><td>${i.closed_count} / ${i.task_count} 已关闭</td><td>${esc(who(i.owner))}</td></tr>`).join('')||'<tr><td colspan="5"><div class="empty">没有可见流程。可新建公共维修事项，或由管理员导入合成工作区。</div></td></tr>'}</tbody></table></div></section>`;
}
async function renderDetail(id){
 const data=await api('/workflows/'+encodeURIComponent(id));S.revision=data.revision;S.current=data.item;const i=S.current;
 if(i.kind==='incident'){renderIncidentDetail(i);return;}
 const canWrite=S.user.role!=='viewer';
 $('content').innerHTML=`<div class="actions">${S.departmentMaintenance?btn('maintenance-back','返回维修列表'):btn('nav','返回流程列表','data-page="flows"')}${btn('refresh','刷新当前状态')}</div><section class="panel"><div class="panel-head"><div><h2>${esc(i.title)}</h2><p class="sub">${esc(i.id)} · ${kinds[i.kind]} · 第${i.cycle}轮 · 流程修订 ${i.revision}</p></div>${i.kind==='maintenance'?`<span class="repair-status-pill" data-stage="${CareOpsMaintenanceStatus.key(i)}">${CareOpsMaintenanceStatus.label(i)}</span>`:chip(i.status)}</div>${i.kind==='maintenance'?CareOpsMaintenanceStatus.progress(i,esc):''}${i.kind==='maintenance'&&CareOpsMaintenanceStatus.note(i)?`<p class="repair-stage-note">${esc(CareOpsMaintenanceStatus.note(i))}</p>`:''}<p class="break">${esc(i.subject.description||'')}</p><p class="sub">负责人：${esc(who(i.owner))} · 长者：${esc(i.minimal_repair?'房间维修 · 无长者资料权限':i.resident_id||'公共事项')} · 位置：${esc(i.subject.location||'—')}</p><p class="sub">${esc(i.template_note)}</p><div class="form-footer">${canWrite&&i.status==='active'?btn('command',i.kind==='maintenance'?'提交验收':'提交最终复核',`data-type="submit_workflow" ${i.can_submit?'':'disabled title="先完成下方缺项"'}`,'primary'):''}${i.can_review?btn('command',i.kind==='maintenance'?'确认验收':'最终复核','data-type="review_workflow"','primary'):''}${reviewer()&&i.status==='closed'?btn('command','说明理由并重开','data-type="reopen_workflow"'):''}</div>${m3DetailHeader(i)}</section><div class="detail-grid"><section class="panel"><h2>${i.kind==='maintenance'?'维修办理':'执行任务'}</h2>${i.tasks.map(t=>{
 const own=t.owner===actor(),active=i.status==='active',attrs=`data-task="${esc(t.task_id)}"`;
 let buttons='';
 if(canWrite&&active&&own){
 if(['待处理','暂缓','拒绝','不适用','已取消'].includes(t.status))buttons+=btn('command','接手并开始',`${attrs} data-type="start_task"`,'small primary');
 if(i.kind!=='maintenance'&&['待处理','执行中','待复核'].includes(t.status)||i.kind==='maintenance'&&t.status==='待复核')buttons+=btn('command',t.status==='待复核'?'补充依据':'添加依据',`${attrs} data-type="add_evidence"`,'small');
 if(t.status==='执行中')buttons+=btn('command',i.kind==='maintenance'?'维修已完成，登记结果':'填写结果并提交',`${attrs} data-type="submit_task"`,'small primary');
 if(['待处理','执行中'].includes(t.status))buttons+=btn('command','暂缓 / 取消',`${attrs} data-type="set_task_exception"`,'small');
 if(i.resident_id&&!['已关闭','已取消','不适用'].includes(t.status))buttons+=btn('command','建立交接',`${attrs} data-type="create_handover"`,'small');
 }
 if(reviewer()&&active){
 if(t.status==='待复核'&&t.completed_by!==actor())buttons+=btn('command',i.kind==='maintenance'?'核对维修结果':'任务复核',`${attrs} data-type="review_task"`,'small primary');
 if(!['待复核','已关闭'].includes(t.status))buttons+=btn('command','分派 / 重新绑定账户',`${attrs} data-type="assign_task"`,'small');
 if(i.kind!=='incident'&&['不适用','已取消'].includes(t.status))buttons+=btn('command','明确豁免',`${attrs} data-type="waive_task"`,'small');
 }
 return `<article class="task" id="task-${esc(t.task_id)}"><div class="task-head"><h3>${esc(t.title||t.task_id)}</h3>${i.kind==='maintenance'?`<small class="sub">${esc({'待处理':'等待接单','执行中':'正在维修','暂缓':'暂停等待','待复核':'维修结果待核对','已关闭':'结果已核对'}[t.status]||'请查看办理记录')}</small>`:chip(t.status)}</div><p class="sub">${esc(t.task_id)} · 负责人 ${esc(who(t.owner))}<br>期限 ${esc(t.due_at||'未约定')}（${esc(S.facility.timezone)}）</p>${t.accepted_at?`<p class="sub">明确接手：${esc(t.accepted_at)} · ${esc(who(t.accepted_by))}</p>`:''}${t.performed_at?`<p class="sub">实际完成：${esc(t.performed_at)} · 来源：${esc(t.performed_source||'未记录')}</p>`:''}${t.result?`<p class="break">${esc(t.result)}</p>`:''}${t.completed_by?`<p class="sub">结果提交：${esc(t.completed_at)} · ${esc(who(t.completed_by))}；复核：${esc(who(t.reviewer))}</p>`:''}<div class="actions">${buttons}</div></article>`;
 }).join('')}</section><div><section class="panel"><h2>交接事项</h2><p class="sub">接收只表示知悉；责任接续需要另行明确确认，且不能完成任务。</p>${i.handovers.map(h=>`<div class="record"><p>${chip(h.status)} ${esc(who(h.receiver))}</p><p class="break">${esc(h.observation)}</p><p class="sub break">${esc(h.request)}</p>${h.status==='待接收'&&h.receiver===actor()?btn('ack','确认接收',`data-id="${esc(h.handover_id)}"`,'small primary'):''}${i.takeover_options?.some(x=>x.handover_id===h.handover_id)?btn('takeover','确认接续责任',`data-id="${esc(h.handover_id)}"`,'small primary'):''}</div>`).join('')||'<p class="empty">暂无可见交接</p>'}</section><section class="panel"><h2>核验依据与复核</h2>${i.evidence.map(e=>`<div class="record"><span class="label">第${e.cycle}轮 · ${esc({note:'文字说明',reference:'位置引用'}[e.kind]||e.kind)}</span><p class="break">${esc(e.value)}</p><p class="sub">${esc(who(e.actor))} · ${esc(e.at)}</p></div>`).join('')||'<p class="sub">暂无可见依据</p>'}${i.reviews.map(r=>`<div class="record"><span class="chip">${esc({task:'任务复核',workflow:'最终复核'}[r.scope]||r.scope)} / ${esc({approve:'通过',reject:'退回',waive:'豁免'}[r.decision]||r.decision)}</span><p>${esc(r.reason)}</p><p class="sub">${esc(who(r.actor))} · 第${r.cycle}轮</p></div>`).join('')}</section></div></div>`;
}
function field(name,label,value='',type='text',required=true,wide=false){return `<label class="${wide?'wide':''}">${esc(label)}${type==='textarea'?`<textarea name="${esc(name)}" ${required?'required':''} maxlength="6000">${esc(value)}</textarea>`:`<input name="${esc(name)}" type="${esc(type)}" value="${esc(value)}" ${required?'required':''} ${type==='password'?'autocomplete="new-password"':''} maxlength="${type==='password'?'128':'1000'}">`}</label>`;}
function selectField(name,label,options,value='',required=true){return `<label>${esc(label)}<select name="${esc(name)}" ${required?'required':''}>${options.map(o=>`<option value="${esc(o[0])}" ${String(o[0])===String(value)?'selected':''}>${esc(o[1])}</option>`).join('')}</select></label>`;}
function dialog(title,html){$('dialog-title').textContent=title;$('dialog-content').innerHTML=html;$('dialog').showModal();updatePendingNotices();if(typeof M3!=='undefined')M3.open();}
function formMarkup(id,fields,extra=''){return `<form id="${id}"><div class="form-grid">${fields}</div>${extra}<p class="error" id="form-error" role="alert" tabindex="-1"></p><div data-pending-placeholder></div><div class="form-footer">${btn('refresh-form','刷新状态并保留输入')}${btn('close-dialog','取消')}<button type="submit" class="primary">确认提交</button></div></form>`;}
function peopleOptions(){return S.directory.filter(u=>u.role!=='viewer').map(u=>[u.id,u.display_name+' · '+labels[u.role]]);}
async function newFlow(kind,quick=false){
 if(typeof Maintenance!=='undefined'&&Maintenance.isRole())return Maintenance.newFlow();
 const residents=quick&&kind==='maintenance'?[]:(await api('/records/residents')).items;
 const tpls=S.templates.filter(t=>t.kind===kind);S.formTemplates=tpls;S.formKind=kind;S.formQuick=quick;
 let fields='';
 if(quick&&kind==='maintenance'){
  fields=`<input type="hidden" name="template" value="0"><div class="wide notice"><strong>${esc(tpls[0]?.title||'设施维修')} v${esc(tpls[0]?.version||'')}</strong><p class="sub">公共维修 · 普通优先级默认可核对；没有真实期限可留空。</p></div>`;
  fields+=field('location','位置')+field('title','设备 / 设施或维修事项')+field('equipment_id','设备编号（可选）','','text',false);
  fields+=field('description','故障说明','','textarea',true,true);
  fields+=field('due_at','约定期限（可选；未约定留空）','','text',false);
  fields+=selectField('owner_user_id','执行负责人',peopleOptions(),S.user.id);
  fields+=selectField('priority','人工优先级',[['普通','普通'],['优先','优先']],'普通');
 }else{
  fields=selectField('template','流程模板',tpls.map((t,n)=>[n,t.title+' v'+t.version]),0);
  fields+=selectField('resident_id','关联长者',[["",kind==='incident'?'请选择长者':'公共事项（无长者）'],...residents.map(r=>[r.resident_id,r.display_name+' · '+r.resident_id])],'',kind==='incident');
  fields+=field('title','事项标题')+field('location','位置');
  if(kind==='maintenance')fields+=field('equipment_id','设备编号（可选）','','text',false);
  fields+=field('description',kind==='rectification'?'检查事实':'观察事实','','textarea',true,true);
  fields+=field('due_at',kind==='maintenance'?'人工约定期限（可选；未约定留空）':'人工约定期限（YYYY-MM-DD HH:mm）','','text',kind!=='maintenance');
  fields+=selectField('owner_user_id','执行负责人',peopleOptions(),S.user.id);
  fields+=selectField('priority','人工优先级',[['普通','普通'],['优先','优先']],'普通');
  if(kind==='incident')fields+=field('occurred_at','发生时间（YYYY-MM-DD HH:mm）')+selectField('category','人工事件分类',(S.schemas.find(s=>s.id==='incidents').fields.find(f=>f.key==='category').values).map(x=>[x,x]),'其他');
  if(kind==='rectification')fields+=field('inspection_ref','原检查记录引用')+field('requirement_ref','依据引用（可选）','','text',false);
 }
 dialog(quick?'快速报修':'新建'+kinds[kind],formMarkup('flow-form',fields,`<p class="sub">时间均按机构时区 ${esc(S.facility.timezone)} 填写；未知时间保持空白，不自动生成默认期限或实际完成时间。</p>`));
}

let pendingCommand=null;
async function commandForm(type,taskId=''){
 if(typeof Maintenance!=='undefined'&&Maintenance.isRole())return Maintenance.commandForm(type,taskId);
 pendingCommand={type,taskId};
 if(type==='start_task'){await sendCommand(type,{task_id:taskId});return;}
 if(type==='submit_workflow'&&S.current.kind!=='incident'){await sendCommand(type,{});return;}
 let fields='';
 if(type==='add_evidence')fields=selectField('kind','依据类型',[['note','文字说明'],['reference','安全存放位置引用']],'note')+field('value','核验依据','','textarea',true,true);
 if(type==='submit_task'){
  const step=S.current.steps?.find(x=>x.task_id===taskId),hasEvidence=S.current.evidence?.some(e=>e.task_id===taskId&&e.cycle===S.current.cycle),needsEvidence=Boolean(step?.evidence&&!hasEvidence);
  fields=field('result','维修 / 工作结果','','textarea',true,true);
  fields+=selectField('evidence_kind','依据类型',[['note','文字说明'],['reference','安全存放位置引用']],'note',needsEvidence);
  fields+=field('evidence_value',needsEvidence?'核验依据':'核验依据（可选）','','textarea',needsEvidence,true);
  if(S.current.kind==='maintenance'){
   fields+=field('performed_at','实际完成时间（可选；YYYY-MM-DD HH:mm）','','text',false);
   fields+=field('performed_source','实际完成时间来源（填写时间时必填）','','text',false);
  }
 }
 if(['review_task','review_workflow'].includes(type))fields=selectField('decision','复核结果',[['approve','通过'],['reject','退回']],'approve')+field('reason','核验意见','','textarea',true,true);
 if(type==='review_workflow'){
  const opts=S.current.tasks.map(t=>`<option value="${esc(t.task_id)}">${esc(t.title)}</option>`).join('');
  fields+=`<label data-reject-only hidden>退回时需重做的任务<select name="task_id" disabled>${opts}</select></label>`;
 }
 if(type==='reopen_workflow')fields+=selectField('task_id','重开时需重做的任务',S.current.tasks.map(t=>[t.task_id,t.title]),S.current.tasks[0]?.task_id);
 if(type==='reopen_workflow'||type==='waive_task')fields+=field('reason','理由','','textarea',true,true);
 if(type==='set_task_exception')fields=selectField('status','异常状态',[['暂缓','暂缓'],['拒绝','拒绝'],['不适用','不适用'],['已取消','已取消']],'暂缓')+field('reason','原因','','textarea',true,true);
 if(type==='create_handover')fields=selectField('receiver_user_id','指定接班账户',peopleOptions())+field('shift','班次（可选）','','text',false)+field('observation','当前情况','','textarea',true,true)+field('request','需要接续的事项','','textarea',true,true);
 if(type==='submit_workflow')fields=selectField('harm','人工核对的损伤结论',['未发现损伤','需专业处理','转外院','其他'].map(x=>[x,x]))+field('harm_source','专业原记录引用','','textarea',true,true);
 if(type==='share_repair')fields=selectField('recipient_user_id','指定后勤账户',S.directory.filter(u=>u.role==='maintenance').map(u=>[u.id,u.display_name]))+field('location','维修位置（不写长者姓名）')+field('equipment','设备 / 设施')+field('fault','故障说明（仅维修必要内容）','','textarea',true,true)+field('access_window','入室时间或安排')+field('contact_role','联系岗位（不填私人联系方式）')+field('reason','发布理由','','textarea',true,true);
 if(type==='revoke_repair')fields=field('reason','撤回理由；已接续责任将由当前主管接回','','textarea',true,true);
 if(type==='takeover_task')fields=selectField('handover_id','已接收的交接',S.current.takeover_options.map(x=>[x.handover_id,x.handover_id+' · '+x.task_id]))+field('reason','确认接续理由','','textarea',true,true);
 if(type==='assign_task')fields=selectField('owner_user_id','新的执行账户',peopleOptions(),S.user.id)+field('reason','分派理由 / 旧身份核对说明','','textarea',true,true);
 const names={share_repair:'发布后勤必要信息',revoke_repair:'撤回后勤信息范围',takeover_task:'确认接续任务责任',add_evidence:'补充核验依据',submit_task:'登记结果与依据并提交复核',review_task:'任务复核',review_workflow:'最终复核',reopen_workflow:'重开流程',waive_task:'明确豁免',set_task_exception:'登记异常',create_handover:'建立交接',submit_workflow:'提交最终复核',assign_task:'分派任务'};
 dialog(names[type],formMarkup('command-form',fields,(type==='share_repair'?'<label class="notice warn"><input type="checkbox" required> 已核对仅含维修必要信息，不含健康记录、长者姓名或私人联系方式。系统不会自动匿名化。</label>':'')+'<p class="sub">服务器记录当前登录账户及时间。实际完成时间未知时留空；结果提交时间由服务器另行记录。</p>'));
 if(type==='review_workflow'){
  const form=$('command-form'),decision=form.elements.decision,target=form.querySelector('[data-reject-only]'),task=form.elements.task_id;
  const sync=()=>{const reject=decision.value==='reject';target.hidden=!reject;task.disabled=!reject;if(!reject)task.selectedIndex=0;};
  decision.addEventListener('change',sync);sync();
  if(typeof M3!=='undefined')M3.baseline=JSON.stringify(M3.values());
 }
}

async function sendCommand(type,data){
 const body={type,expected_workspace_revision:S.revision,data};
 if(type!=='start'&&type!=='publish_template'){body.instance_id=S.current.id;body.expected_revision=S.current.revision;}
 if(typeof M3!=='undefined'&&M3.ref&&$('dialog').open)body.draft_ref=M3.ref;
 const result=await S.tracker.submit('/commands',body,'command');
 if(typeof M3!=='undefined')M3.reset();
 S.revision=result.revision;if($('dialog').open)$('dialog').close();toast('已保存到本机数据库 · 版本 '+result.revision);
 if(result.instance_id){S.current={id:result.instance_id};S.page='detail';}if(await loadPage())focusPageHeading();
}
// 老人档案只消费服务端投影与允许编辑字段；隐藏字段不生成空输入。
const ResidentProfile={
 sensitive:new Set(['id_number','guardian_phone']),
 groups:[['基本信息',['resident_id','display_name','sex','age','id_number','birthplace','household_location','former_occupation','unit','bed']],['护理信息',['care_level','dementia_status','what_matters','support','note']],['监护人信息',['guardian_name','guardian_phone']]],
 allowed(schema,row={}){const explicit=S.recordAllowedFields;if(Array.isArray(explicit))return schema.fields.filter(f=>explicit.includes(f.key)&&f.key!=='facility_id');return schema.fields.filter(f=>f.key!=='facility_id'&&(reviewer()||Object.hasOwn(row,f.key)));},
 mask(value){const text=String(value??'');return text?text.length>4?'••••'+text.slice(-4):'••••':'未填写';},
 grouped(fields,render){const covered=new Set();let html=this.groups.map(([name,keys])=>{const fs=keys.map(k=>fields.find(f=>f.key===k)).filter(Boolean);fs.forEach(f=>covered.add(f.key));return fs.length?`<fieldset class="resident-group wide"><legend>${name}</legend><div class="form-grid">${fs.map(render).join('')}</div></fieldset>`:'';}).join('');const rest=fields.filter(f=>!covered.has(f.key));if(rest.length)html+=`<fieldset class="resident-group wide"><legend>其他档案信息</legend><div class="form-grid">${rest.map(render).join('')}</div></fieldset>`;return html;},
 form(schema,row){return this.grouped(this.allowed(schema,row),f=>{let h=f.type==='enum'?selectField(f.key,f.label,[['','未填写'],...(f.values||[]).map(v=>[v,v])],row[f.key]??'',f.required):field(f.key,f.label,row[f.key]??'',this.sensitive.has(f.key)?'password':f.key==='age'||f.type==='number'?'number':['note','what_matters','support'].includes(f.key)?'textarea':'text',f.required,['note','what_matters','support'].includes(f.key));if(f.key==='age')h=h.replace('type="number"','type="number" min="0" max="130" step="1"');if(this.sensitive.has(f.key)){h=h.replace('autocomplete="new-password"','autocomplete="off"');h=h.replace('</label>',`${btn('resident-reveal-input','显示 / 隐藏',`data-key="${f.key}"`,'small')}<small>仅当前授权档案字段；默认遮盖。</small></label>`);}if(f.key==='care_level')h=h.replace('</label>','<small>按机构已确认等级填写，不自动分级。</small></label>');return h;});},
 details(row,context='profile'){const schema=S.schemas.find(x=>x.id==='residents');const fs=(schema?.fields||[]).filter(f=>f.key!=='facility_id'&&Object.hasOwn(row,f.key));return this.grouped(fs,f=>`<div class="resident-fact"><span class="sub">${esc(f.label)}</span><div class="break">${this.sensitive.has(f.key)?`<span id="resident-${context}-${f.key}">${esc(this.mask(row[f.key]))}</span>${row[f.key]?btn('resident-sensitive','查看',`data-key="${f.key}" data-context="${context}" aria-expanded="false"`,'small'):''}`:esc(row[f.key]??'未填写')||'未填写'}</div></div>`);}
};
async function editResidentProfile(id){const response=await api('/records/residents');S.recordAllowedFields=response.allowed_fields;S.records=response.items;S.revision=response.revision;S.table='residents';const index=S.records.findIndex(r=>r.resident_id===id);if(index<0)throw Error('档案不在当前授权范围内');recordForm(index);}
async function renderRecords(){
 if(S.user.role==='maintenance'&&S.table==='residents')S.table='workorders';
 const r=await api('/records/'+S.table);S.revision=r.revision;S.records=r.items;S.recordAllowedFields=r.allowed_fields;S.recordCanWrite=r.can_write;
 const schema=S.schemas.find(s=>s.id===S.table);const keys=S.table==='residents'?['display_name','sex','age','bed','care_level','dementia_status'].filter(k=>!Array.isArray(r.allowed_fields)||r.allowed_fields.includes(k)):(r.items[0]?Object.keys(r.items[0]):schema.fields.map(f=>f.key)).filter(x=>x!=='facility_id').slice(0,6);
 const label=k=>schema.fields.find(f=>f.key===k)?.label||k;
 const writable=(r.can_write??reviewer())&&['residents','stays','assessments','plans'].includes(S.table);
 $('content').innerHTML=`<section class="panel"><div class="panel-head"><div><h2>${S.table==='residents'?'老人档案':'已授权业务记录'}</h2><p class="sub">${r.items.length} 条可见记录 · v${S.revision}</p></div><div class="actions">${S.table==='residents'&&typeof residentExcelButtons==='function'?`<div class="ui-btn-group" role="group" aria-label="档案导入导出">${residentExcelButtons(writable)}</div>`:''}${writable?btn('new-record',S.table==='residents'?'+ 新增老人':'+ 新增'+tableNames[S.table],'','primary'):''}</div></div><div class="filters"><label>数据表 <select id="record-table" class="filter">${Object.entries(tableNames).map(([k,v])=>`<option value="${k}" ${S.table===k?'selected':''}>${v}</option>`).join('')}</select></label><div class="ui-btn-group" role="group" aria-label="刷新操作">${btn('refresh','刷新')}</div></div><div class="table-wrap"><table><thead><tr>${keys.map(k=>`<th>${esc(label(k))}</th>`).join('')}<th>查看</th></tr></thead><tbody>${r.items.map((row,n)=>`<tr>${keys.map(k=>`<td>${esc(S.table==='residents'&&(row[k]==null||row[k]==='')?'未填写':row[k])}</td>`).join('')}<td>${btn('record-detail','详情',`data-index="${n}"`,'small')}${writable?btn('edit-record','编辑',`data-index="${n}"`,'small'):''}${reviewer()&&['incidents','workorders','corrections'].includes(S.table)&&!['已复核','已关闭'].includes(row.status)?btn('continue-row','转入协作流程',`data-index="${n}"`,'small'):''}</td></tr>`).join('')||`<tr><td colspan="7"><div class="empty">没有可见记录</div></td></tr>`}</tbody></table></div></section>${S.table==='residents'?'<div id="resident-excel-import"></div>':''}${exportPanel(schema)}`;
}
function recordForm(index=-1){
 const schema=S.schemas.find(s=>s.id===S.table),row=index<0?(S.table==='residents'?{resident_id:'RES-'+crypto.randomUUID().replace(/-/g,'').slice(0,12).toUpperCase()}:{}):S.records[index];
 let fields=S.table==='residents'?ResidentProfile.form(schema,row):schema.fields.filter(f=>f.key!=='facility_id').map(f=>f.type==='enum'?selectField(f.key,f.label,[['','未填写'],...f.values.map(v=>[v,v])],row[f.key]||'',f.required):field(f.key,f.label,row[f.key]??'',f.type==='number'?'number':f.key==='note'||f.key==='measure'?'textarea':'text',f.required,['note','measure'].includes(f.key))).join('');
 fields+=field('_reason','登记 / 更正理由','','textarea',true,true);S.editingRecord=row;
 dialog((index<0?'新增':'编辑')+schema.name,formMarkup('record-form',fields,'<p class="sub">日期按 YYYY-MM-DD、时间按 YYYY-MM-DD HH:mm 填写。评估和计划只登记已由人员确认的内容，系统不生成医疗判断。</p>'));
 if(index>=0)$('record-form').elements[schema.idKey].readOnly=true;
}
async function renderUsers(){
 S.accounts=await api('/users');
 $('content').innerHTML=`<section class="panel"><div class="panel-head"><div><h2>账户与授权范围</h2><p class="sub">角色和范围每次请求均重新核对。改权或停用会撤销该账户现有会话。</p></div>${btn('new-user','创建账户','','primary')}</div><div class="table-wrap"><table><thead><tr><th>账号</th><th>显示名</th><th>角色</th><th>照料区</th><th>状态</th><th>操作</th></tr></thead><tbody>${S.accounts.map((u,n)=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${labels[u.role]}</td><td>${esc(u.units.join('、')||'无长者授权')}</td><td>${u.active?'启用':'停用'}</td><td>${btn('edit-user','调整权限',`data-index="${n}"`,'small')}</td></tr>`).join('')}</tbody></table></div></section><div class="notice warn">* 表示全部照料区。后勤默认仅公共维修；房间维修须向指定后勤发布最小必要信息，不开放长者档案；同名员工仍由不同账户 ID 区分。</div>`;
}
function userForm(index=-1){
 S.editingUser=index<0?null:S.accounts[index];let f='';const u=S.editingUser;
 if(!u)f=field('username','账号（小写英文）')+field('display_name','显示名')+field('password','初始密码（至少12字符）','','password');
 f+=selectField('role','角色',Object.entries(labels),u?.role||'caregiver')+field('units','照料区（英文逗号分隔；*为全部）',u?.units.join(',')||'2F','text',false);
 if(u)f+=selectField('active','账号状态',[['true','启用'],['false','停用']],String(u.active));
 dialog(u?'调整账户权限':'创建账户',formMarkup('user-form',f));
}
async function renderImports(){
 $('content').innerHTML=tableImportPanel()+'<section class="panel"><h2>本人的近期导入记录</h2><div id="import-history">正在读取…</div></section>'+ (S.user.role==='admin'?`<section class="panel"><h2>整份 JSON 历史迁移（仅空工作区）</h2><p class="sub">与表格追加不同：仅允许导入同编号的空机构工作区，不覆盖已有业务。</p><input class="file" type="file" id="import-file" accept=".json,application/json"><div id="import-report"></div></section><section class="panel"><h2>业务 JSON 与整库备份</h2><p class="sub">JSON不含账号、登录会话、实际附件或表格原件。完整SQLite备份由包内“备份当前数据.command”保存到本机固定备份目录，不提供数据库公开下载。</p><div class="form-footer">${btn('export','导出 JSON 业务工作区','','primary')}</div><p class="sub">M3启动前会检查旧库并生成升级前快照，再执行受控结构升级；同磁盘快照仍需由你保管独立副本，不等于自动异地备份。</p></section>`:'')+`<div class="notice warn">只使用合成资料。表格导入不生成认证的历史审批；需要接续的事件、维修、整改在记录中心明确“转入协作流程”并选择账户。旧v0.3文件不与本数据库同步。</div>`;
 await loadImportHistory();
}
async function previewImport(file){
 if(!file)return;if(file.size>5_000_000)throw Error('文件超过5MB候选版限制');
 const data=await api('/import/preview','POST',{raw_text:await file.text()});S.importPreview=data;S.revision=data.revision;
 $('import-report').innerHTML=`<div class="notice"><h3>预览已完成，尚未写入业务库</h3><p>原版本 ${esc(data.report.source_version)}；${data.report.flows} 个流程；${data.report.errors} 项错误、${data.report.warnings} 项提示。</p><p class="sub break">SHA256 ${esc(data.sha256)}</p><pre>${esc(JSON.stringify(data.report.counts,null,2))}</pre><p class="sub">错误行原样保留，需人工核对；旧身份不升级为认证身份。</p>${btn('commit-import','确认导入空工作区','','primary')}</div>`;
}
async function renderAudit(){
 const rows=await api('/audit');
 $('content').innerHTML=`<section class="panel"><h2>服务端操作审计</h2><p class="sub">最近 ${rows.length} 条。服务器生成 UTC 时间和账户 ID；接口和数据库触发器禁止修改/删除。拥有数据库管理权限的人仍可能改库，不是防篡改认证。</p><div class="table-wrap"><table><thead><tr><th>时间 UTC</th><th>账户</th><th>操作</th><th>对象</th><th>结果</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.at)}</td><td>${esc(who('account:'+r.user_id))}</td><td>${esc(r.action)}</td><td>${esc(r.entity_id)}</td><td>${esc(r.outcome)}</td></tr>`).join('')}</tbody></table></div></section>`;
}
document.addEventListener('submit',async e=>{
 if(e.target.id==='today-filter')return;
 e.preventDefault();const form=e.target;if(!(form instanceof HTMLFormElement))return;const b=form.querySelector('button[type=submit]');if(b)b.disabled=true;
 const values=Object.fromEntries(new FormData(form));
 const epoch=S.authEpoch||0;const err=form.id==='login-form'?form.querySelector('#login-error'):form.querySelector('#form-error');if(err)err.textContent='';
 try{
 if(typeof Departments!=='undefined'&&await Departments.handleSubmit(form))return;
 if(typeof Maintenance!=='undefined')Maintenance.prepareValues(form,values);
 if(form.id==='login-form'){const r=await api('/auth/login','POST',values);S.csrf=r.csrf;await init();}
 else if(form.id==='flow-form'){const tpl=S.formTemplates[Number(values.template)];delete values.template;await sendCommand('start',{...values,template_id:tpl.id,template_version:tpl.version});}
 else if(form.id==='command-form'){const data={...values};if(pendingCommand.taskId)data.task_id=pendingCommand.taskId;await sendCommand(pendingCommand.type,data);}
 else if(form.id==='record-form'){const reason=values._reason;delete values._reason;const schema=S.schemas.find(s=>s.id===S.table);for(const f of schema.fields)if(Object.hasOwn(values,f.key)&&f.type==='number'&&values[f.key]!=='')values[f.key]=Number(values[f.key]);const r=await S.tracker.submit('/records/'+S.table,{expected_workspace_revision:S.revision,row:values,reason,...(M3.ref?{draft_ref:M3.ref}:{})},'record');S.revision=r.revision;M3.reset();$('dialog').close();toast('记录已保存');if(await loadPage())focusPageHeading();}
 else if(form.id==='user-form'){values.units=values.units.split(',').map(s=>s.trim()).filter(Boolean);if(S.editingUser){values.active=values.active==='true';await api('/users/'+S.editingUser.id,'PATCH',values);}else await api('/users','POST',values);$('dialog').close();S.directory=await api('/directory');toast('账号配置已保存');await renderUsers();focusPageHeading();}
 else if(form.id==='password-form'){await api('/auth/password','POST',values);showLogin('密码已更新，全部旧会话已撤销。请使用新密码登录。');}
 }catch(error){if(epoch===(S.authEpoch||0)){if(err)showFormError(error,err);else if(form.isConnected)toast(error.message);}}
 finally{if(b&&b.isConnected)b.disabled=false;updatePendingNotices();}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b)return;const a=b.dataset.action;const errorElement=$('dialog').open?$('dialog').querySelector('#form-error'):null,epoch=S.authEpoch||0;
 try{
 if(typeof Departments!=='undefined'&&await Departments.handleClick(b))return;
 if(typeof Maintenance!=='undefined'&&await Maintenance.handle(a,b))return;
 if(['close-dialog','logout','nav','password'].includes(a)&&typeof Departments!=='undefined'&&!Departments.confirmLeave())return;
 if(a==='close-dialog')$('dialog').close();
 else if(a==='nav'){S.departmentMaintenance=false;S.page=b.dataset.page;await loadPage();}
 else if(a==='maintenance-back'){S.page='flows';S.filter='';S.departmentMaintenance=true;await loadPage();}
 else if(a==='refresh')await loadPage();
 else if(a==='logout'){await api('/auth/logout','POST',{});showLogin();}
 else if(a==='demo-switch-open'){const d=await api('/auth/demo-accounts');if(!d?.enabled){toast('当前非栖云颐养合成演示目录，请使用账号密码切换');return;}dialog('切换本地演示岗位会话',`<p class="sub">每次切换均在服务端签发真实角色会话与照料区范围（例如照护师仅见本人分派长者，后勤维保不可见长者隐私档案，管理员亦不可自修自验）：</p><div class="actions" style="flex-wrap:wrap;gap:10px;margin:14px 0">${d.accounts.map(u=>btn('demo-switch-exec',`${u.display_name}（${u.role_label}）`,`data-username="${esc(u.username)}"`,u.username===S.user?.username?'primary':'')).join('')}</div>${btn('close-dialog','取消')}`);}
 else if(a==='demo-switch-exec'){const r=await api('/auth/demo-switch','POST',{username:b.dataset.username});if($('dialog')?.open)$('dialog').close();S.authEpoch=(S.authEpoch||0)+1;if(typeof Departments!=='undefined')Departments.reset();if(typeof Maintenance!=='undefined')Maintenance.reset();S.csrf=r.csrf;await init();toast('已切换为：'+S.user.display_name);}
 else if(a==='password')dialog('修改密码',formMarkup('password-form',field('current_password','当前密码','','password')+field('new_password','新密码（至少12字符）','','password')));
 else if(a==='open-flow'){S.current={id:b.dataset.id};S.page='detail';await loadPage();}
 else if(a==='new-flow')await newFlow(b.dataset.kind);
 else if(a==='quick-repair')await newFlow('maintenance',true);
 else if(a==='command')await commandForm(b.dataset.type,b.dataset.task||'');
 else if(a==='ack')await sendCommand('ack_handover',{handover_id:b.dataset.id});
 else if(a==='resident-profile-edit')await editResidentProfile(b.dataset.id);
 else if(a==='resident-reveal-input'){const input=$('record-form')?.elements.namedItem(b.dataset.key);if(input&&ResidentProfile.sensitive.has(b.dataset.key))input.type=input.type==='password'?'text':'password';}
 else if(a==='resident-sensitive'){const row=b.dataset.context==='dialog'?S.recordDetailRow:S.profile?.resident,key=b.dataset.key,node=$('resident-'+b.dataset.context+'-'+key);if(row&&Object.hasOwn(row,key)&&ResidentProfile.sensitive.has(key)&&node){const reveal=b.getAttribute('aria-expanded')!=='true';node.textContent=reveal?String(row[key]):ResidentProfile.mask(row[key]);b.setAttribute('aria-expanded',String(reveal));b.textContent=reveal?'隐藏':'查看';}}
 else if(a==='new-record')recordForm();
 else if(a==='edit-record')recordForm(Number(b.dataset.index));
 else if(a==='record-detail')showRecordDetail(S.table,S.records[Number(b.dataset.index)]);
 else if(a==='new-user')userForm();
 else if(a==='edit-user')userForm(Number(b.dataset.index));
 else if(a==='refresh-form'){
 if(S.current&&S.page==='detail'){const x=await api('/workflows/'+S.current.id);S.current=x.item;S.revision=x.revision;}else S.revision=(await api('/workflows')).revision;
 toast('最新版本已读取，表单输入保留。请核对后再提交。');
 }
 else if(a==='commit-import'){if(!S.importPreview)throw Error('请先预览文件');await api('/import/commit','POST',{preview_id:S.importPreview.preview_id,source_sha256:S.importPreview.sha256,expected_workspace_revision:S.revision,confirm:true});toast('导入已提交；原文件保留，历史身份未经认证');S.page='flows';if(await loadPage())focusPageHeading();}
 else if(a==='export'){const data=await api('/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='CareOps_v04_workspace.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),3000);toast('已触发业务备份下载，请确认本地保存');}
 }catch(error){if(epoch===(S.authEpoch||0)){if(errorElement)showFormError(error,errorElement);else if(b.isConnected)toast(error.message);}}finally{updatePendingNotices();}
});
document.addEventListener('change',async e=>{
 try{
 if(e.target.id==='flow-filter'){S.filter=e.target.value;await renderFlows();}
 if(e.target.id==='record-table'){S.table=e.target.value;await renderRecords();}
 if(e.target.id==='import-file')await previewImport(e.target.files[0]);
 }catch(error){toast(error.message);if($('import-report'))$('import-report').innerHTML=`<p class="error">${esc(error.message)}</p>`;}
});
setInterval(async()=>{if(S.user)try{const m=await api('/auth/me');S.user=m.user;S.csrf=m.csrf;}catch(e){if(e.status===401)showLogin('登录已过期或授权已调整，请重新登录。');}},60000);
function updatePendingNotices(){
 wireToolbars();
 let m=null;try{m=S.tracker?.marker();}catch(e){toast(e.message);}
 const html=m?`<div class="notice warn"><p>存在一次尚未核对结果的提交。不要重新创建；先查询回执。刷新页面只保留编号，不保留表单正文。</p><div class="actions">${btn('check-submission','核对上次提交')}${btn('retry-submission','使用原编号重试')}${btn('cancel-submission','终止旧请求后重新填写')}</div></div>`:'';
 document.querySelectorAll('[data-pending-placeholder]').forEach(el=>el.innerHTML=html);
}
async function afterRecovered(r){
 if($('dialog').open)$('dialog').close();
 if(r?.revision!==undefined)S.revision=r.revision;
 if(r?.instance_id){S.current={id:r.instance_id};S.page='detail';}
 else if(r?.table){S.table=r.table;S.page='records';}
 else if(r?.preview_id){S.table=S.tablePreview?.report.table||S.table;S.page='records';S.tablePreview=null;}
 toast('已从原回执确认保存，不再重复写入');if(await loadPage())focusPageHeading();
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b||!['check-submission','retry-submission','cancel-submission'].includes(b.dataset.action))return;
 b.disabled=true;const errorElement=$('dialog').open?$('dialog').querySelector('#form-error'):null,epoch=S.authEpoch||0;
 try{
  if(!S.tracker)throw Error('请先登录原提交账户');
  if(b.dataset.action==='retry-submission'){await afterRecovered(await S.tracker.retry());return;}
  if(b.dataset.action==='cancel-submission'&&!confirm('服务器将先核对旧请求：已提交的不会撤销；未提交的旧编号将被终止，延迟请求也不能再写入。继续？'))return;
  const r=await (b.dataset.action==='cancel-submission'?S.tracker.cancel():S.tracker.inspect());
  if(r.state==='COMMITTED')await afterRecovered(r.result);
  else toast(r.state==='CANCELLED'?'旧请求已终止；原数据未回滚。请刷新状态、核对输入后重新提交。':r.state==='NONE'?'没有未确认提交。':'尚无提交回执。可保留原编号重试；页面刷新后无正文时，先终止旧编号再重填。');
 }catch(err){if(epoch===(S.authEpoch||0)){if(errorElement)showFormError(err,errorElement);else if(b.isConnected)toast(err.message);}}
 finally{if(b.isConnected)b.disabled=false;updatePendingNotices();}
});
document.addEventListener('visibilitychange',async()=>{if(document.visibilityState==='visible'&&S.user)try{await api('/auth/me');}catch(e){if(e.status===401)showLogin('登录已过期，请重新登录。');}});
document.addEventListener('DOMContentLoaded',()=>init().catch(()=>showLogin()),{once:true});
