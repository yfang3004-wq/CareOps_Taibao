/* Entirely synthetic, deterministic scenarios. NOT institution data or a clinical SOP. */
(function(root){'use strict';
const W=typeof module!=='undefined'&&module.exports?require('./workflow.js'):root.CareWorkflow;
function demo(){let w=W.empty();w.asOf='2026-10-05';w.classification='合成演示（全部长者、人员、事实与流程虚构）';let sequence=0;const at='2026-10-04 09:00:00';
for(let n=1;n<=12;n++){const id='R'+String(n).padStart(3,'0'),unit=n<=4?'2F':n<=8?'3F':'4F';w.tables.residents.push(W.C.normalize(W.C.R.byId.residents,{facility_id:'DEMO-01',resident_id:id,display_name:'示例长者 '+String(n).padStart(2,'0'),unit,bed:unit+'-'+String(n).padStart(2,'0'),what_matters:'尊重本人选择与参与；纯演示信息'}));w.tables.stays.push(W.C.normalize(W.C.R.byId.stays,{facility_id:'DEMO-01',stay_id:'ST-'+id,resident_id:id,start_date:'2026-09-01',status:'在院',unit}));}
function run(type,d={}){const i=w.flow.instances.at(-1);w=W.command(w,{type,request_id:'DEMO-REQ-'+(++sequence),facility_id:'DEMO-01',actor:'执行员甲',role:'执行人员',at,...(i?{instance_id:i.id,expected_revision:i.revision}:{}),...d});return w.flow.instances.at(-1);}
function start(kind,title,d={}){return run('start',{template_id:'WFT-'+kind,template_version:'1.0',title,description:'全合成情景，仅验证记录、责任交接和复核，不代表真实处置建议。',owner:'执行员甲',resident_id:'R001',location:'示例机构二层',due_at:'2026-10-06 18:00:00',inspection_ref:'SYNTHETIC-CHECK',...d});}
function doneTask(id){run('start_task',{task_id:id});run('add_evidence',{task_id:id,kind:'note',value:'合成核验说明：仅用于软件流程测试'});run('submit_task',{task_id:id,result:'合成结果：已完成模板约定的信息记录并交复核'});run('review_task',{task_id:id,actor:'复核员乙',role:'主管复核',decision:'approve',reason:'已核对演示信息'});}
function finish(){const i=w.flow.instances.at(-1);i.steps.forEach(s=>doneTask(s.task_id));run('submit_workflow',{harm:'未发现损伤',harm_source:'SYNTHETIC-专业原记录引用'});run('review_workflow',{actor:'复核员乙',role:'主管复核',decision:'approve',reason:'本轮演示链条完成'});}
start('incident','01 已复核完成的异常事件');finish();
start('incident','02 待继续跟进的异常事件',{resident_id:'R002'});run('start_task',{task_id:w.flow.instances.at(-1).steps[0].task_id});
start('incident','03 已接班但任务尚未完成',{resident_id:'R003'});const handTask=w.flow.instances.at(-1).steps[1].task_id;
for(let n=0;n<8;n++){run('create_handover',{task_id:handTask,receiver:n%2?'接班员丁':'接班员丙',observation:'合成交接现状 '+(n+1),request:'按机构安排核对后续资料，交接确认不表示执行完成',shift:'演示班次'});if(n%2===0)run('ack_handover',{handover_id:w.flow.handover_links.at(-1).handover_id,actor:'接班员丙'});}
start('maintenance','04 处理中：公共区域设施维修',{resident_id:'',due_at:'2026-10-04 18:00:00'});run('start_task',{task_id:w.flow.instances.at(-1).steps[0].task_id});
start('maintenance','05 验收退回：补充核验依据',{resident_id:'R004'});let t=w.flow.instances.at(-1).steps[0].task_id;run('start_task',{task_id:t});run('add_evidence',{task_id:t,kind:'reference',value:'SYNTHETIC/EVIDENCE/REPAIR-05'});run('submit_task',{task_id:t,result:'演示维修完成，等待核验'});run('review_task',{task_id:t,actor:'复核员乙',role:'主管复核',decision:'reject',reason:'合成例：核验说明不足，请补充'});
start('maintenance','06 已验收完成的维修',{resident_id:''});finish();
start('rectification','07 必要任务完成，等待最终复核',{resident_id:'R005'});w.flow.instances.at(-1).steps.forEach(s=>doneTask(s.task_id));run('submit_workflow');
start('rectification','08 已超过约定期限的整改',{resident_id:'R006',due_at:'2026-10-04 17:00:00'});run('start_task',{task_id:w.flow.instances.at(-1).steps[0].task_id});
start('rectification','09 整改复核退回后重新办理',{resident_id:'R007'});t=w.flow.instances.at(-1).steps[0].task_id;run('start_task',{task_id:t});run('add_evidence',{task_id:t,kind:'note',value:'合成整改情况'});run('submit_task',{task_id:t,result:'合成整改结果'});run('review_task',{task_id:t,actor:'复核员乙',role:'主管复核',decision:'reject',reason:'合成例：效果核对还需补充'});
start('incident','10 已关闭后重新打开的事件',{resident_id:'R008'});finish();run('reopen_workflow',{task_id:w.flow.instances.at(-1).steps[1].task_id,actor:'主管戊',role:'主管复核',reason:'合成例：收到新资料，启动第二轮跟进'});
start('maintenance','11 必要任务取消但尚未获豁免',{resident_id:'R009'});run('set_task_exception',{task_id:w.flow.instances.at(-1).steps[0].task_id,status:'已取消',reason:'合成例：报修内容需核实，不能据此判为完成'});
return w;}
if(typeof module!=='undefined'&&module.exports)module.exports=demo;else root.CareDemo=demo;
})(globalThis);
