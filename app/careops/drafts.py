"""Private explicit server drafts, not browser-local autosave or business records.
Access is revalidated on save/list/restore/use, no administrator read-all API.
Content is never logged/exported; full SQLite backups still contain drafts.
"""
from __future__ import annotations
import time, json
from copy import deepcopy
from typing import Literal
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, update, delete, func
from .database import drafts, utcnow
from .security import authenticate, actor, REVIEWERS, canonical, digest, fail, log
from . import policy

DAYS=7
MAX_ACTIVE=20
SUPPORTED_COMMANDS={'add_evidence','submit_task','review_task','review_workflow','reopen_workflow','waive_task','set_task_exception','create_handover','submit_workflow','assign_task','share_repair','revoke_repair','takeover_task'}
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class DraftRef(Strict):
    id:str=Field(pattern=r'^[A-Za-z0-9-]{16,48}$')
    revision:int=Field(ge=1,strict=True)
class DraftBody(Strict):
    kind:Literal['flow','command','record']
    context:dict=Field(max_length=8)
    values:dict=Field(max_length=50)
    expected_draft_revision:int=Field(ge=0,strict=True)
    expected_workspace_revision:int=Field(ge=0,strict=True)
class DeleteBody(Strict):revision:int=Field(ge=1,strict=True)

def scope_hash(u):return digest({k:u[k] for k in ('id','facility_id','role','units','active')})

def validate_content(u,ws,kind,context,values):
    from .app import COMMAND_FIELDS
    if any(not isinstance(v,str) or len(v)>100 for v in context.values()):fail(422,'草稿上下文仅允许短标识')
    if u['role']=='viewer':fail(403,'只读账户不保存业务草稿')
    if any(not isinstance(k,str) or not isinstance(v,str) or len(v)>6000 for k,v in values.items()):fail(422,'草稿字段必须是限长文本')
    if len(canonical([context,values]).encode('utf-8'))>40000:fail(422,'草稿过长，最多40KB文本')
    if kind=='flow':
        if set(context)!={'workflow_kind','template_id','template_version'}:fail(422,'新建流程草稿上下文无效')
        tpl=next((t for t in ws['flow']['templates'] if t['id']==context['template_id'] and t['version']==context['template_version']),None)
        if not tpl or tpl['kind']!=context['workflow_kind'] or tpl['kind'] not in policy.KINDS[u['role']]:fail(403,'流程模板不在当前权限范围')
        allowed=COMMAND_FIELDS['start']-{'template_id','template_version','existing_subject_id'}
        rid=values.get('resident_id','')
        if rid and rid not in policy.visible_residents(u,ws):fail(403,'草稿关联长者已不在当前权限范围')
        if u['role'] in {'maintenance','maint_executor','maint_supervisor'} and rid:fail(403,'后勤不可草拟长者关联流程')
        if u['role'] in {'maint_executor','maint_supervisor'}:
            policy.authorize_command(u,ws,{**values,'type':'start','template_id':context['template_id'],'template_version':context['template_version']})
    elif kind=='command':
        if set(context)!={'type','instance_id','task_id'} or context.get('type') not in SUPPORTED_COMMANDS:fail(422,'命令草稿上下文无效')
        typ=context['type'];allowed=COMMAND_FIELDS[typ]-({'task_id'} if context.get('task_id') else set())
        d={**values,'type':typ,'instance_id':context['instance_id']}
        if context.get('task_id'):d['task_id']=context['task_id']
        policy.authorize_command(u,ws,d)
        i=policy.get_flow(u,ws,context['instance_id'])
        if typ=='takeover_task':
            policy.takeover_target(u,ws,i,values.get('handover_id',''))
        if typ in {'share_repair','revoke_repair'}:
            if i['kind']!='maintenance' or not i.get('resident_id'):fail(403,'不是可发布的房间维修')
            task=next(t for t in ws['tables']['tasks'] if t['task_id']==i['steps'][0]['task_id'])
            if task['status'] in {'待复核','已关闭'}:fail(409,'原维修已进入复核，不恢复发布/撤回草稿')
            if u['role'] not in REVIEWERS and task['owner']!=actor(u):fail(403,'已不再负责此维修，原发布草稿不可恢复')
        if typ=='submit_workflow' and i['kind']!='incident':fail(422,'该操作无需填写表单，不建立草稿')
        # Already closed/advanced forms cannot reveal a stale action draft.
        if typ in {'review_task','review_workflow'}:
            if not policy.can_review(u,i):fail(403,'复核草稿权限已变化')
            if typ=='review_workflow' and (i['status']!='pending_review' or i.get('submitted_by')==actor(u)):fail(409,'最终复核状态或身份已变化')
        elif typ=='reopen_workflow':
            if i['status']!='closed':fail(409,'流程不再处于可重开状态')
        elif i['status']!='active':fail(409,'流程不在办理中，请核对当前状态')
        if context.get('task_id'):
            t=next((t for t in ws['tables']['tasks'] if t['task_id']==context['task_id'] and any(s['task_id']==t['task_id'] for s in i['steps'])),None)
            if not t:fail(404,'草稿关联任务不存在')
            if typ=='review_task' and (t['status']!='待复核' or t.get('completed_by')==actor(u)):fail(409,'任务复核状态或身份已变化')
    elif kind=='record':
        if set(context)!={'table','record_id'} or context['table'] not in {'residents','stays','assessments','plans'}:fail(422,'记录草稿上下文无效')
        if u['role'] not in REVIEWERS:fail(403,'该记录草稿需要主管权限')
        table=context['table'];allowed=set(policy.field_keys(u,table))-{'facility_id'}|{'_reason'}
        if table=='residents':
            from .resident_profile import validate_profile
            validate_profile(values)
            if values.get('unit') and not policy.in_unit(u,values['unit']):fail(403,'草稿照料区不在当前权限范围')
        elif values.get('resident_id') and values['resident_id'] not in policy.visible_residents(u,ws):fail(403,'草稿关联长者不可见')
        if context.get('record_id'):
            if not any(r[policy.IDS[table]]==context['record_id'] for r in policy.rows_for(u,ws,table)):fail(404,'原记录已不可见')
            if values.get(policy.IDS[table])!=context['record_id']:fail(422,'编辑草稿不能更换原记录编号')
    else:fail(422,'不支持此类草稿')
    if set(values)-allowed:fail(422,'草稿含有不允许保存的字段；密码、会话、导入文件不进入草稿')
    if any(not isinstance(v,str) or len(v)>100 for v in context.values()):fail(422,'草稿上下文仅允许短标识')

