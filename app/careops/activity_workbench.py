"""活动工作台：共享长者主档、独立签到事实及鉴权附件。"""
from copy import deepcopy
from datetime import datetime, date
from pathlib import Path
from uuid import uuid4
import base64
import hashlib
import re
import os

COMMANDS = {'plan_create', 'plan_copy', 'publish', 'cancel', 'register', 'checkin', 'complete', 'preferences'}
MAX_FILE = 3 * 1024 * 1024


def root_for(db):
    if db.engine.url.get_backend_name() != 'sqlite' or db.engine.url.database == ':memory:':
        raise ValueError('活动附件需要独立持久化数据库目录')
    return Path(db.engine.url.database).resolve().parent / 'attachments' / 'activities'


def actual_time(values, at):
    value, source = values.get('performed_at', ''), values.get('performed_source', '')
    if not isinstance(value,str) or not isinstance(source,str): raise ValueError('实际时间与来源须为文本')
    if bool(value) != bool(source): raise ValueError('实际时间与来源须同时填写；可均留空')
    if source and source not in {'本人记录', '原始记录', '交班记录'}: raise ValueError('实际时间来源不正确')
    if value:
        if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}',value): raise ValueError('实际时间须包含明确日期与时点')
        moment = datetime.fromisoformat(value); submitted = datetime.fromisoformat(at)
        if moment.tzinfo is None: moment = moment.replace(tzinfo=submitted.tzinfo)
        if moment > submitted: raise ValueError('实际时间不得晚于提交时间')
    return value, source


