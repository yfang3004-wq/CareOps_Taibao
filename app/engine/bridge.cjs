/* A trusted, fixed entry point. No scripts/paths are accepted from API callers. */
'use strict';
const W = require('./workflow.js');
let text='';
process.stdin.setEncoding('utf8');
process.stdin.on('data',s=>{text+=s;if(text.length>16000000)process.exit(2);});
process.stdin.on('end',()=>{
 try {
  const p=JSON.parse(text); let result;
  if(p.op==='empty') result=W.empty();
  else if(p.op==='restore') result=W.restore(p.workspace);
  else if(p.op==='validate') {W.assertStructure(p.workspace);result=W.C.validate(p.workspace).issues;}
  else if(p.op==='command') {
    const c=p.command;
    if(c.type!=='assign_task') result=W.command(p.workspace,c);
    else {
      result=structuredClone(p.workspace);W.assertStructure(result);
      const i=W.findInstance(result,c.facility_id,c.instance_id);
      if(i.status!=='active'||i.revision!==c.expected_revision)throw Error('流程已变更或不在办理中');
      if(W.C.stamp(c.at)<W.C.stamp(i.last_action_at))throw Error('操作时间早于流程最近操作；请核对机构时钟');
      const t=W.findTask(result,i,c.task_id);
      if(['待复核','已关闭'].includes(t.status))throw Error('待复核或已关闭任务不能改派');
      if(!c.owner||!c.reason)throw Error('改派须指定账户和原因');
      const before={owner:t.owner,revision:i.revision};t.owner=c.owner;i.revision++;i.last_action_at=c.at;
      result.audit.push({time:c.at,actor:c.actor,actor_type:'human-unverified',role:c.role,action:'assign_task',table:'workflow',id:i.id,facility:i.facility_id,request_id:c.request_id,reason:c.reason,before,after:{owner:t.owner,revision:i.revision}});
      result.flow.receipts.push({id:c.request_id,payload:W.canonical(c)});W.assertStructure(result);
    }
  }
  else if(p.op==='upsert') result=W.C.upsert(p.workspace,p.table,p.row,p.index,p.actor,p.reason);
  else if(p.op==='prepare_tabular') result=require('./tabular.cjs').prepare(p.workspace,p.table,p.rows);
  else if(p.op==='registry') result=W.C.R.schemas;
  else throw Error('未开放的服务端引擎操作');
  process.stdout.write(JSON.stringify({ok:true,result}));
 } catch(e){process.stdout.write(JSON.stringify({ok:false,error:String(e.message).slice(0,1000)}));}
});
