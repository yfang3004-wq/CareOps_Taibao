'use strict';
/* Repair states are a VIEW, not a rewrite of task, review or audit facts.
   Four labels everywhere: 待处理 → 处理中 → 待验收 → 已完成.
   Pauses, rejections and waivers remain secondary facts, never a fifth status.
*/
(function(root){
  const stages=[
    {key:'todo',label:'待处理'},
    {key:'doing',label:'处理中'},
    {key:'inspect',label:'待验收'},
    {key:'done',label:'已完成'}
  ];
  const legacy={queued:'todo',in_progress:'doing',waiting:'doing',pending_check:'inspect',pending_submission:'inspect',pending_review:'inspect',closed:'done',
    '待接单':'todo','待处理':'todo','维修中':'doing','暂缓／等待':'doing','暂停维修':'doing','修理结果待检查':'inspect',
    '待检查修理结果':'inspect','待负责人交验收':'inspect','待提交整单验收':'inspect','待整单验收':'inspect','待最终验收':'inspect','已验收':'done','已完成':'done'};
  function key(item){
    if(!item||typeof item!=='object')return 'todo';
    if(item.status==='closed')return 'done';
    if(item.status==='pending_review')return 'inspect';
    const declared=item.repair_display_stage;
    if(['todo','doing','inspect','done'].includes(declared))return declared;
    const tasks=Array.isArray(item.tasks)?item.tasks:[];
    const values=tasks.map(t=>t?.status);
    if(values.some(s=>s==='待复核'||s==='已关闭'))return 'inspect';
    if(values.some(s=>['执行中','暂缓','拒绝','已取消','不适用'].includes(s)))return 'doing';
    if(tasks.some(t=>t?.accepted_at))return 'doing';
    return legacy[item.business_stage]||legacy[item.stage]||'todo';
  }
  function label(item){return stages.find(s=>s.key===key(item)).label;}
  function note(item){
    const ts=Array.isArray(item?.tasks)?item.tasks:[];
    if(item?.repair_completion_note)return item.repair_completion_note;
    if(item?.status==='closed'&&ts.length&&ts.every(t=>['已取消','不适用'].includes(t.status)))return '已终止，非实际修复';
    if(ts.some(t=>t?.status==='暂缓'))return '暂缓／等配件等情况请查看原因';
    if(ts.some(t=>t?.status==='已取消'))return '有取消事项，需核对处置记录';
    return '';
  }
  function progress(item,escapeText=v=>String(v??'')){
    const current=stages.findIndex(s=>s.key===key(item));
    return `<ol class="repair-stages" aria-label="维修四阶段">${stages.map((s,idx)=>`<li class="repair-stage ${idx<current?'is-past':idx===current?'is-current':'is-future'}" ${idx===current?'aria-current="step"':''}><span class="repair-stage-num">${idx+1}</span><span>${escapeText(s.label)}</span></li>`).join('')}</ol>`;
  }
  const api={stages,key,label,note,progress};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.CareOpsMaintenanceStatus=api;
})(globalThis);