def owned(c,u,did):
    row=c.execute(select(drafts).where(drafts.c.id==did,drafts.c.user_id==u['id'],drafts.c.facility_id==u['facility_id'])).mappings().first()
    if not row or row['expires']<=int(time.time()):fail(404,'草稿不存在、已过期或不属于当前账户')
    return dict(row)

def permitted(u,ws,row):
    if row['scope_hash']!=scope_hash(u):fail(403,'账号权限已调整，原草稿不再展示；可从草稿清单删除不可恢复项')
    if row.get('result') is None:validate_content(u,ws,row['kind'],row['context'],row['values'])

def check_ref(c,u,ws,ref,kind,context):
    if not ref:return None
    row=owned(c,u,ref.id);permitted(u,ws,row)
    if row.get('result') is not None:fail(409,'此草稿已经用于提交；请核对原记录，不重复创建')
    if row['revision']!=ref.revision:fail(409,'草稿已被另一标签页修改，请重新打开并核对')
    if row['kind']!=kind:fail(422,'草稿与当前操作类型不符')
    if any(row['context'].get(k)!=v for k,v in context.items()):fail(422,'草稿与当前操作对象不符')
    return row

def consume(c,u,row,result):
    if not row:return
    c.execute(update(drafts).where(drafts.c.id==row['id']).values(values={},context={},result={k:v for k,v in result.items() if k in {'instance_id','id','table','revision','request_id'}},revision=row['revision']+1,updated_at=utcnow()))
    log(c,u,'draft_consumed',row['id'],summary={'kind':row['kind'],'content_retained':False})