def apply_activity(new, state, body, actor, at, facility_id):
    from .departments import validate, make_row, add_history
    command = body['action']; values = deepcopy(body.get('values', {}))
    if values.get('performed_at') or values.get('performed_source'): actual_time(values,at)
    if body.get('draft_context') is not None: raise ValueError('活动操作不接受草稿办理意图；请使用draft_save')
    rows = state['rows']['activities']; row = next((r for r in rows if r['id'] == body.get('id')), None)
    def require(states):
        if not row or row['status'] not in states: raise ValueError('活动不存在或当前状态不支持此操作')
        if row['status'] == 'draft' and row.get('plan_actor') not in {None, actor}: raise ValueError('策划草稿属于其他使用者')
    def allowed(keys):
        if set(values) - set(keys): raise ValueError('活动操作存在未识别字段')
    if command == 'preferences':
        allowed({'resident_id','interest','support'})
        resident = next((r for r in new.get('tables',{}).get('residents',[]) if r.get('resident_id') == values.get('resident_id') and r.get('facility_id') == facility_id), None)
        if not resident: raise ValueError('长者主档不存在')
        for key in ('interest','support'):
            if key in values and (not isinstance(values[key], str) or len(values[key]) > 2000): raise ValueError('兴趣与支持格式错误')
        before = {k: resident.get(k,'') for k in ('what_matters','support')}
        if 'interest' in values: resident['what_matters'] = values['interest']
        if 'support' in values: resident['support'] = values['support']
        state.setdefault('activity_preference_history', []).append({'resident_id':resident['resident_id'],'actor':actor,'at':at,'before':before,'after':{k:resident.get(k,'') for k in ('what_matters','support')}})
        return {'record': None, 'related': {'id':resident['resident_id'],'kind':'residents'}}
    if command in {'plan_create','publish'} and {'attendance','summary','performed_at','performed_source','exception_type','exception_detail'}.intersection(values): raise ValueError('策划不可写入签到或执行事实')
    if command == 'plan_create':
        validate(values,'activities',new,state,partial=True)
        row = make_row(state,'activities',values,actor,at)
        row.update(status='draft', plan_actor=actor,activity_version=1)
        row['history'][-1]['after'] = {k:deepcopy(v) for k,v in row.items() if k!='history'}
        row['history'][-1]['action'] = 'plan_create'
    elif command == 'plan_copy':
        require({'draft','planned','running','completed','cancelled'}); allowed(set())
        from .departments import SCHEMAS
        copy = {f['key']: deepcopy(row[f['key']]) for f in SCHEMAS['activities']['fields'] if f['key'] in row and f['key'] not in {'attendance','summary','performed_at','performed_source','exception_type','exception_detail'}}
        copy['title'] = copy.get('title','') + '（副本）'
        parent = row['id']; row = make_row(state,'activities',copy,actor,at)
        row.update(status='draft',plan_actor=actor,copied_from=parent,activity_version=1)
        row['history'][-1]['after'] = {k:deepcopy(v) for k,v in row.items() if k!='history'}
        row['history'][-1]['action'] = 'plan_copy'
    else:
        require({'draft','planned','running','completed'}); before = {k:deepcopy(v) for k,v in row.items() if k != 'history'}
        if command == 'publish':
            require({'draft'}); validate(values,'activities',new,state,partial=True)
            merged = {**row,**values}
            from .departments import SCHEMAS
            validate({f['key']:merged[f['key']] for f in SCHEMAS['activities']['fields'] if f['key'] in merged},'activities',new,state)
            if not isinstance(merged.get('target_group'),str) or not merged['target_group'].strip(): raise ValueError('请填写活动对象')
            row.update(values); row.update(status='planned',published_at=at)
        elif command == 'cancel':
            require({'planned','running'}); allowed({'cancel_reason'})
            reason=values.get('cancel_reason')
            if not isinstance(reason,str) or not reason.strip() or len(reason)>2000: raise ValueError('请填写取消原因')
            row.update(status='cancelled',cancel_reason=reason,cancelled_at=at,cancelled_by=actor)
        elif command in {'register','checkin'}:
            require({'planned','running'} if command == 'register' else {'running'})
            key = 'participants' if command == 'register' else 'attendance'; allowed({key})
            ids=values.get(key)
            from .departments import residents
            known={r['id'] for r in residents(new)}
            if not isinstance(ids,list) or len(ids)>500 or any(not isinstance(x,str) or x not in known for x in ids) or len(set(ids))!=len(ids): raise ValueError('名单须引用现有长者且不可重复')
            row[key]=ids
            if command == 'checkin':
                row.update(attendance_confirmed=True,checked_in_at=at,checked_in_by=actor)
                row['checkins']=[{'resident_id':rid,'checked_in_at':at,'checked_in_by':actor} for rid in ids]
        elif command == 'complete':
            require({'running'}); allowed({'summary','performed_at','performed_source','exception_type','exception_detail'})
            summary=values.get('summary')
            if not isinstance(summary,str) or not summary.strip() or len(summary)>8000: raise ValueError('请填写简短活动日志')
            if not row.get('attendance_confirmed'): raise ValueError('请先确认实际签到名单（可确认无人参加）')
            exception=values.get('exception_type','')
            if exception not in {'','临时中止','老人身体不适','场地问题','其他情况'}: raise ValueError('异常类型不正确')
            detail=values.get('exception_detail','')
            if not isinstance(detail,str) or len(detail)>2000 or exception and not detail.strip(): raise ValueError('异常情况须填写简短说明')
            performed,source=actual_time(values,at)
            row.update(values); row.update(status='completed',completed_at=at,performed_at=performed,performed_source=source,actual_end=performed)
        else: raise ValueError('未知活动操作')
        add_history(row,actor,at,command,before)
        row['history'][-1].update(performed_at=values.get('performed_at','') if command=='complete' else '',performed_source=values.get('performed_source','') if command=='complete' else '')
    return {'record':row,'related':None}


def attachment_public(item):
    return {k:v for k,v in item.items() if k != 'storage_name'} | {'url':f"/api/activities/{item['activity_id']}/attachments/{item['attachment_id']}"}


