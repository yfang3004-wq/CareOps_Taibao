'use strict';
/* 事件详情只调整展示；动作条件和 data-action 沿用原 renderDetail，表单及服务端校验不变。 */
function incidentTaskButtons(i,t){
 const canWrite=S.user.role!=='viewer';
 const own=t.owner===actor(),active=i.status==='active',attrs=`data-task="${esc(t.task_id)}"`;
 let buttons='';
 if(canWrite&&active&&own){
 if(['待处理','暂缓','拒绝','不适用','已取消'].includes(t.status))buttons+=btn('command','接手并开始',`${attrs} data-type="start_task"`,'small primary');
 if(i.kind!=='maintenance'&&['待处理','执行中','待复核'].includes(t.status)||i.kind==='maintenance'&&t.status==='待复核')buttons+=btn('command',t.status==='待复核'?'补充依据':'添加依据',`${attrs} data-type="add_evidence"`,'small');
 if(t.status==='执行中')buttons+=btn('command',i.kind==='maintenance'?'登记结果与依据':'填写结果并提交',`${attrs} data-type="submit_task"`,'small primary');
 if(['待处理','执行中'].includes(t.status))buttons+=btn('command','暂缓 / 取消',`${attrs} data-type="set_task_exception"`,'small');
 if(i.resident_id&&!['已关闭','已取消','不适用'].includes(t.status))buttons+=btn('command','建立交接',`${attrs} data-type="create_handover"`,'small');
 }
 if(reviewer()&&active){
 if(t.status==='待复核'&&t.completed_by!==actor())buttons+=btn('command','任务复核',`${attrs} data-type="review_task"`,'small primary');
 if(!['待复核','已关闭'].includes(t.status))buttons+=btn('command','分派 / 重新绑定账户',`${attrs} data-type="assign_task"`,'small');
 if(i.kind!=='incident'&&['不适用','已取消'].includes(t.status))buttons+=btn('command','明确豁免',`${attrs} data-type="waive_task"`,'small');
 }
 return buttons;
}
function incidentFacts(items){
 return `<dl class="fields">${items.map(([label,value])=>`<dt>${esc(label)}</dt><dd class="break">${esc(value||'未记录')}</dd>`).join('')}</dl>`;
}
function incidentHistory(i){
 const evidence=i.evidence||[],reviews=i.reviews||[];
 const cycles=[...new Set([...evidence,...reviews].map(x=>String(x.cycle??'')))].sort((a,b)=>Number(b)-Number(a));
 if(!cycles.length)return '<p class="empty">暂无可见依据与复核记录</p>';
 return cycles.map(c=>`<section class="incident-history-cycle"><h3>${c?'第 '+esc(c)+' 轮':'轮次未记录'}${String(i.cycle)===c?' · 当前轮':''}</h3>${evidence.filter(e=>String(e.cycle??'')===c).map(e=>`<div class="record"><span class="label">${esc({note:'文字说明',reference:'位置引用'}[e.kind]||e.kind)} · ${esc(e.task_id||'流程依据')}</span><p class="break">${esc(e.value)}</p><p class="sub">${esc(who(e.actor))} · ${esc(e.at||'时间未记录')}</p></div>`).join('')}${reviews.filter(r=>String(r.cycle??'')===c).map(r=>`<div class="record"><span class="chip">${esc({task:'任务复核',workflow:'最终复核'}[r.scope]||r.scope)} / ${esc({approve:'通过',reject:'退回',waive:'豁免'}[r.decision]||r.decision)}</span><p class="break">${esc(r.reason)}</p><p class="sub">${esc(r.task_id||'流程')} · ${esc(who(r.actor))} · ${esc(r.at||'时间未记录')}</p></div>`).join('')}</section>`).join('');
}
function renderIncidentDetail(i){
 const s=i.subject||{},canWrite=S.user.role!=='viewer';
 const finalActions=`${canWrite&&i.status==='active'?btn('command','提交最终复核',`data-type="submit_workflow" ${i.can_submit?'':'disabled title="先完成下方缺项"'}`,'primary'):''}${i.can_review?btn('command','最终复核','data-type="review_workflow"','primary'):''}${reviewer()&&i.status==='closed'?btn('command','说明理由并重开','data-type="reopen_workflow"'):''}`;
 const tasks=(i.tasks||[]).map(t=>`<article class="task" id="task-${esc(t.task_id)}"><div class="task-head"><h3>${esc(t.title||t.task_id)}</h3>${chip(t.status)}</div><p class="sub">${esc(t.task_id)} · 执行负责人 ${esc(who(t.owner))}</p>${incidentFacts([['约定期限',t.due_at||'未约定'],['明确接手时间',t.accepted_at],['接手账户',t.accepted_by?who(t.accepted_by):'未记录'],['实际完成时间',t.performed_at||'未知／未登记'],['实际时间来源',t.performed_source||'未记录'],['结果提交时间',t.completed_at],['结果提交账户',t.completed_by?who(t.completed_by):'未记录'],['复核时间',t.reviewed_at],['复核账户',t.reviewer?who(t.reviewer):'未记录']])}${t.result?`<div class="incident-task-result"><span class="label">已登记结果</span><p class="break">${esc(t.result)}</p></div>`:''}<div class="actions">${incidentTaskButtons(i,t)}</div></article>`).join('');
 const handovers=(i.handovers||[]).map(h=>{const links=(i.handover_links||[]).filter(x=>x.handover_id===h.handover_id);return `<article class="record"><div class="task-head"><h3>${esc(h.handover_id)}</h3>${chip(h.status)}</div>${incidentFacts([['发送账户',who(h.sender)],['指定接收账户',who(h.receiver)],['建立时间',h.created_at],['接收时间',h.accepted_at],['约定期限',h.due_at||'未约定'],['班次',h.shift],['关联任务',links.map(x=>x.task_id).join('、')||'未记录']])}<span class="label">当前情况</span><p class="break">${esc(h.observation||'未记录')}</p><span class="label">需要接续的事项</span><p class="break">${esc(h.request||'未记录')}</p>${h.outcome?`<p class="break">登记结果：${esc(h.outcome)}</p>`:''}<div class="actions">${h.status==='待接收'&&h.receiver===actor()?btn('ack','确认接收',`data-id="${esc(h.handover_id)}"`,'small primary'):''}${i.takeover_options?.some(x=>x.handover_id===h.handover_id)?btn('takeover','确认接续责任',`data-id="${esc(h.handover_id)}"`,'small primary'):''}</div></article>`;}).join('');
 $('content').innerHTML=`<div class="actions">${btn('nav','返回流程列表','data-page="flows"')}${btn('refresh','刷新当前状态')}</div><section class="panel incident-summary"><div class="panel-head"><div><p class="overline">事件协作 / 当前授权详情</p><h2>${esc(i.title)}</h2><p class="sub">${esc(i.id)} · 第${esc(i.cycle)}轮 · 流程修订 ${esc(i.revision)}</p></div>${chip(i.status)}</div>${incidentFacts([['事件性质（登记 kind）',s.kind],['事件类别（登记 category）',s.category],['伤害情况（登记 harm）',s.harm],['发生时间',s.occurred_at],['登记时间',s.reported_at],['位置',s.location]])}<p class="sub">发生与登记是两个事实时间；空字段不推断。伤害情况是登记内容，不代表系统风险评级。时间按 ${esc(S.facility.timezone)} 核对。</p></section><div class="incident-detail-grid"><div class="incident-main"><section class="panel incident-next"><h2>当前下一步</h2><p class="sub">按钮按原账户、任务状态和责任条件显示；最终复核仍由独立账户办理。</p><div class="actions">${finalActions||'<span class="sub">当前没有可办理的最终提交／复核／重开动作。</span>'}</div>${canWrite&&i.status==='active'&&!i.can_submit?'<p class="sub">提交最终复核尚未开放：先完成下方缺项。</p>':''}${m3DetailHeader(i)}</section><section class="panel incident-tasks"><h2>执行任务</h2><p class="sub">逐项办理与独立复核；交接接收不等于任务已完成。保留模板实际任务，不合并必要任务。</p>${tasks||'<p class="empty">暂无可见任务</p>'}</section><section class="panel incident-handovers"><h2>交接事项</h2><div class="notice">确认接收只表示知悉，不转移执行责任、不完成任务。只有服务端提供接续选项时才显示“确认接续责任”；接收时间缺失不推断已接收。</div>${handovers||'<p class="empty">暂无可见交接</p>'}</section><section class="panel incident-history"><h2>核验依据与复核历史</h2><p class="sub">按真实轮次分组；旧轮次保留，不视作当前轮完成证据。</p>${incidentHistory(i)}</section></div><aside class="incident-context"><section class="panel"><h2>责任与上下文</h2>${incidentFacts([['流程负责人',who(i.owner)],['事件登记负责人',who(s.owner)],['事件登记账户',who(s.reporter)],['长者编号',i.resident_id||'公共事项'],['后续期限',s.followup_due||'未约定'],['事件登记状态',s.status]])}<p class="sub">仅展示当前授权字段，不计算年龄、风险或入住指标。</p></section><section class="panel"><h2>原始情况说明</h2><p class="break">${esc(s.description||'未记录')}</p><h3>已登记即时处理</h3><p class="break">${esc(s.immediate_action||'未记录')}</p><p class="sub break">${esc(i.template_note||'')}</p></section></aside></div>`;
}