def install(app,db,get_ws,touch):
    @app.get('/api/drafts')
    def list_drafts(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);ws=get_ws(c,u)['payload']
            c.execute(delete(drafts).where(drafts.c.user_id==u['id'],drafts.c.expires<=int(time.time())))
            out=[]
            for row in c.execute(select(drafts).where(drafts.c.user_id==u['id'],drafts.c.facility_id==u['facility_id']).order_by(drafts.c.updated_at.desc())).mappings():
                recoverable=True
                try:permitted(u,ws,row)
                except Exception as e:
                    from fastapi import HTTPException
                    if not isinstance(e,HTTPException):raise
                    recoverable=False
                # Never put body, resident names, notes or a user-entered title into a list.
                out.append({'id':row['id'],'kind':row['kind'],'revision':row['revision'],'updated_at':row['updated_at'],'expires':row['expires'],'recoverable':recoverable and row['result'] is None,'submitted':row['result'] is not None})
            touch(c,t);return {'items':out,'retention_days':DAYS,'limit':MAX_ACTIVE}

    @app.get('/api/drafts/{did}')
    def get_draft(did:str,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);w=get_ws(c,u);row=owned(c,u,did);permitted(u,w['payload'],row);touch(c,t)
            if row['result'] is not None:return {'id':did,'submitted':True,'note':'草稿已用于业务提交，正文已清除；请在授权业务页面核对。'}
            return {k:row[k] for k in ('id','kind','context','values','revision','workspace_revision','updated_at','expires')}|{'current_workspace_revision':w['revision'],'needs_recheck':True,'scope_revalidated':True}

    @app.put('/api/drafts/{did}')
    def put_draft(did:str,body:DraftBody,request:Request):
        import re
        if not re.fullmatch(r'[A-Za-z0-9-]{16,48}',did):fail(422,'草稿编号格式错误')
        with db.write() as c:
            u,t=authenticate(c,request,True);w=get_ws(c,u);validate_content(u,w['payload'],body.kind,body.context,body.values)
            old=c.execute(select(drafts).where(drafts.c.id==did)).mappings().first()
            if old:
                old=owned(c,u,did);permitted(u,w['payload'],old)
                if old.get('result') is not None:fail(409,'已提交草稿不可继续写入')
                if old['revision']!=body.expected_draft_revision:
                    if old['revision']==body.expected_draft_revision+1 and old['kind']==body.kind and old['context']==body.context and old['values']==body.values:
                        return {'id':did,'revision':old['revision'],'replayed':True,'saved':True}
                    fail(409,'草稿版本冲突，未覆盖较新的草稿')
            elif body.expected_draft_revision!=0:fail(409,'草稿已不存在，不自动重建旧版本')
            if body.expected_workspace_revision!=w['revision']:fail(409,'工作区已变化，请先刷新状态、核对后保存草稿')
            c.execute(delete(drafts).where(drafts.c.user_id==u['id'],drafts.c.expires<=int(time.time())))
            n=c.execute(select(func.count()).select_from(drafts).where(drafts.c.user_id==u['id'],drafts.c.result==None)).scalar_one()
            if not old and n>=MAX_ACTIVE:fail(409,'未提交草稿最多20份，请先删除不用的草稿')
            vals={'kind':body.kind,'context':body.context,'values':body.values,'revision':(old['revision'] if old else 0)+1,'workspace_revision':w['revision'],'scope_hash':scope_hash(u),'updated_at':utcnow(),'expires':int(time.time())+DAYS*86400,'result':None}
            if old:c.execute(update(drafts).where(drafts.c.id==did).values(**vals))
            else:c.execute(drafts.insert().values(id=did,user_id=u['id'],facility_id=u['facility_id'],**vals))
            log(c,u,'draft_saved',did,summary={'kind':body.kind,'revision':vals['revision']});touch(c,t)
            return {'id':did,'revision':vals['revision'],'saved':True,'expires':vals['expires'],'workspace_revision':w['revision']}

    @app.post('/api/drafts/{did}/delete')
    def delete_draft(did:str,body:DeleteBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);row=owned(c,u,did)
            if row['revision']!=body.revision:fail(409,'草稿已变更，先刷新再删除')
            c.execute(delete(drafts).where(drafts.c.id==did));log(c,u,'draft_deleted',did);touch(c,t)
            return {'ok':True}