def view(payload, actor, date_from='', date_to=''):
    from .departments import seed, STAFF
    for value in (date_from,date_to):
        if value: date.fromisoformat(value)
    if date_from and date_to and date_from > date_to: raise ValueError('日期范围顺序错误')
    state=seed(payload)
    rows=[deepcopy(r) for r in state['rows']['activities'] if r['status']!='draft' or r.get('plan_actor') in {None,actor}]
    for row in rows:
        row['attachments']=[attachment_public(a) for a in row.get('attachments',[])]
    selected=[r for r in rows if r['status']!='draft' and (not date_from or r.get('scheduled_at','')[:10]>=date_from) and (not date_to or r.get('scheduled_at','')[:10]<=date_to)]
    counted=[r for r in selected if r['status']!='cancelled' and r.get('attendance_confirmed')]
    counts={}; types={}
    for row in selected:
        if row['status']=='cancelled': continue
        typ=row.get('activity_type') or '未分类'; types[typ]=types.get(typ,0)+1
    for row in counted:
        for rid in row.get('attendance',[]): counts[rid]=counts.get(rid,0)+1
    directory=[]
    for r in payload.get('tables',{}).get('residents',[]):
        rid=r['resident_id']
        from .departments import is_sales_candidate
        if is_sales_candidate(payload,r): continue
        history=[x for x in counted if rid in x.get('attendance',[])]
        directory.append({'id':rid,'label':r.get('display_name',rid),'bed':r.get('bed',''),'unit':r.get('unit',''),'service_category':r.get('service_category') if r.get('service_category') in {'活力自主','护理'} else '未确认','interest':r.get('what_matters',''),'support':r.get('support',''),'participation_count':counts.get(rid,0),'last_activity_at':max((x.get('scheduled_at','') for x in history),default=''),'activity_ids':[x['id'] for x in history]})
    return {'activities':rows,'residents':directory,'drafts':[d for d in state['drafts'] if d['actor']==actor and d['kind']=='activities'],'staff':STAFF,'statistics':{'activity_count':len([r for r in selected if r['status']!='cancelled']),'completed_count':sum(r['status']=='completed' for r in selected),'planned_count':sum(r['status'] in {'planned','running'} for r in selected),'attendance_count':sum(counts.values()),'unique_residents':len(counts),'resident_counts':counts,'type_distribution':types},'date_from':date_from,'date_to':date_to,'statistics_basis':'按计划日期范围；实际确认签到；草稿与取消排除'}


