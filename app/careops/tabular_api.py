"""Permission-checked, previewed, append-only CSV/XLSX ingest and value reports.

Existing approvals are never created by this API. Historical text keeps an
unverified source label, and imported rows cannot attach to managed workflows.
"""
from __future__ import annotations
import hashlib
import io
import zipfile
import json
import re
import time
from collections import Counter
from copy import deepcopy
from datetime import date
from functools import lru_cache
from typing import Literal
from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, update, delete
from . import __version__, policy
from .bridge import engine
from .database import workspaces, previews, utcnow
from .security import authenticate, REVIEWERS, actor, ident, digest, canonical, fail, log
from .resident_profile import validate_profile
from .tabular_codec import read_file, MAX_ROWS, export_csv, export_xlsx

PLAN_FORMAT = 'careops-tabular-plan-v1'
ACTOR_FIELDS = {'owner','completed_by','reviewer','reporter','sender','receiver','verified_by','author','assessor','approver'}
SOURCE_TABLE = {'安全事件':'incidents','设施维修':'workorders','检查整改':'corrections','状态交接':'handovers','照护计划':'plans'}

class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class FileBody(Strict):
    filename:str=Field(min_length=1,max_length=200)
    file_base64:str=Field(min_length=1,max_length=2_666_672)
class PreviewBody(FileBody):
    mode:Literal['append','upsert']='append'
    sheet:str=Field(min_length=1,max_length=160)
    header_row:int=Field(ge=1,le=1048576,strict=True)
    table:str=Field(min_length=1,max_length=32)
    mapping:dict[str,StrictInt]=Field(max_length=80)
class CommitBody(Strict):
    preview_id:str=Field(min_length=1,max_length=60)
    expected_workspace_revision:int=Field(ge=0,strict=True)
    plan_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirm:bool
    skip_invalid:bool=False

@lru_cache(maxsize=1)
def schemas(): return {x['id']:x for x in engine('registry')}

def named_fields(table): return {f['key']:f for f in schemas()[table]['fields']}

def tidy(value): return re.sub(r'[\s_]+','',str(value)).casefold()

def suggestion(headers):
    candidates=[]
    for name,schema in schemas().items():
        mapping={}
        for f in schema['fields']:
            aliases={'display_name':{'显示称呼','姓名'},'id_number':{'身份证号码','身份证号'},'guardian_phone':{'监护人联系电话','监护人电话'}}
            names={tidy(f['key']),tidy(f['label'])}|{tidy(x) for x in aliases.get(f['key'],set())}
            found=[i for i,x in enumerate(headers) if tidy(x) in names]
            if len(found)==1:mapping[f['key']]=found[0]
        score=len(mapping)+(3 if schema['idKey'] in mapping else 0)
        candidates.append((score,name,mapping))
    candidates.sort(reverse=True,key=lambda x:x[0])
    _,table,mapping=candidates[0]
    return {'table':table,'mapping':mapping,'ambiguous':len(candidates)>1 and candidates[0][0]==candidates[1][0]}

def row_scope(u,ws,table,row):
    if row.get('facility_id')!=u['facility_id']: return '机构编号不符或为空；不会自动更改来源编号'
    if table=='residents':
        if not policy.resident_visible(u,row): return '该照料区不在当前账号授权范围'
    elif row.get('resident_id'):
        if row['resident_id'] not in policy.visible_residents(u,ws): return '关联长者不存在或不在当前账号授权范围'
    elif table=='followups':
        ev=next((r for r in policy.rows_for(u,ws,'incidents') if r['event_id']==row.get('event_id')),None)
        if not ev: return '关联事件不存在或不在当前账号授权范围'
    elif '*' not in u['units']:
        return '无长者关联的机构级历史台账仅允许全范围主管或管理员导入'
    return None

