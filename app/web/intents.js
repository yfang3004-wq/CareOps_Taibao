/* One explicit submission intent, one stable request key. No business text stored
   in Web Storage. A server cancellation fence prevents delayed requests racing
   a user who has explicitly decided to re-enter an operation. */
(function(root){
'use strict';
class SubmissionTracker {
 constructor(api,storage,userId,uuid=()=>crypto.randomUUID()){
  this.api=api;this.storage=storage;this.userId=userId;this.uuid=uuid;
  this.key='CareOps.pending.v2.'+userId;this.memory=null;
 }
 marker(){
  let raw;try{raw=this.storage.getItem(this.key);}catch{throw Error('浏览器会话存储不可用；为避免无法追踪的重复写入，本页暂停提交。');}
  if(!raw)return null;
  let m;try{m=JSON.parse(raw);}catch{throw Error('提交标记损坏，请先核对数据库记录；不要盲目重交。');}
  if(m.user_id!==this.userId||!['command','record','table'].includes(m.kind)||typeof m.id!=='string')throw Error('提交标记无效，请人工核对。');
  return m;
 }
 clear(){this.storage.removeItem(this.key);this.memory=null;}
 async submit(path,body,kind){
  if(this.marker())throw Error('上次提交结果尚未确认。先核对回执，或明确终止旧请求，再开始新的提交。');
  const data=JSON.parse(JSON.stringify(body));const id=kind==='table'?data.preview_id:this.uuid();
  if(kind!=='table')data.request_id=id;
  const marker={user_id:this.userId,id,kind,path};
  try{this.storage.setItem(this.key,JSON.stringify(marker));}catch{throw Error('无法保存最小提交标记，本次未发出写入。');}
  this.memory={marker,data};
  try{const r=await this.api(path,'POST',data);this.clear();return r;}
  catch(e){if(e.status===422){this.clear();}else{e.submissionPending=true;}throw e;}
 }
 async inspect(){
  const m=this.marker();if(!m)return {state:'NONE'};
  try{
   const r=await this.api(m.kind==='table'?'/tabular/previews/'+encodeURIComponent(m.id):'/receipts/'+encodeURIComponent(m.id));
   if(m.kind==='table'){
    if(r.committed){this.clear();return {state:'COMMITTED',result:r.report.commit_result};}
    return {state:'NOT_FOUND',retry_available:!!this.memory,expired:r.expired};
   }
   this.clear();return {state:r.cancelled?'CANCELLED':'COMMITTED',result:r.result};
  }catch(e){if(e.status===404)return {state:'NOT_FOUND',retry_available:!!this.memory};throw e;}
 }
 async retry(){
  const memory=this.memory;
  const check=await this.inspect();
  if(check.state==='COMMITTED')return check.result;
  if(check.state==='CANCELLED'||check.state==='NONE')throw Error('旧请求已终止或已核对，请重新查看当前业务状态。');
  if(!memory||memory.marker.id!==this.marker()?.id)throw Error('原提交正文未保存在浏览器中；请先终止旧请求，再重新填写。');
  try{const result=await this.api(memory.marker.path,'POST',memory.data);this.clear();return result;}
  catch(e){e.submissionPending=true;throw e;}
 }
 async cancel(){
  const m=this.marker();if(!m)return {state:'NONE'};
  if(m.kind==='table'){
   try{await this.api('/tabular/previews/'+encodeURIComponent(m.id),'DELETE',{});this.clear();return {state:'CANCELLED'};}
   catch(e){if(e.status===409)return this.inspect();if(e.status===404){this.clear();return {state:'CANCELLED'};}throw e;}
  }
  const r=await this.api('/receipts/'+encodeURIComponent(m.id)+'/cancel','POST',{});
  this.clear();return {state:r.already_committed?'COMMITTED':'CANCELLED',result:r.result};
 }
}
if(typeof module!=='undefined'&&module.exports)module.exports={SubmissionTracker};else root.CareSubmission=SubmissionTracker;
})(globalThis);