def install(app,db,get_ws,touch):
    from fastapi import Request
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, ConfigDict, Field
    from sqlalchemy import select,update
    from zoneinfo import ZoneInfo
    from .database import workspaces,receipts,facilities,utcnow
    from .security import authenticate,fail,actor,digest,log
    from .departments import seed,add_history
    def check(user):
        if user['role']!='admin' or '*' not in user['units']: fail(403,'活动演示仅限现有全院管理员')
    @app.get('/api/activities')
    def get_activities(request:Request,date_from:str='',date_to:str=''):
        with db.read() as c:
            u,t=authenticate(c,request);check(u);w=get_ws(c,u)
            return {'revision':w['revision'],**view(w['payload'],actor(u),date_from,date_to)}
    class Upload(BaseModel):
        model_config=ConfigDict(extra='forbid')
        request_id:str=Field(pattern=r'^[A-Za-z0-9_.-]{12,70}$')
        expected_workspace_revision:int=Field(ge=0,strict=True)
        filename:str=Field(min_length=1,max_length=180)
        content_base64:str=Field(max_length=4*MAX_FILE+16)
    @app.post('/api/activities/{activity_id}/attachments')
    def upload(activity_id:str,body:Upload,request:Request):
        written=None;temporary=None
        try:
            with db.write() as c:
                u,t=authenticate(c,request,True);check(u);w=get_ws(c,u);data=body.model_dump();hashed=digest({'activity_upload':activity_id,'body':data})
                receipt=c.execute(select(receipts).where(receipts.c.facility_id==u['facility_id'],receipts.c.request_id==body.request_id)).mappings().first()
                if receipt:
                    if receipt['result'].get('cancelled') or receipt['user_id']!=u['id'] or receipt['digest']!=hashed: fail(409,'请求编号已被其他内容使用或终止')
                    touch(c,t);return {**receipt['result'],'replayed':True}
                if w['revision']!=body.expected_workspace_revision: fail(409,'工作区已更新，请刷新后再上传')
                state=seed(w['payload']);row=next((r for r in state['rows']['activities'] if r['id']==activity_id),None)
                if not row or row['status'] not in {'planned','running','completed'}: fail(422,'此活动状态不接受照片')
                if len(row.get('attachments',[]))>=30: fail(422,'单场活动最多30张照片')
                if '/' in body.filename or '\\' in body.filename or any(ord(x)<32 for x in body.filename): fail(422,'文件名包含不安全字符')
                try: raw=base64.b64decode(body.content_base64,validate=True)
                except (ValueError,TypeError): fail(422,'照片编码错误')
                if not raw or len(raw)>MAX_FILE: fail(422,'照片须为1字节至3MB')
                if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)>=33 and raw[12:16]==b'IHDR' and b'IEND' in raw[-16:]: ext,mime='png','image/png'
                elif raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'): ext,mime='jpg','image/jpeg'
                else: fail(422,'只接受字节格式正确的JPG或PNG照片')
                if Path(body.filename).suffix.lower() not in ({'.png'} if ext=='png' else {'.jpg','.jpeg'}): fail(422,'文件扩展名与照片格式不一致')
                if not re.fullmatch(r'activities-[a-f0-9]{12}',activity_id): fail(422,'活动标识格式不正确')
                aid=uuid4().hex;root=root_for(db);directory=root/activity_id
                if root.is_symlink() or root.parent.is_symlink() or directory.is_symlink(): fail(422,'附件存储目录不可为符号链接')
                directory.mkdir(parents=True,exist_ok=True)
                if root.resolve() not in directory.resolve().parents: fail(422,'附件目录超出存储范围')
                target=directory/(aid+'.'+ext);temporary=directory/(aid+'.tmp')
                with temporary.open('xb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(temporary,target);written=target
                facility=c.execute(select(facilities).where(facilities.c.id==u['facility_id'])).mappings().one();at=datetime.now(ZoneInfo(facility['timezone'])).isoformat(timespec='seconds')
                item={'attachment_id':aid,'activity_id':activity_id,'filename':body.filename,'mime_type':mime,'size':len(raw),'created_at':at,'created_by':actor(u),'storage_name':target.name,'sha256':hashlib.sha256(raw).hexdigest()}
                before={k:deepcopy(v) for k,v in row.items() if k!='history'};row.setdefault('attachments',[]).append(item);add_history(row,actor(u),at,'attach_photo',before)
                new=deepcopy(w['payload']);new['department_demo']=state;revision=w['revision']+1
                c.execute(update(workspaces).where(workspaces.c.facility_id==u['facility_id']).values(payload=new,revision=revision,updated_at=utcnow()))
                result={'ok':True,'revision':revision,'request_id':body.request_id,'attachment':attachment_public(item)}
                c.execute(receipts.insert().values(facility_id=u['facility_id'],request_id=body.request_id,user_id=u['id'],digest=hashed,result=result));log(c,u,'activity:attach_photo',activity_id,body.request_id,summary={'attachment_id':aid,'size':len(raw),'sha256':item['sha256']});touch(c,t)
            return result
        except BaseException:
            if written and written.exists(): written.unlink()
            if temporary and temporary.exists(): temporary.unlink()
            raise
    @app.get('/api/activities/{activity_id}/attachments/{attachment_id}')
    def download(activity_id:str,attachment_id:str,request:Request):
        with db.read() as c:
            u,t=authenticate(c,request);check(u);w=get_ws(c,u);state=seed(w['payload'])
            row=next((r for r in state['rows']['activities'] if r['id']==activity_id),None)
            item=next((a for a in (row or {}).get('attachments',[]) if a['attachment_id']==attachment_id),None)
            if not item: fail(404,'照片不存在或不在当前机构')
            name=item.get('storage_name','')
            if not re.fullmatch(r'[a-f0-9]{32}\.(jpg|png)',name) or not re.fullmatch(r'activities-[a-f0-9]{12}',activity_id): fail(404,'照片存储标识无效')
            raw_root=root_for(db);raw_path=raw_root/activity_id/name
            if raw_root.is_symlink() or raw_root.parent.is_symlink() or raw_path.parent.is_symlink() or raw_path.is_symlink(): fail(404,'照片存储路径不可为符号链接')
            root=raw_root.resolve();path=raw_path.resolve()
            if root not in path.parents or not path.is_file(): fail(404,'照片文件缺失，请核对附件备份')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item.get('sha256'): fail(409,'照片文件校验失败')
            return FileResponse(path,media_type=item['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