def managed_reference(ws,table,row):
    subjects={(f['subject_type'],f['subject_id']) for f in ws['flow']['instances']}
    findings={f.get('finding_id') for f in ws['flow']['instances'] if f.get('finding_id')}
    handovers={x['handover_id'] for x in ws['flow']['handover_links']}
    if table=='tasks':
        pair=(SOURCE_TABLE.get(row.get('source_type')),row.get('source_id'))
        if pair in subjects or pair==('handovers',row.get('source_id')) and row.get('source_id') in handovers:
            return '不能向已托管流程追加表格任务；请使用流程操作'
    if table=='followups' and ('incidents',row.get('event_id')) in subjects:
        return '不能用表格给已托管事件补造后续评价；请使用流程操作'
    if table=='corrections' and row.get('finding_id') in findings:
        return '不能从表格关联已托管检查发现'
    return None

def preparation_workspace(ws, table, mode, rows):
    if table != 'residents' or mode != 'upsert': return ws
    targets={(r['facility_id'],r['resident_id']) for r in rows}
    base=deepcopy(ws)
    base['tables']['residents']=[r for r in base['tables']['residents'] if (r['facility_id'],r['resident_id']) not in targets]
    return base


def install(app,db,get_ws,touch,require_role):
    @app.get('/api/tabular/residents/template')
    def resident_template(request:Request,format:Literal['xlsx']='xlsx'):
        with db.read() as c:
            u,t=authenticate(c,request);require_role(u,REVIEWERS)
            labels=named_fields('residents');keys=[k for k in policy.field_keys(u,'residents') if k!='facility_id']
            output=export_xlsx([labels[k]['label'] for k in keys],[],{'用途':'老人档案空模板，不含业务数据','机构编号':u['facility_id'],'匹配规则':'按机构编号+长者编号；不按姓名合并','导入模式':'居民选择upsert可更新同ID；未映射字段保留，已映射空值明确清空','编号格式':'身份证/电话/编号按文本填写，避免Excel精度丢失','资料边界':'护理等级与认知症仅人工登记，不自动诊断；Demo使用合成资料'})
            # 空列使用现有文本样式，工作人员直接填编号时保留前导零/长身份证。
            target=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(output)) as source, zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as destination:
                for item in source.infolist():
                    data=source.read(item.filename)
                    if item.filename=='xl/worksheets/sheet1.xml': data=data.replace(b'customWidth="1"/>',b'customWidth="1" style="3"/>')
                    destination.writestr(item.filename,data)
            return Response(target.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="CareOps_residents_template.xlsx"'})

    @app.post('/api/tabular/inspect')
    def inspect_file(body:FileBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,REVIEWERS)
            name,raw,book=read_file(body.filename,body.file_base64)
            result=[]
            for sh in book['sheets']:
                ranked=[(len(suggestion(r['values'])['mapping']),r) for r in sh['rows'][:20]]
                header=max(ranked,key=lambda x:x[0])[1]
                result.append({'name':sh['name'],'nonempty_rows':len(sh['rows']), 'suggested_header_row':header['line'],
                  'sample_rows':[{'line':r['line'],'values':[str(v)[:180] for v in r['values']]} for r in sh['rows'][:20]],
                  'suggestion':suggestion(header['values'])})
            touch(c,t)
            return {'filename':name,'sha256':hashlib.sha256(raw).hexdigest(),'sheets':result,'encoding':book['encoding'],'warnings':book['warnings'],
                    'limits':{'file_bytes':2_000_000,'data_rows_per_preview':MAX_ROWS},'note':'本步骤仅识别文件；未写入业务记录。请选择一张表及表头，明确映射。'}

    @app.post('/api/tabular/preview')
    def preview(body:PreviewBody,request:Request):
        if body.table not in schemas():fail(422,'未知目标台账')
        if body.mode=='upsert' and body.table!='residents':fail(422,'按ID更新模式仅开放老人主档')
        fields=named_fields(body.table);key=schemas()[body.table]['idKey']
        if not body.mapping or set(body.mapping)-set(fields):fail(422,'字段映射包含未开放字段')
        if any(type(v) is not int or not 0<=v<80 for v in body.mapping.values()) or len(set(body.mapping.values()))!=len(body.mapping):fail(422,'每个源列只能映射一个字段，列号须在0至79之间')
        missing=[f['label'] for f in fields.values() if f['required'] and f['key']!='facility_id' and f['key'] not in body.mapping and not (body.table=='residents' and body.mode=='upsert' and f['key']=='display_name')]
        if missing:fail(422,'必填字段未映射：'+'、'.join(missing))
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,REVIEWERS);w=get_ws(c,u);ws=w['payload']
            name,raw,book=read_file(body.filename,body.file_base64)
            sh=next((s for s in book['sheets'] if s['name']==body.sheet),None)
            if not sh:fail(422,'所选工作表不存在或未读取')
            hi=next((i for i,r in enumerate(sh['rows']) if r['line']==body.header_row),None)
            if hi is None:fail(422,'表头行不存在')
            if str(body.header_row) in sh['errors']:fail(422,'表头包含不支持的单元格')
            header=sh['rows'][hi]['values']
            if any(ix>=len(header) for ix in body.mapping.values()):fail(422,'映射超出所选表头列范围')
            source_rows=sh['rows'][hi+1:]
            if not source_rows or len(source_rows)>MAX_ROWS:fail(422,'每次预览须有1至1000条非空数据行')
            pid=ident();filehash=hashlib.sha256(raw).hexdigest()
            candidates=[];reports=[]
            counts=Counter(str(r['values'][body.mapping[key]]).strip() if len(r['values'])>body.mapping[key] else '' for r in source_rows)
            existing={str(r.get(key,'')) for r in ws['tables'][body.table]}
            originals={(r['facility_id'],r[key]):r for r in ws['tables'][body.table]}
            warnings=list(book['warnings'])
            ignored=[str(h) for i,h in enumerate(header) if i not in body.mapping.values()]
            if ignored:warnings.append('未映射列不写入业务字段（原文件仍留存于本次记录）：'+'、'.join(ignored)[:800])
            if 'facility_id' not in body.mapping:warnings.append('未映射机构列：本批使用当前机构 '+u['facility_id'])
            for sr in source_rows:
                row={k:(sr['values'][ix] if ix<len(sr['values']) else '') for k,ix in body.mapping.items()}
                if 'facility_id' not in row:row['facility_id']=u['facility_id']
                # Mirror text normalization without interpreting formulas or binding identities.
                row={k:str(v).strip() if v is not None else '' for k,v in row.items()}
                errs=list(sh['errors'].get(str(sr['line']),[]))
                if not row.get(key):errs.append('编号必填')
                elif row[key] in existing and not (body.table=='residents' and body.mode=='upsert'):errs.append('编号已存在或不可写入；本批仅新增，不覆盖')
                elif counts[row[key]]>1:errs.append('源文件内编号重复；所有同号行均暂停写入')
                old=originals.get((row.get('facility_id'),row.get(key))) if body.table=='residents' and body.mode=='upsert' else None
                if old:
                    old_scope=row_scope(u,ws,body.table,old)
                    if old_scope:errs.append(old_scope)
                    row={**{k:str(v).strip() if v is not None else '' for k,v in old.items() if k in fields},**row}
                if body.table=='residents':
                    try:validate_profile(row)
                    except ValueError as exc:errs.append(str(exc))
                    if not str(row.get('display_name','')).strip():errs.append('姓名为必填内容；新建必须填写，更新未映射则保留')
                err=row_scope(u,ws,body.table,row)
                if err:errs.append(err)
                err=managed_reference(ws,body.table,row)
                if err:errs.append(err)
                for k,v in row.items():
                    if len(v)>12000:errs.append(fields[k]['label']+'超过长度限制')
                    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',v):errs.append(fields[k]['label']+'含不支持的控制字符')
                    if k in ACTOR_FIELDS and 'account:' in v:errs.append('表格人员字段不得伪装认证账户；请保留历史姓名，接续时明确分派')
                    if fields[k]['type']=='date' and re.fullmatch(r'\d{4}-\d{2}-\d{2} 00:00:00',v):row[k]=v[:10]
                row['_meta']={'file':name,'sheet':body.sheet,'row':sr['line'],'import_id':pid,'source_sha256':filehash,'identity':'historical-unverified'}
                report={'line':sr['line'],'record_id':row.get(key,''),'status':'REJECTED' if errs else 'PENDING','errors':errs,'warnings':[], 'operation':'update' if old else 'create', 'values':{k:v for k,v in row.items() if k!='_meta'}}
                reports.append(report)
                if not errs:candidates.append({'line':sr['line'],'row':row})
            prepared=engine('prepare_tabular',workspace=preparation_workspace(ws,body.table,body.mode,[x['row'] for x in candidates]),table=body.table,rows=candidates)
            accepted={x['line']:x['row'] for x in prepared['accepted']}
            rejected={x['line']:x['errors'] for x in prepared['rejected']}
            for r in reports:
                if r['line'] in accepted:r['status']='ACCEPTED'
                elif r['line'] in rejected:r['status']='REJECTED';r['errors'].extend(rejected[r['line']])
                r['warnings']=[x['message'] for x in prepared['warnings'] if x['line']==r['line']]
            plan={'format':PLAN_FORMAT,'table':body.table,'mode':body.mode,'base_revision':w['revision'],'permission_scope':{'role':u['role'],'units':sorted(u['units'])},'rows':list(accepted.values()),'filename':name,'sheet':body.sheet,'header_row':body.header_row,'mapping':body.mapping,'source_sha256':filehash}
            planhash=digest(plan)
            report={'kind':PLAN_FORMAT,'table':body.table,'mode':body.mode,'table_name':schemas()[body.table]['name'],'source_sha256':filehash,'plan_sha256':planhash,'filename':name,'sheet':body.sheet,
                    'total':len(reports),'accepted':len(accepted),'rejected':len(reports)-len(accepted),'warnings':warnings,'rows':reports,'identity':'historical-unverified',
                    'note':('居民显式按机构+ID更新/新增；未映射字段保留。' if body.mode=='upsert' else '仅新增。')+'拒绝行保存在导入核对记录，不进入业务台账。旧人员和审批是来源文字，不是当前账户认证的完成记录。'}
            now=int(time.time())
            c.execute(delete(previews).where(previews.c.committed==False,previews.c.expires<now))
            n=len(c.execute(select(previews.c.id).where(previews.c.user_id==u['id'],previews.c.committed==False)).all())
            if n>=10:fail(429,'未提交预览已达10份；请删除不再使用的预览或等待30分钟过期')
            c.execute(previews.insert().values(id=pid,facility_id=u['facility_id'],user_id=u['id'],digest=planhash,raw_text=canonical(body.model_dump()),normalized=plan,report=report,expires=now+1800,committed=False))
            log(c,u,'tabular_preview',pid,summary={'table':body.table,'source_sha256':filehash,'accepted':len(accepted),'rejected':report['rejected']});touch(c,t)
            return {'preview_id':pid,'plan_sha256':planhash,'revision':w['revision'],'report':report}

    def find_preview(c,u,pid):
        p=c.execute(select(previews).where(previews.c.id==pid,previews.c.user_id==u['id'],previews.c.facility_id==u['facility_id'])).mappings().first()
        if not p or p['normalized'].get('format')!=PLAN_FORMAT:fail(404,'该表格预览不存在或不可见')
        return p

    def assert_saved_scope(u,p):
        if p['normalized'].get('permission_scope')!={'role':u['role'],'units':sorted(u['units'])}:
            fail(403,'当前授权与预览时不同，请重新预览；原始证据不删除')

    @app.get('/api/tabular/history')
    def import_history(request:Request):
        with db.read() as c:
            u,t=authenticate(c,request);require_role(u,REVIEWERS)
            scope={'role':u['role'],'units':sorted(u['units'])}
            rows=c.execute(select(previews).where(previews.c.user_id==u['id'],previews.c.facility_id==u['facility_id']).order_by(previews.c.expires.desc()).limit(50)).mappings()
            items=[]
            for p in rows:
                if p['normalized'].get('format')!=PLAN_FORMAT or p['normalized'].get('permission_scope')!=scope:continue
                r=p['report'];items.append({'preview_id':p['id'],'filename':r['filename'],'table_name':r['table_name'],'accepted':r['accepted'],'rejected':r['rejected'],
                   'committed':p['committed'],'expired':p['expires']<int(time.time()),'committed_at':r.get('committed_at','')})
                if len(items)>=20:break
            return {'items':items,'limit':20,'scope':'仅本人、同机构、与当前授权一致的近期表格预览；不是全机构导入清单'}

    @app.get('/api/tabular/previews/{pid}')
    def preview_report(pid:str,request:Request):
        with db.read() as c:
            u,t=authenticate(c,request);require_role(u,REVIEWERS);p=find_preview(c,u,pid);assert_saved_scope(u,p);w=get_ws(c,u)
            # Rights can change after preview creation; never return a broader saved projection.
            for row in p['normalized']['rows']:
                if row_scope(u,w['payload'],p['normalized']['table'],row):fail(403,'当前授权已变化；不能读取该预览')
            return {'preview_id':pid,'committed':p['committed'],'expired':p['expires']<int(time.time()),'revision':p['normalized']['base_revision'],'plan_sha256':p['digest'],'report':p['report']}

    @app.delete('/api/tabular/previews/{pid}')
    def discard_preview(pid:str,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,REVIEWERS);p=find_preview(c,u,pid)
            if p['committed']:fail(409,'已提交的来源证据不能通过预览删除入口移除')
            c.execute(delete(previews).where(previews.c.id==pid));touch(c,t)
            return {'ok':True}

    @app.post('/api/tabular/commit')
    def commit(body:CommitBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,REVIEWERS);w=get_ws(c,u);p=find_preview(c,u,body.preview_id);assert_saved_scope(u,p);plan=p['normalized'];report=p['report']
            if not body.confirm or p['digest']!=body.plan_sha256:fail(422,'须确认原预览摘要后提交')
            for row in plan['rows']:
                if row_scope(u,w['payload'],plan['table'],row):fail(403,'当前授权已变化，请重新预览')
            if p['committed']:
                touch(c,t);return {**report['commit_result'],'replayed':True}
            if p['expires']<int(time.time()):fail(409,'预览已过期，请重新预览')
            if not plan['rows']:fail(422,'没有可写入的数据行')
            if report['rejected'] and not body.skip_invalid:fail(422,'存在拒绝行；请明确确认仅导入通过校验的行，拒绝行将保留于核对记录')
            if w['revision']!=body.expected_workspace_revision or w['revision']!=plan['base_revision']:fail(409,'工作区已变化，请重新预览，不覆盖新数据')
            # Revalidate stored plan and references inside the write transaction.
            check=engine('prepare_tabular',workspace=preparation_workspace(w['payload'],plan['table'],plan.get('mode','append'),plan['rows']),table=plan['table'],rows=[{'line':r['_meta']['row'],'row':r} for r in plan['rows']])
            if check['rejected'] or len(check['accepted'])!=len(plan['rows']):fail(409,'写入前校验与预览不一致，请重新预览')
            new=deepcopy(w['payload'])
            updated=0
            if plan['table']=='residents' and plan.get('mode')=='upsert':
                targets={(r['facility_id'],r['resident_id']):r for r in plan['rows']}
                kept=[]
                for old in new['tables']['residents']:
                    pair=(old['facility_id'],old['resident_id'])
                    if pair in targets:
                        kept.append(targets.pop(pair));updated+=1
                    else:kept.append(old)
                new['tables']['residents']=kept+list(targets.values())
            else:new['tables'][plan['table']].extend(plan['rows'])
            new['sources'].append({'file':plan['filename'],'sheet':plan['sheet'],'import_id':body.preview_id,'sha256':plan['source_sha256'],'accepted':report['accepted'],'rejected':report['rejected'],'identity':'historical-unverified'})
            new['audit'].append({'time':utcnow(),'actor':actor(u),'actor_type':'authenticated-session','authenticated_user_id':u['id'],'action':'tabular_append','table':plan['table'],'id':body.preview_id,'facility':u['facility_id'],'reason':'显式确认追加已校验来源行；不认证历史审批','before':{'count':len(w['payload']['tables'][plan['table']]),'records':[r for r in w['payload']['tables'][plan['table']] if plan['table']=='residents' and plan.get('mode')=='upsert' and any(x['resident_id']==r['resident_id'] and x['facility_id']==r['facility_id'] for x in plan['rows'])]},'after':{'count':len(new['tables'][plan['table']]),'source_sha256':plan['source_sha256']}})
            engine('validate',workspace=new)
            rev=w['revision']+1
            result={'ok':True,'revision':rev,'preview_id':body.preview_id,'imported':report['accepted'],'updated':updated,'created':report['accepted']-updated,'rejected':report['rejected'],'replayed':False}
            report={**report,'commit_result':result,'committed_at':utcnow()}
            c.execute(update(workspaces).where(workspaces.c.facility_id==u['facility_id']).values(payload=new,revision=rev,updated_at=utcnow()))
            c.execute(update(previews).where(previews.c.id==body.preview_id).values(committed=True,report=report))
            log(c,u,'tabular_committed',body.preview_id,summary={'table':plan['table'],'revision':rev,'source_sha256':plan['source_sha256'],'imported':report['accepted'],'rejected':report['rejected']});touch(c,t)
            return result

    @app.get('/api/reports/{table}')
    def report_export(table:str,request:Request,format:Literal['csv','xlsx']='xlsx',status:str='',owner:str='',due_from:str='',due_to:str=''):
        if table not in schemas():fail(404,'未知台账')
        for v in (due_from,due_to):
            if v:
                try:date.fromisoformat(v)
                except ValueError:fail(422,'期限筛选须为YYYY-MM-DD')
        if due_from and due_to and due_from>due_to:fail(422,'起始日期不能晚于结束日期')
        if len(status)>80 or len(owner)>100:fail(422,'筛选值过长')
        with db.write() as c:
            u,t=authenticate(c,request);w=get_ws(c,u);rows=policy.rows_for(u,w['payload'],table)
            fields=policy.field_keys(u,table)
            if status and 'status' not in fields or owner and 'owner' not in fields:fail(403,'不能按未授权字段筛选')
            due='due_at' if 'due_at' in fields else 'followup_due' if 'followup_due' in fields else 'review_due' if 'review_due' in fields else None
            if (due_from or due_to) and not due:fail(422,'此台账没有可用于期限筛选的字段')
            rows=[r for r in rows if (not status or r.get('status')==status) and (not owner or r.get('owner')==owner) and (not due_from or r.get(due,'')[:10]>=due_from) and (not due_to or bool(r.get(due)) and r[due][:10]<=due_to)]
            labels=named_fields(table);headers=[labels[k]['label'] for k in fields]
            source={r.get(policy.IDS[table]):r.get('_meta',{}) for r in w['payload']['tables'][table]}
            def origin(r):
                m=source.get(r.get(policy.IDS[table]),{})
                return '表格来源；历史身份未认证' if m.get('identity')=='historical-unverified' else '现有记录；需结合来源与复核核对'
            grid=[[r.get(k,'') for k in fields]+[origin(r)] for r in rows];headers+=['来源提示']
            at=utcnow();filters=canonical({'status':status,'owner':owner,'due_from':due_from,'due_to':due_to})
            meta={'产品':f'CareOps {__version__}','导出时间UTC':at,'机构编号':u['facility_id'],'账号角色':u['role'],'授权照料区':'、'.join(u['units']) or '公共维修范围','台账':schemas()[table]['name'],'筛选条件':filters,'工作区修订':str(w['revision']),'可见数据行数':str(len(rows)),
                  '范围说明':'只导出当前授权范围内的字段和记录；不是机构全部记录或完整系统备份。',
                  '身份说明':'表格历史人员不自动视为当前认证账户；不能凭台账状态宣称真实照护完成率。',
                  '格式说明':'XLSX为静态值表，日期和编号按文本保留；CSV危险首字符加单引号。无公式执行。'}
            if format=='csv':
                extra=[at,u['facility_id'],filters,str(w['revision'])];output=export_csv(headers+['导出时间UTC','机构范围','筛选条件','工作区修订'],[r+extra for r in grid]);mime='text/csv; charset=utf-8'
            else:output=export_xlsx(headers,grid,meta);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            log(c,u,'table_export',table,summary={'rows':len(rows),'format':format,'revision':w['revision'],'filters':{'status':status,'due_from':due_from,'due_to':due_to,'owner_filter_applied':bool(owner)}});touch(c,t)
            return Response(output,media_type=mime,headers={'Content-Disposition':f'attachment; filename="CareOps_{table}_{at[:10]}.{format}"','X-CareOps-Row-Count':str(len(rows)),'X-CareOps-Revision':str(w['revision']),'X-CareOps-Exported-At':at})
