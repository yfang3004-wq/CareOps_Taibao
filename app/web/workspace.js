'use strict';
/* 今日工作台仅呈现既有授权投影；筛选、导航和提交沿用原事件处理器。 */
function renderTodayWorkspace(r,options){
 const queueButtons=Object.entries(queueNames).map(([key,label])=>{
  const count=r.counts?.[key];
  return `<button type="button" data-action="today-queue" data-queue="${esc(key)}" class="workspace-queue ${r.queue===key?'selected':''}" aria-pressed="${r.queue===key?'true':'false'}"><span>${esc(label)}</span><strong>${esc(count===undefined||count===null?'未提供':count)}</strong></button>`;
 }).join('');
 const cells=(label,content)=>`<td data-label="${esc(label)}"><div class="workspace-cell">${content}</div></td>`;
 const rows=r.items.map(x=>{
  const openAttrs=`data-id="${esc(x.flow_id)}"`;
  const action=x.item_type==='closure'&&x.kind==='maintenance'&&S.user.role!=='viewer'&&x.owner===actor()
   ?btn('today-submit','提交最终复核',`${openAttrs} data-revision="${esc(x.flow_revision)}"`,'small primary')
   :btn('open-flow',S.user.role==='viewer'?'查看详情':'查看下一步',openAttrs,'small');
  return `<tr>${cells('需要处理的事项',`${btn('open-flow',x.title,openAttrs,'link')}<div class="sub">${esc(x.entity_id)} · ${esc(kinds[x.kind]||x.kind)}</div>`)}${cells('办理环节',esc(x.label))}${cells('状态',`${chip(x.status)}${x.overdue?'<span class="chip warn">超期未终结</span>':''}`)}${cells('负责人',esc(who(x.owner)))}${cells('期限',esc(x.due_at||'未约定'))}${cells('操作',action)}</tr>`;
 }).join('')||'<tr class="workspace-empty-row"><td colspan="6" class="workspace-empty"><div class="workspace-cell empty">当前授权队列与筛选下没有已登记事项；不代表全机构没有待办或风险。</div></td></tr>';
 const kindsOptions=[...new Set(S.templates.map(t=>t.kind))].map(k=>`<option value="${esc(k)}" ${S.todayFilter.kind===k?'selected':''}>${esc(kinds[k]||k)}</option>`).join('');
 return `<section class="panel workspace-panel"><div class="panel-head"><div><h2>今日工作台</h2><p class="sub">${esc(r.scope_note)}</p></div><div class="actions">${S.user.role!=='viewer'?btn('quick-repair','快速报修','','primary'):''}${btn('nav','完整流程','data-page="flows"')}</div></div><div class="workspace-queues" role="group" aria-label="授权待办队列">${queueButtons}</div><p class="sub workspace-count-note">数量来自同一筛选下的授权队列，各队列可能重叠，请勿相加；不是全机构工作量。</p><form id="today-filter" class="filters"><label>类型<select name="kind"><option value="">全部允许类型</option>${kindsOptions}</select></label>${selectField('owner_user_id','负责人',options,S.todayFilter.owner_user_id,false)}<label>期限不晚于<input name="due_before" type="date" value="${esc(S.todayFilter.due_before)}"></label><button type="submit">应用筛选</button>${btn('today-reset','清除筛选')}</form><div class="workspace-section-head"><h3>${esc(queueNames[r.queue]||r.queue)}</h3><p class="sub">核对时间 ${esc(r.as_of||'未提供')} · ${esc(r.timezone||'未提供')}；仅已登记事项，不是实时告警。</p></div><div class="table-wrap workspace-table-wrap"><table class="workspace-table"><thead><tr><th>需要处理的事项</th><th>办理环节</th><th>状态</th><th>负责人</th><th>期限</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div><div class="statusbar"><span>共 ${esc(r.total)} 项 · 第 ${esc(r.page)} 页</span><div class="actions">${btn('today-page','上一页',`data-page="${esc(r.page-1)}" ${r.page<=1?'disabled':''}`)}${btn('today-page','下一页',`data-page="${esc(r.page+1)}" ${r.page*r.page_size>=r.total?'disabled':''}`)}</div></div></section>`;
}
