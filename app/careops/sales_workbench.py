"""销售轻量事务：共享主档/房床，咨询事件与家属关系分离。"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4
import re

COLLECTIONS=('contacts','relations','inquiries','visits','followups','preferences','reservations','charges','payments','preparations','bed_history')
STAGES={'new','contacted','visit_scheduled','visited','intent','reserved','preparing','admitted','paused','lost'}
COMMANDS={'inquiry_create','contact_create','relation_create','followup_create','visit_create','stage_change','intent_update','preference_create','bed_create','bed_update','reserve','reservation_release','preparation_create','preparation_update','move_in','charge_create','payment_report','payment_confirm','apply_advance'}
PREP_ITEMS={'basic','bed','care','room','fees'}

def sales_state(state):
    value=state.setdefault('sales',{'version':1})
    if not isinstance(value,dict) or value.get('version')!=1: raise ValueError('销售扩展版本不兼容')
    for key in COLLECTIONS:
        rows=value.setdefault(key,[])
        if not isinstance(rows,list) or len(rows)>5000 or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in rows): raise ValueError('销售集合结构错误')
    required={
        'contacts':('name',), 'relations':('resident_id','contact_id','relationship'),
        'inquiries':('resident_id','contact_id','prospect_id','occurred_at','result'),
        'visits':('contact_id','occurred_at','result'), 'followups':('prospect_id','contact_id','occurred_at','result'),
        'preferences':('resident_id','source','category','content'),
        'reservations':('resident_id','prospect_id','bed_id','starts_at','expires_at','status'),
        'charges':('resident_id','period'), 'payments':('resident_id','category','status'),
        'preparations':('prospect_id','resident_id','bed_id','planned_move_in_at','status'),
        'bed_history':('bed_id','operation')}
    for key in COLLECTIONS:
        for row in value[key]:
            if any(not isinstance(row.get(k),str) for k in required[key]): raise ValueError('销售业务字段结构错误')
            if key in {'payments','charges'} and (isinstance(row.get('amount_cents'),bool) or not isinstance(row.get('amount_cents'),int) or row['amount_cents']<0): raise ValueError('费用分币结构错误')
            if key=='visits' and (not isinstance(row.get('resident_ids'),list) or any(not isinstance(r,str) for r in row['resident_ids'])): raise ValueError('到访关联结构错误')
            if key=='preparations' and (not isinstance(row.get('items'),dict) or set(row['items'])!=PREP_ITEMS or any(not isinstance(x,dict) or not isinstance(x.get('status'),str) or x.get('status') not in {'未开始','处理中','已完成','不适用'} for x in row['items'].values())): raise ValueError('入住准备结构错误')
    for row in state['rows']['leads']:
        if row.get('sales_version')==1 and (not isinstance(row.get('resident_id'),str) or not isinstance(row.get('stage'),str) or row['stage'] not in STAGES): raise ValueError('销售客户阶段结构错误')
    return value

def text(v,label='内容',required=False,limit=2000):
    if not isinstance(v,str) or len(v)>limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',v) or required and not v.strip(): raise ValueError(label+'格式或长度错误')
    return v.strip()

def moment(v,at,actual=False):
    text(v,'时间',True,50)
    try: value=datetime.fromisoformat(v)
    except ValueError: raise ValueError('时间须为ISO日期时间')
    submitted=datetime.fromisoformat(at)
    if value.tzinfo is None: value=value.replace(tzinfo=submitted.tzinfo)
    if 'T' not in v and ' ' not in v: raise ValueError('请填写日期与时间，不以仅日期替代实际发生时间')
    if actual and value>submitted: raise ValueError('实际发生时间不得晚于提交时间')
    return value

def period(v):
    if not isinstance(v,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',v): raise ValueError('费用期间须为YYYY-MM')
    return v

def money(v):
    if isinstance(v,bool) or not isinstance(v,(str,int,float)): raise ValueError('金额须为最多两位小数的非负数')
    try: d=Decimal(str(v))
    except InvalidOperation: raise ValueError('金额格式错误')
    if not d.is_finite() or d<0 or d>Decimal('100000000') or d*100 != (d*100).to_integral_value(): raise ValueError('金额须为最多两位小数的非负数')
    return int(d*100)

def amount(cents): return f'{Decimal(cents)/100:.2f}'

def maintenance_blocks(payload,bed):
    locations={str(bed['id']),str(bed.get('room',''))}-{''}
    found=[]
    orders={r.get('workorder_id'):r for r in payload.get('tables',{}).get('workorders',[])}
    for flow in payload.get('flow',{}).get('instances',[]):
        if flow.get('kind')!='maintenance' or flow.get('status') not in {'active','pending_review'}: continue
        oid=flow.get('subject_id') or (flow.get('subject') or {}).get('id')
        subject=orders.get(oid,{})
        location=subject.get('location') or flow.get('location') or (flow.get('subject') or {}).get('location')
        if location in locations: found.append({'id':flow['id'],'status':flow['status'],'source':'既有未关闭维修流程'})
    return found

def effective_stays(payload,facility_id,when):
    """共享入住日期开始含、结束不含，不把未来或离院记录当当前入住。"""
    return [x for x in payload.get('tables',{}).get('stays',[])
            if x.get('facility_id')==facility_id and x.get('status') in {'在院','外出','外院'}
            and x.get('start_date','9999')<=when and (not x.get('end_date') or when<x['end_date'])]


def effective_beds(payload,state):
    result=deepcopy(state['beds'])
    for b in result:
        blocks=maintenance_blocks(payload,b)
        b['maintenance_block']=blocks
        b['effective_status']='unavailable' if blocks and b['status']!='occupied' else b['status']
        for key in ('building','floor','room','room_type'): b.setdefault(key,'')
    return result

def fee_summary(s,rid,month):
    charges=[x for x in s['charges'] if x['resident_id']==rid and x['period']==month]
    due=sum(x['amount_cents'] for x in charges) if charges else None
    confirmed=sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('status')=='confirmed' and x.get('category')=='period' and x.get('period')==month)
    allocated=sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('category')=='allocation' and x.get('period')==month)
    advance=sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('status')=='confirmed' and x.get('category')=='advance')-sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('category')=='allocation')
    deposit=sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('status')=='confirmed' and x.get('category')=='deposit')
    pending=sum(x['amount_cents'] for x in s['payments'] if x['resident_id']==rid and x.get('status')=='customer_reported')
    nums={'due':due,'confirmed':confirmed+allocated,'outstanding':None if due is None else max(0,due-confirmed-allocated),'advance_balance':advance,'deposit':deposit,'pending':pending}
    return {'resident_id':rid,'period':month,**{k:None if v is None else amount(v) for k,v in nums.items()},**{k+'_cents':v for k,v in nums.items()}}

def apply_sales(new,state,body,actor,at,facility_id):
    from .departments import add_history
    from .resident_profile import validate_profile
    s=sales_state(state);command=body['action'];v=deepcopy(body.get('values',{}));ident=body.get('id','')
    if command not in COMMANDS: raise ValueError('未知销售操作')
    if body.get('draft_context') is not None: raise ValueError('销售业务操作不接受角色或草稿上下文')
    def keys(required=(),optional=()):
        if set(v)-set(required)-set(optional): raise ValueError('销售操作存在未识别字段')
        if any(k not in v or v[k] is None or v[k]=='' for k in required): raise ValueError('请补齐必要业务字段：'+','.join(required))
        for key,value in v.items():
            if key not in {'age','amount','price_amount','is_primary','is_fee_contact','resident_ids'}: text(value,key,False,4000)
    def get(collection,id):
        row=next((r for r in collection if r['id']==id),None)
        if row is None: raise ValueError('关联业务对象不存在')
        return row
    def resident(rid):
        row=next((r for r in new['tables']['residents'] if r.get('resident_id')==rid and r.get('facility_id')==facility_id),None)
        if row is None: raise ValueError('共享长者主档不存在')
        return row
    def link(rid,cid):
        resident(rid);get(s['contacts'],cid)
        if not any(x['resident_id']==rid and x['contact_id']==cid for x in s['relations']): raise ValueError('联系人与老人未建立关系')
    def record(collection,prefix,values,status='recorded'):
        if len(collection)>=5000: raise ValueError('销售对象数量超过本地Demo上限')
        if 'amount_cents' in values: values={**values,'amount':amount(values['amount_cents'])}
        row={'id':prefix+'-'+uuid4().hex[:12],**values,'status':status,'created_at':at,'updated_at':at,'created_by':actor,'history':[]}
        add_history(row,actor,at,command,None);collection.append(row);return row
    def change(row,values):
        before={k:deepcopy(x) for k,x in row.items() if k!='history'};row.update(values);add_history(row,actor,at,command,before);return row
    def prospect(id):
        row=get(state['rows']['leads'],id)
        if row.get('sales_version')!=1: raise ValueError('旧咨询请保留旧业务入口；不猜老人及联系人身份')
        return row
    def admitted(rid):
        resident(rid)
        if not any(x.get('resident_id')==rid for x in effective_stays(new,facility_id,at[:10])): raise ValueError('费用仅关联已有实际入住事实的老人')
    def usable(bed):
        if maintenance_blocks(new,bed): raise ValueError('床位/房间存在未关闭维修，暂不可预留或入住')
        if bed['status']=='unavailable': raise ValueError('床位暂不可用')
    related=None
    if command=='inquiry_create':
        keys(('occurred_at','result'),('resident_id','display_name','sex','age','current_living','contact_id','contact_name','phone','relationship','source'))
        moment(v['occurred_at'],at,True);result=text(v['result'],'咨询结果',True)
        if v.get('resident_id'):
            r=resident(v['resident_id'])
            if set(v)&{'display_name','sex','age'}: raise ValueError('已存在主档请在原授权主档入口修改人口资料')
        else:
            name=text(v.get('display_name',''),'老人姓名',True,120)
            r={'facility_id':facility_id,'resident_id':'RES-'+uuid4().hex[:12],'display_name':name,'unit':'','bed':'','what_matters':'','support':'','note':''}
            for key in ('sex','age'):
                if key in v:r[key]=v[key]
            validate_profile(r);new['tables']['residents'].append(r)
        if v.get('contact_id'): c=get(s['contacts'],v['contact_id'])
        else:c=record(s['contacts'],'CONTACT',{'name':text(v.get('contact_name',''),'联系人姓名',True,120),'phone':text(v.get('phone',''),'电话',False,40)})
        if not any(x['resident_id']==r['resident_id'] and x['contact_id']==c['id'] for x in s['relations']):
            record(s['relations'],'REL',{'resident_id':r['resident_id'],'contact_id':c['id'],'relationship':text(v.get('relationship','未确认'),'关系',False,80),'is_primary':not any(x['resident_id']==r['resident_id'] and x.get('is_primary') for x in s['relations']),'is_fee_contact':False})
        row=next((x for x in state['rows']['leads'] if x.get('sales_version')==1 and x.get('resident_id')==r['resident_id']),None)
        if row is None:row=record(state['rows']['leads'],'SALES',{'sales_version':1,'resident_id':r['resident_id'],'title':r['display_name'],'owner':'sales','stage':'new','intent':'未判断','first_inquiry_at':v['occurred_at'],'current_living':text(v.get('current_living',''))},'new')
        inquiry=record(s['inquiries'],'INQ',{'resident_id':r['resident_id'],'prospect_id':row['id'],'contact_id':c['id'],'occurred_at':v['occurred_at'],'source':text(v.get('source','')),'result':result})
        related={'resident_id':r['resident_id'],'contact_id':c['id'],'inquiry_id':inquiry['id']}
    elif command=='contact_create':
        keys(('name',),('phone',));row=record(s['contacts'],'CONTACT',{'name':text(v['name'],'联系人',True,120),'phone':text(v.get('phone',''),'电话',False,40)})
    elif command=='relation_create':
        keys(('resident_id','contact_id','relationship'),('is_primary','is_fee_contact'));resident(v['resident_id']);get(s['contacts'],v['contact_id'])
        if any(x['resident_id']==v['resident_id'] and x['contact_id']==v['contact_id'] for x in s['relations']): raise ValueError('此联系人关系已存在')
        for key in ('is_primary','is_fee_contact'):
            if key in v and not isinstance(v[key],bool): raise ValueError('联系人标记须为布尔值')
        if v.get('is_primary') and any(x['resident_id']==v['resident_id'] and x.get('is_primary') for x in s['relations']): raise ValueError('已有主要联系人，请保留明确唯一关系')
        row=record(s['relations'],'REL',{**v,'relationship':text(v['relationship'],'关系',True,80),'is_primary':v.get('is_primary',False),'is_fee_contact':v.get('is_fee_contact',False)})
    elif command=='followup_create':
        keys(('prospect_id','contact_id','occurred_at','method','result'),('next_action','next_followup_at'));p=prospect(v['prospect_id']);link(p['resident_id'],v['contact_id']);moment(v['occurred_at'],at,True)
        if p['stage'] in {'admitted','lost'}: raise ValueError('已入住或正式流失的咨询不可继续销售跟进')
        if v.get('next_followup_at'):moment(v['next_followup_at'],at)
        row=record(s['followups'],'FOLLOW',{**v,'method':text(v['method'],'方式',True,80),'result':text(v['result'],'跟进结果',True),'next_action':text(v.get('next_action','')),'next_followup_at':v.get('next_followup_at','')})
        change(p,{'next_action':row['next_action'],'next_followup_at':row['next_followup_at']})
    elif command=='visit_create':
        keys(('resident_ids','contact_id','occurred_at','result'),('areas','concerns'));ids=v['resident_ids']
        if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids): raise ValueError('到访关联老人名单格式错误')
        moment(v['occurred_at'],at,True)
        for rid in ids:link(rid,v['contact_id'])
        row=record(s['visits'],'VISIT',{**v,'result':text(v['result'],'到访结果',True),'areas':text(v.get('areas','')),'concerns':text(v.get('concerns',''))})
    elif command in {'stage_change','intent_update'}:
        p=prospect(ident)
        if command=='intent_update':
            keys(('intent',));
            if v['intent'] not in {'高','中','低','未判断'}: raise ValueError('意向等级错误')
            row=change(p,{'intent':v['intent']})
        else:
            keys(('stage',),('reason',));stage=v['stage']
            if stage not in {'new','contacted','visit_scheduled','visited','intent','paused','lost'} or p['stage'] in {'admitted','reserved','preparing'}: raise ValueError('阶段须通过对应业务办理，不可跳过预留/入住事实')
            progression={'new':{'contacted','paused','lost'},'contacted':{'visit_scheduled','visited','paused','lost'},'visit_scheduled':{'visited','paused','lost'},'visited':{'intent','paused','lost'},'intent':{'paused','lost'},'paused':{'contacted','visit_scheduled','visited','intent','lost'},'lost':set()}
            if stage not in progression.get(p['stage'],set()): raise ValueError('当前阶段不支持此变更')
            if stage in {'visited','intent'} and not any(p['resident_id'] in x['resident_ids'] for x in s['visits']): raise ValueError('请先登记真实到访事件')
            reason=text(v.get('reason',''),'原因',stage in {'lost','paused'})
            data={'stage':stage,'status':stage,'stage_reason':reason}
            if stage=='lost':data.update(lost_at=at,lost_reason=reason)
            row=change(p,data)
    elif command=='preference_create':
        keys(('resident_id','source','category','content'),('contact_id',));resident(v['resident_id'])
        if v['source'] not in {'老人本人','家属'} or v['category'] not in {'房间','生活','饮食','活动','沟通','其他'}: raise ValueError('偏好来源或类别错误')
        if v['source']=='家属':link(v['resident_id'],v.get('contact_id',''))
        elif v.get('contact_id'):raise ValueError('老人本人偏好不附家属身份')
        row=record(s['preferences'],'PREF',{**v,'content':text(v['content'],'偏好',True)})
    elif command in {'bed_create','bed_update'}:
        if command=='bed_create':
            keys(('id','label','building','floor','room','source'),('room_type',))
            if not re.fullmatch(r'[A-Za-z0-9_\-一-龥]{1,80}',text(v['id'],'床号',True,80)): raise ValueError('床号格式错误')
            if any(b['id']==v['id'] for b in state['beds']): raise ValueError('床号已存在')
            for k in ('label','building','floor','room','source'):text(v[k],k,True,160)
            row={**v,'status':'available','resident_id':'','created_at':at,'created_by':actor};state['beds'].append(row)
        else:
            keys((),('building','floor','room','room_type','label','status','unavailable_reason','unavailable_source','expected_available_at','price_amount','price_source'))
            row=get(state['beds'],ident)
            for k,x in v.items():
                if k!='price_amount':text(x,k,False,300)
            if 'status' in v:
                if row['status'] in {'occupied','reserved'} or v['status'] not in {'available','unavailable'}: raise ValueError('入住/预留床位只能通过对应事实办理')
                if v['status']=='unavailable' and (not v.get('unavailable_reason') or not v.get('unavailable_source')): raise ValueError('停用须原因与来源')
            if v.get('expected_available_at'):moment(v['expected_available_at'],at)
            if ('price_amount' in v)!=('price_source' in v) or 'price_amount' in v and not v.get('price_source'): raise ValueError('价格金额与可信来源须同时填写')
            if 'price_amount' in v:v['price_cents']=money(v.pop('price_amount'))
            row.update(v)
        record(s['bed_history'],'BEDH',{'bed_id':row['id'],'operation':command,'values':deepcopy(v)})
    elif command=='reserve':
        keys(('bed_id','starts_at','expires_at','reason'));p=prospect(ident)
        if p['stage']!='intent': raise ValueError('须完成到访及明确入住意向后预留')
        bed=get(state['beds'],v['bed_id']);usable(bed)
        starts=moment(v['starts_at'],at);expires=moment(v['expires_at'],at);now=datetime.fromisoformat(at)
        if expires<=starts or expires<=now: raise ValueError('预留截止须晚于开始及当前时间')
        if bed['status']!='available' or any(x['bed_id']==bed['id'] and x['status']=='active' for x in s['reservations']): raise ValueError('床位已有入住或预留，请刷新核对')
        if any(r.get('resident_id')==p['resident_id'] and r['status']=='active' for r in s['reservations']): raise ValueError('该老人已有有效或待处理预留')
        if resident(p['resident_id']).get('bed'): raise ValueError('老人主档已有占床，不重复预留')
        row=record(s['reservations'],'RESERVE',{**v,'reason':text(v['reason'],'预留原因',True),'resident_id':p['resident_id'],'prospect_id':p['id'],'created_by':actor},'active')
        bed.update(status='reserved',resident_id='',reservation_id=row['id'],lead_id=p['id']);change(p,{'stage':'reserved','status':'reserved','bed_id':bed['id'],'reservation_id':row['id']})
    elif command=='reservation_release':
        keys(('reason',));row=get(s['reservations'],ident)
        if row['status']!='active': raise ValueError('预留已办理，不重复释放')
        bed=get(state['beds'],row['bed_id'])
        if bed.get('reservation_id')!=row['id'] or bed['status']!='reserved': raise ValueError('共享床位状态已变化')
        change(row,{'status':'released','release_reason':text(v['reason'],'释放原因',True),'released_at':at});bed.update(status='available',reservation_id='',lead_id='')
        p=prospect(row['prospect_id']);change(p,{'stage':'intent','status':'intent','bed_id':'','reservation_id':''})
        for prep in s['preparations']:
            if prep['prospect_id']==p['id'] and prep['status']=='active':change(prep,{'status':'cancelled','cancelled_at':at,'reason':'预留已明确释放'})
    elif command in {'preparation_create','preparation_update','move_in'}:
        if command=='preparation_update':
            keys(('item','status'),('note','source'));row=get(s['preparations'],ident)
            if row['status']!='active' or v['item'] not in PREP_ITEMS or v['status'] not in {'未开始','处理中','已完成','不适用'}: raise ValueError('准备事项或状态错误')
            if v['item']=='care' and v['status']=='已完成' and not v.get('source'): raise ValueError('护理评估完成须登记已确认资料来源，不代做专业判断')
            items=deepcopy(row['items']);items[v['item']]={'status':v['status'],'note':text(v.get('note','')),'source':text(v.get('source','')),'recorded_at':at,'recorded_by':actor};change(row,{'items':items})
        else:
            p=prospect(ident);res=get(s['reservations'],p.get('reservation_id',''));bed=get(state['beds'],res['bed_id']);usable(bed)
            if res['status']!='active' or moment(res['expires_at'],at)<=datetime.fromisoformat(at) or bed['status']!='reserved' or bed.get('reservation_id')!=res['id']: raise ValueError('预留已过期/失效或床位已变化，先处理预留')
            if command=='preparation_create':
                keys(('planned_move_in_at',));moment(v['planned_move_in_at'],at)
                if p['stage']!='reserved' or any(x['prospect_id']==p['id'] and x['status']=='active' for x in s['preparations']): raise ValueError('已进入入住准备或阶段不支持')
                row=record(s['preparations'],'PREP',{'prospect_id':p['id'],'resident_id':p['resident_id'],'bed_id':bed['id'],'planned_move_in_at':v['planned_move_in_at'],'items':{k:{'status':'未开始','note':'','source':''} for k in PREP_ITEMS}},'active');change(p,{'stage':'preparing','status':'preparing','planned_move_in_at':v['planned_move_in_at'],'preparation_id':row['id']})
            else:
                keys(('actual_move_in_at',),('unit',));actual=moment(v['actual_move_in_at'],at,True)
                if p['stage']!='preparing': raise ValueError('请先完成入住准备')
                prep=get(s['preparations'],p.get('preparation_id',''))
                if prep['status']!='active' or any(item['status'] not in {'已完成','不适用'} for item in prep['items'].values()): raise ValueError('入住准备事项未完成或明确不适用')
                if actual<moment(res['starts_at'],at): raise ValueError('实际入住早于床位预留开始')
                r=resident(p['resident_id'])
                if r.get('bed') or any(x.get('facility_id')==facility_id and x.get('resident_id')==r['resident_id'] and x.get('status') in {'在院','外出','外院'} and (not x.get('end_date') or actual.astimezone(datetime.fromisoformat(at).tzinfo).date().isoformat()<x['end_date']) for x in new['tables'].get('stays',[])): raise ValueError('此老人已有占床或在院事实')
                unit=text(v.get('unit',r.get('unit','')),'照料区',False,80);r.update(bed=bed['id'],unit=unit)
                stay={'facility_id':facility_id,'stay_id':'STAY-'+uuid4().hex[:12],'resident_id':r['resident_id'],'start_date':actual.astimezone(datetime.fromisoformat(at).tzinfo).date().isoformat(),'end_date':'','status':'在院','unit':unit,'source_ref':'销售实际入住 '+p['id']};new['tables'].setdefault('stays',[]).append(stay)
                bed.update(status='occupied',resident_id=r['resident_id']);change(res,{'status':'consumed','actual_move_in_at':v['actual_move_in_at']});change(prep,{'status':'completed','actual_move_in_at':v['actual_move_in_at']});row=change(p,{'stage':'admitted','status':'admitted','actual_move_in_at':v['actual_move_in_at']});related={'resident_id':r['resident_id'],'stay_id':stay['stay_id'],'bed_id':bed['id']}
    elif command=='charge_create':
        keys(('resident_id','period','amount'),('description',));admitted(v['resident_id']);month=period(v['period']);cents=money(v['amount']);row=record(s['charges'],'CHARGE',{'resident_id':v['resident_id'],'period':month,'amount_cents':cents,'description':text(v.get('description',''))})
    elif command=='payment_report':
        keys(('resident_id','amount','category'),('period','reference'));admitted(v['resident_id'])
        if v['category'] not in {'period','advance','deposit'}: raise ValueError('款项类别错误')
        month=period(v.get('period','')) if v['category']=='period' else ''
        row=record(s['payments'],'PAY',{'resident_id':v['resident_id'],'amount_cents':money(v['amount']),'category':v['category'],'period':month,'reference':text(v.get('reference','')),'reported_by':actor},'customer_reported')
    elif command=='payment_confirm':
        keys(('confirmation_ref',));row=get(s['payments'],ident)
        if row['status']!='customer_reported': raise ValueError('款项已确认，不重复到账')
        if row['category']=='period':
            fees=fee_summary(s,row['resident_id'],row['period'])
            if fees['due_cents'] is None or row['amount_cents']>fees['outstanding_cents']: raise ValueError('请先登记本期应缴；超过尚欠部分请明确作为预交款')
        change(row,{'status':'confirmed','confirmation_ref':text(v['confirmation_ref'],'Demo确认依据',True),'confirmed_at':at,'confirmed_by':actor,'confirmation_mode':'当前Demo管理员明确费用确认'})
    elif command=='apply_advance':
        keys(('resident_id','period','amount','reference'));admitted(v['resident_id']);month=period(v['period']);cents=money(v['amount']);fees=fee_summary(s,v['resident_id'],month)
        if cents<=0 or fees['outstanding_cents'] is None or cents>fees['advance_balance_cents'] or cents>fees['outstanding_cents']: raise ValueError('明确抵扣须在预交余额与本期尚欠范围内；押金不参与')
        row=record(s['payments'],'ALLOC',{'resident_id':v['resident_id'],'period':month,'category':'allocation','amount_cents':cents,'reference':text(v['reference'],'抵扣依据',True),'confirmed_by':actor,'confirmed_at':at},'confirmed')
    else:raise ValueError('未知销售操作')
    return {'record':row,'related':related}


def safe_preparations(rows):
    out=deepcopy(rows)
    def project(value):
        if isinstance(value,dict):
            if isinstance(value.get('items'),dict) and isinstance(value['items'].get('care'),dict):
                value['items']['care']={k:x for k,x in value['items']['care'].items() if k in {'status','assessment_id','recorded_at','recorded_by'}}
            for x in value.values(): project(x)
        elif isinstance(value,list):
            for x in value: project(x)
    project(out)
    return out

def view(payload,at,month,facility_id):
    from .departments import seed,STAFF
    state=seed(payload);s=sales_state(state);month=period(month or at[:7]);now=datetime.fromisoformat(at)
    prospects=[deepcopy(r) for r in state['rows']['leads'] if r.get('sales_version')==1]
    # 只投影销售必要的人口与销售字段，绝不返回主档全对象或照护全文。
    pby={p['resident_id']:p for p in prospects};current_stays=effective_stays(payload,facility_id,at[:10]);admitted_ids={x['resident_id'] for x in current_stays}
    directory=[{'id':r['resident_id'],'label':r.get('display_name',r['resident_id']),'sex':r.get('sex',''),'age':r.get('age',''),'bed':r.get('bed',''),'unit':r.get('unit',''),'is_admitted':r['resident_id'] in admitted_ids, 'current_living':pby.get(r['resident_id'],{}).get('current_living','')} for r in payload.get('tables',{}).get('residents',[]) if r.get('facility_id')==facility_id]
    reservations=deepcopy(s['reservations'])
    for r in reservations:r['effective_status']='expired' if r['status']=='active' and moment(r['expires_at'],at)<=now else r['status']
    beds=effective_beds(payload,state)
    for b in beds:
        if b.get('reservation_id'):b['reservation']=next((r for r in reservations if r['id']==b['reservation_id']),None)
        if b.get('resident_id'):b['resident']=next((r for r in directory if r['id']==b['resident_id']),None)
        b['stay']=next(({k:x.get(k,'') for k in ('stay_id','start_date','status')} for x in current_stays if x.get('resident_id')==b.get('resident_id')),None)
        if 'price_cents' in b:b['price_amount']=amount(b['price_cents'])
    losses={}
    for p in prospects:
        if p['stage']=='lost':reason=p.get('lost_reason','未登记');losses[reason]=losses.get(reason,0)+1
    active_res=[r for r in reservations if r['effective_status']=='active']
    stats={'inquiries':len(s['inquiries']),'visits':len(s['visits']),'intents':sum(p['stage'] in {'intent','reserved','preparing'} for p in prospects),'reservations':len(active_res),'admitted':sum(p['stage']=='admitted' and bool(p.get('actual_move_in_at')) for p in prospects),'paused':sum(p['stage']=='paused' for p in prospects),'lost':sum(p['stage']=='lost' for p in prospects),'loss_reasons':losses,'beds':{'total':len(beds),**{status:sum(b['effective_status']==status for b in beds) for status in ('available','reserved','occupied','unavailable')}},'due_followups':sum(p['stage'] not in {'admitted','lost'} and bool(p.get('next_followup_at')) and moment(p['next_followup_at'],at)<=now for p in prospects),'preparations':sum(x['status']=='active' for x in s['preparations'])}
    return {'as_of':at,'period':month,'residents':directory,'prospects':prospects,**{k:deepcopy(s[k]) for k in COLLECTIONS if k!='bed_history' and k!='reservations'},'reservations':reservations,'beds':beds,'fees':[fee_summary(s,r['id'],month) for r in directory if r['id'] in admitted_ids],'statistics':stats,'staff':STAFF,'preparations':safe_preparations(s['preparations']),'legacy_leads':[{'id':r['id'],'title':r.get('title',''),'status':r['status'],'source':'原部门Demo咨询，不猜老人/联系人对应'} for r in state['rows']['leads'] if r.get('sales_version')!=1],'statistics_basis':'本机登记目录；事件累计数，到访一次多人只计一次；无未定义转化率'}


def install(app,db,get_ws,touch):
    from fastapi import Request
    from sqlalchemy import select
    from zoneinfo import ZoneInfo
    from .database import facilities
    from .security import authenticate,fail
    @app.get('/api/sales')
    def sales_view(request:Request,period:str=''):
        with db.read() as c:
            user,token=authenticate(c,request)
            if user['role']!='admin' or '*' not in user['units']:fail(403,'销售Demo仅限当前全院管理员，未新增岗位账号')
            w=get_ws(c,user);f=c.execute(select(facilities).where(facilities.c.id==user['facility_id'])).mappings().one();at=datetime.now(ZoneInfo(f['timezone'])).isoformat(timespec='seconds')
            return {'revision':w['revision'],'timezone':f['timezone'],**view(w['payload'],at,period,user['facility_id'])}
