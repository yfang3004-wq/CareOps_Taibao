"""护理普通服务记录；共享主档/计划/任务/交班，旧正式flow复核不变。"""
from copy import deepcopy
from datetime import datetime,date
from hashlib import sha256
from uuid import uuid4
import re

COLLECTIONS=('assignments','plan_metadata','orders','task_metadata','health_reminders','observations','handover_metadata','assessment_links')
STATION={'assign','health_confirm','plan_create','order_confirm','schedule_order','observation_handle','refer_medical','admission_assess'}
COMMON={'task_complete','task_incomplete','task_retry','service_request','observation_create','handover_create','handover_receive'}
HEALTH={'疾病','过敏','行动','跌倒风险','吞咽饮食','排泄','睡眠','其他'}
TYPES={'食欲异常','情绪异常','睡眠异常','排泄异常','皮肤异常','疼痛不适','行动变化','跌倒疑似跌倒','其他'}
REASONS={'老人拒绝','老人不在','身体不适','临时取消','延后','设备环境','其他'}
NEXT={'稍后再做','下一班跟进','护士站关注','无需继续'}

def nursing_state(state):
    n=state.setdefault('nursing',{'version':1})
    if not isinstance(n,dict) or n.get('version')!=1:raise ValueError('护理扩展版本错误')
    for key in COLLECTIONS:
        rows=n.setdefault(key,[])
        if not isinstance(rows,list) or len(rows)>5000 or any(not isinstance(x,dict) or not isinstance(x.get('id'),str) for x in rows):raise ValueError('护理集合结构错误')
    return n

def txt(v,label='内容',required=False,limit=4000):
    if not isinstance(v,str) or len(v)>limit or required and not v.strip() or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',v):raise ValueError(label+'格式错误')
    return v.strip()

def day(v):
    txt(v,'日期',True,10)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):raise ValueError('日期须为YYYY-MM-DD')
    try:d=date.fromisoformat(v)
    except ValueError:raise ValueError('日期须为YYYY-MM-DD')
    if d.year<1900 or d.year>2200:raise ValueError('Demo日期范围1900..2200')
    return v

def clock(v):
    if not isinstance(v,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v):raise ValueError('时点须为HH:mm')
    return v

def stamp(v,ref=None):
    t=datetime.fromisoformat(v)
    if ref and t.tzinfo is not None:t=t.astimezone(datetime.fromisoformat(ref).tzinfo)
    return t.strftime('%Y-%m-%d %H:%M:%S')

def actual(v,source,at):
    txt(v,'实际时间',False,50);txt(source,'时间来源',False,50)
    if bool(v)!=bool(source):raise ValueError('实际时间与来源成对，可均留空')
    if source and source not in {'本人记录','原始记录','交班记录'}:raise ValueError('实际时间来源错误')
    if v:
        if 'T' not in v and ' ' not in v:raise ValueError('实际时间须包含日期与时间')
        t=datetime.fromisoformat(v);now=datetime.fromisoformat(at)
        if t.tzinfo is None:t=t.replace(tzinfo=now.tzinfo)
        if t>now:raise ValueError('实际时间不得晚于提交')
    return v

def active_residents(payload,facility,when):
    admitted={s['resident_id']:s for s in payload.get('tables',{}).get('stays',[]) if s.get('facility_id')==facility and s.get('status') in {'在院','外出','外院'} and s.get('start_date','9999')<=when and (not s.get('end_date') or when<s['end_date'])}
    return [(r,admitted[r['resident_id']]) for r in payload['tables']['residents'] if r.get('facility_id')==facility and r.get('resident_id') in admitted]

def own_assignment(n,rid,who,when):
    return next((a for a in n['assignments'] if a.get('resident_id')==rid and a.get('owner')==who and a['date_from']<=when and (not a.get('date_to') or when<a['date_to'])),None)

def current_owner(n,rid,when):
    a=next((a for a in n['assignments'] if a.get('resident_id')==rid and a['date_from']<=when and (not a.get('date_to') or when<a['date_to'])),None)
    return a.get('owner','') if a else ''

def remaining(n,order):
    if order['status']!='confirmed':return None
    return max(0,order['total_count']-sum(t.get('status')=='completed' and t.get('order_id')==order['id'] for t in n['task_metadata']))

def task_id(plan_id,rid,rule_id,when,slot,schedule=''):
    return 'NTS-'+sha256('|'.join((plan_id,rid,rule_id,when,slot,schedule)).encode()).hexdigest()[:12]

def planned_tasks(payload,state,n,facility,when):
    active={r['resident_id'] for r,_ in active_residents(payload,facility,when)};result={}
    # 执行过的历史即使计划更替/次数耗尽仍保留，不重新补造。
    for t in n['task_metadata']:
        if t.get('date')==when and t.get('resident_id') in active:result[t['task_id']]={**deepcopy(t),'id':t['task_id'],'virtual':False}
    plans={p['plan_id']:p for p in payload['tables'].get('plans',[]) if p.get('facility_id')==facility}
    for meta in n['plan_metadata']:
        p=plans.get(meta['plan_id']);rid=meta['resident_id']
        if not p or rid not in active or p['status']!='已确认' or when<p['start_date'] or p.get('end_date') and when>=p['end_date']:continue
        for rule in meta['rules']:
            category=rule['category'];slots=[];order=None
            if category=='基础':
                if rule['frequency']=='daily' or rule['frequency']=='weekly' and date.fromisoformat(when).isoweekday() in rule['weekdays']:slots=[(slot,'') for slot in rule['times']]
            else:
                order=next((o for o in n['orders'] if o['id']==rule.get('order_id') and o['status']=='confirmed' and o['effective_from']<=when and (not o.get('effective_until') or when<o['effective_until'])),None)
                if order:slots=[(s['time'],s['id']) for s in order.get('schedules',[]) if s['date']==when and s['plan_id']==p['plan_id']]
            for slot,schedule in slots:
                tid=task_id(p['plan_id'],rid,rule['id'],when,slot,schedule)
                if tid in result:continue
                result[tid]={'id':tid,'task_id':tid,'resident_id':rid,'plan_id':p['plan_id'],'rule_id':rule['id'],'title':rule['name'],'date':when,'slot':slot,'scheduled_at':when+'T'+slot,'schedule_id':schedule,'owner':current_owner(n,rid,when),'category':category,'order_id':order['id'] if order else '', 'status':'pending','ordinary':True,'reason':'','next_step':'','next_action':'','performed_at':'','performed_source':'','completed_at':'','completed_by':'','virtual':True}
    return sorted(result.values(),key=lambda t:(t['scheduled_at'],t['resident_id'],t['id']))

def mode_permission(user,mode,action=None):
    if mode not in {'caregiver','station'}:raise ValueError('护理工作台模式错误')
    role=user.get('role','admin')
    if mode=='station' and role not in {'admin','supervisor'}:raise PermissionError('当前认证账号无护士站权限')
    if mode=='caregiver' and role not in {'admin','caregiver','supervisor'}:raise PermissionError('当前认证账号无照护权限')
    if action in STATION and mode!='station':raise PermissionError('护理员模式不接受专业确认或主管动作')

def scope(user,resident):return '*' in user.get('units',['*']) or resident.get('unit','') in user.get('units',[])

def authorize(c,user,body,payload):
    """原命令事务内验证真实角色与真实分派账号，不信任客户端角色。"""
    from sqlalchemy import select
    from .database import users
    mode_permission(user,body.get('values',{}).get('mode'),body['action'])
    if body['action'] in {'assign','handover_create','task_incomplete'} and (body['values'].get('owner_id') or body['values'].get('receiver_id')):
        key='owner_id' if body['action']=='assign' else 'receiver_id';uid=body['values'].get(key)
        target=c.execute(select(users).where(users.c.id==uid,users.c.facility_id==user['facility_id'],users.c.active==True)).mappings().first()
        if not target or target['role'] not in {'admin','caregiver','supervisor'}:raise ValueError('分派/接班须选择当前机构真实照护账号')
        rid=body['values'].get('resident_id')
        if not rid and body['action']=='task_incomplete' and body['values'].get('date'):
            from .departments import seed
            state=seed(payload);n=nursing_state(state)
            rid=next((t['resident_id'] for t in planned_tasks(payload,state,n,user['facility_id'],day(body['values']['date'])) if t['id']==body.get('id')),None)
        if rid:
            r=next((r for r in payload['tables']['residents'] if r.get('resident_id')==rid),None)
            if r and not scope(dict(target),r):raise ValueError('目标账号不在该老人照料区范围')

def apply_nursing(new,state,body,who,at,facility,user=None):
    from .departments import add_history
    n=nursing_state(state);v=deepcopy(body.get('values',{}));mode=v.pop('mode',None);action=body['action'];ident=body.get('id','');user=user or {'role':'admin','units':['*'],'id':who.removeprefix('account:')}
    mode_permission(user,mode,action)
    if action not in STATION|COMMON:raise ValueError('未知护理操作')
    if body.get('draft_context') is not None:raise ValueError('护理操作不接受账号身份/草稿上下文')
    today=at[:10]
    def keys(required=(),optional=()):
        if set(v)-set(required)-set(optional) or any(k not in v or v[k] is None or v[k]=='' for k in required):raise ValueError('请补齐护理必要字段，且不注入其他字段')
        for k,value in v.items():
            if k not in {'rules','total_count'}:txt(value,k,False,8000)
    def get(rows,id,key='id'):
        r=next((r for r in rows if r.get(key)==id),None)
        if not r:raise ValueError('护理关联对象不存在')
        return r
    def resident(rid,when=today,require_stay=True):
        r=next((r for r in new['tables']['residents'] if r.get('resident_id')==rid and r.get('facility_id')==facility),None)
        if not r or not scope(user,r):raise PermissionError('老人不在当前认证范围')
        if require_stay and rid not in {r['resident_id'] for r,_ in active_residents(new,facility,when)}:raise ValueError('仅有效入住老人可安排每日照护')
        if mode=='caregiver' and not own_assignment(n,rid,who,when):raise PermissionError('护理员只能办理本人已分派老人')
        return r
    def make(rows,prefix,data):
        if len(rows)>=5000:raise ValueError('本地护理集合超过上限')
        row={'id':prefix+'-'+uuid4().hex[:12],**data,'created_at':at,'created_by':who,'updated_at':at,'history':[]};add_history(row,who,at,action,None);rows.append(row);return row
    def change(row,data):
        before={k:deepcopy(x) for k,x in row.items() if k!='history'};row.update(data);add_history(row,who,at,action,before)
        if action=='task_complete':row['history'][-1].update(performed_at=row.get('performed_at',''),performed_source=row.get('performed_source',''))
        return row
    def handover(rid,observation,request,shift,receiver,done='',task='',obs=''):
        resident(rid);txt(observation,'交班事实',True);txt(request,'下一步',True);txt(receiver,'接班账号',True,80)
        if shift not in {'早班','中班','晚班','夜班'}:raise ValueError('请明确接班班次')
        hid='NHO-'+uuid4().hex[:12]
        shared={'facility_id':facility,'handover_id':hid,'resident_id':rid,'created_at':stamp(at),'shift':shift,'sender':who,'receiver':'account:'+receiver,'observation':observation,'request':request,'due_at':'','status':'待接收','accepted_at':'','outcome':done,'verified_by':'','verified_at':''}
        new['tables'].setdefault('handovers',[]).append(shared)
        make(n['handover_metadata'],'NHM',{'handover_id':hid,'resident_id':rid,'from_task_id':task,'from_observation_id':obs,'received_by':''})
        return {'id':hid,**shared}
    def observation(rid,typ,description,occurred=''):
        resident(rid)
        if typ not in TYPES:raise ValueError('异常类别错误')
        txt(description,'异常一句话',True)
        if occurred:actual(occurred,'本人记录',at)
        return make(n['observations'],'NOB',{'resident_id':rid,'type':typ,'description':description,'occurred_at':occurred,'status':'new'})
    def new_plan(rid,start,goal,source,rules,replaces=''):
        resident(rid,start);day(start);txt(goal,'目标',True);txt(source,'已确认计划来源',True)
        if not isinstance(rules,list) or not rules or len(rules)>30:raise ValueError('计划需要1..30条明确规则')
        checked=[]
        for rule in rules:
            if not isinstance(rule,dict) or set(rule)-{'name','category','frequency','weekdays','times','order_id'}:raise ValueError('服务排期规则结构错误')
            name=txt(rule.get('name',''),'服务名称',True,120);category=rule.get('category');frequency=rule.get('frequency')
            if not isinstance(category,str) or category not in {'基础','增值'} or not isinstance(frequency,str) or frequency not in {'daily','weekly','manual'}:raise ValueError('服务类型/频次错误')
            days=rule.get('weekdays',[]);times=rule.get('times',[])
            if not isinstance(days,list) or any(isinstance(x,bool) or not isinstance(x,int) or x not in range(1,8) for x in days) or len(set(days))!=len(days) or frequency=='weekly' and not days:raise ValueError('每周规则必须明确1..7星期')
            if not isinstance(times,list) or len(times)>12 or frequency!='manual' and not times or any(not isinstance(x,str) for x in times):raise ValueError('计划时点错误')
            for t in times:clock(t)
            if len(set(times))!=len(times):raise ValueError('计划时点不可重复')
            order_id=rule.get('order_id','')
            if category=='增值':
                order=get(n['orders'],order_id)
                if order['resident_id']!=rid or order['status']!='confirmed':raise ValueError('增值规则只能关联此老人已确认订购')
            elif order_id or frequency=='manual':raise ValueError('基础服务应选择每日或具体星期，不引用增值订单')
            checked.append({'id':'NR-'+uuid4().hex[:12],'name':name,'category':category,'frequency':frequency,'weekdays':days,'times':times,'order_id':order_id})
        old=None
        if replaces:
            old=get(n['plan_metadata'],replaces,'plan_id')
            if old['resident_id']!=rid or old['status']!='confirmed':raise ValueError('只能更替此老人当前确认的版本')
            oldshared=get(new['tables']['plans'],replaces,'plan_id')
            if start<=oldshared['start_date']:raise ValueError('新版生效日必须晚于原版本开始日')
            oldshared['end_date']=start;change(old,{'status':'superseded','end_date':start})
        pid='NPL-'+uuid4().hex[:12];meta=make(n['plan_metadata'],'NPM',{'plan_id':pid,'resident_id':rid,'status':'confirmed','version':old['version']+1 if old else 1,'source':source,'confirmed_by':who,'confirmed_at':at,'start_date':start,'end_date':'','rules':checked,'task_ids':[],'copied_from':replaces})
        shared={'facility_id':facility,'plan_id':pid,'resident_id':rid,'assessment_id':'','start_date':start,'end_date':'','goal':goal,'measure':'；'.join(x['name'] for x in checked),'owner':who,'status':'已确认','approver':who,'review_due':'','note':'NURSING_PLAN:'+meta['id']};new['tables'].setdefault('plans',[]).append(shared)
        return {**meta,'id':pid,'goal':goal}
    related=None
    if action=='assign':
        keys(('resident_id','owner_id','date_from'),('date_to','shift'));r=resident(v['resident_id'],v['date_from']);start=day(v['date_from']);end=day(v['date_to']) if v.get('date_to') else ''
        if end and end<=start:raise ValueError('分派结束须晚于开始')
        if any(a['resident_id']==r['resident_id'] and a['date_from']<(end or '9999') and start<(a.get('date_to') or '9999') for a in n['assignments']):raise ValueError('此老人分派日期重叠，请保留明确责任人')
        row=make(n['assignments'],'NAS',{'resident_id':r['resident_id'],'owner':'account:'+v['owner_id'],'owner_id':v['owner_id'],'date_from':start,'date_to':end,'shift':v.get('shift','')})
    elif action=='health_confirm':
        keys(('resident_id','category','content','source'));resident(v['resident_id'])
        if v['category'] not in HEALTH:raise ValueError('提醒类别错误')
        row=make(n['health_reminders'],'NHE',{**v,'content':txt(v['content'],'已确认必要提醒',True),'source':txt(v['source'],'原始来源',True),'confirmed_by':who})
    elif action=='plan_create':
        keys(('resident_id','start_date','goal','source','rules'),('replaces_id',));row=new_plan(v['resident_id'],v['start_date'],v['goal'],v['source'],v['rules'],v.get('replaces_id',''))
    elif action=='service_request':
        keys(('resident_id','name'),('note',));resident(v['resident_id']);row=make(n['orders'],'NOR',{'resident_id':v['resident_id'],'name':txt(v['name'],'需求服务',True,120),'note':txt(v.get('note','')),'status':'requested','total_count':None,'schedules':[]})
    elif action=='order_confirm':
        keys(('total_count','effective_from','source'),('effective_until',));row=get(n['orders'],ident);resident(row['resident_id']);start=day(v['effective_from']);end=day(v['effective_until']) if v.get('effective_until') else ''
        if row['status']!='requested' or isinstance(v['total_count'],bool) or not isinstance(v['total_count'],int) or not 1<=v['total_count']<=10000 or end and end<=start:raise ValueError('只有需求可明确确认正整数次数及有效区间')
        change(row,{**v,'status':'confirmed','confirmed_by':who,'confirmed_at':at,'source':txt(v['source'],'购买确认来源',True),'effective_until':end})
    elif action=='schedule_order':
        keys(('date','time'));row=get(n['orders'],ident);when=day(v['date']);clock(v['time']);resident(row['resident_id'],when)
        if row['status']!='confirmed' or remaining(n,row)<=0 or when<row['effective_from'] or row.get('effective_until') and when>=row['effective_until']:raise ValueError('须已确认购买、日期有效且有剩余次数才安排')
        if len(row['schedules'])>=row['total_count']:raise ValueError('已明确排期次数达到订购总次数，不超额安排或隐藏旧安排')
        if any(s['date']==when and s['time']==v['time'] for s in row['schedules']):raise ValueError('同一服务日期时点已安排')
        if not row.get('plan_id'):
            p=new_plan(row['resident_id'],row['effective_from'],'已确认增值服务安排',row['source'],[{'name':row['name'],'category':'增值','frequency':'manual','times':[],'order_id':row['id']}]);row['plan_id']=p['plan_id']
        schedules=deepcopy(row['schedules']);schedules.append({'id':'NSC-'+uuid4().hex[:12],'date':when,'time':v['time'],'plan_id':row['plan_id']});change(row,{'schedules':schedules})
    elif action in {'task_complete','task_incomplete','task_retry'}:
        keys(('date',),('performed_at','performed_source') if action=='task_complete' else ('reason','next_step','next_action','receiver_id','shift') if action=='task_incomplete' else ())
        when=day(v['date'])
        if when>today:raise ValueError('未来日期照护不得提前执行')
        if any(ident in {step.get('task_id') for step in f.get('steps',[])} for f in new.get('flow',{}).get('instances',[])):raise ValueError('原正式流程任务须保留独立复核，不经过普通护理操作')
        t=get(planned_tasks(new,state,n,facility,when),ident);resident(t['resident_id'],when)
        if mode=='caregiver' and t['owner']!=who:raise PermissionError('不是本人负责服务')
        meta=get(n['plan_metadata'],t['plan_id'],'plan_id');sharedplan=get(new['tables']['plans'],t['plan_id'],'plan_id')
        if sharedplan.get('note')!='NURSING_PLAN:'+meta['id'] or t['rule_id'] not in {r['id'] for r in meta['rules']} or t['resident_id']!=meta['resident_id']:raise ValueError('护理计划/规则关联不匹配')
        old=get(n['task_metadata'],ident,'task_id') if not t['virtual'] else None
        if action=='task_retry':
            if not old or old['status']!='incomplete':raise ValueError('仅未完成事项可明确重试')
        elif t['status']!='pending':raise ValueError('此护理事项已记录执行结果，不重复执行')
        if action=='task_complete':
            actual(v.get('performed_at',''),v.get('performed_source',''),at)
            if v.get('performed_at'):
                submitted=datetime.fromisoformat(at);performed=datetime.fromisoformat(v['performed_at'])
                if performed.tzinfo is None:performed=performed.replace(tzinfo=submitted.tzinfo)
                if performed<datetime.fromisoformat(when).replace(tzinfo=submitted.tzinfo):raise ValueError('实际执行时间不得早于该服务计划日；历史服务允许明确补执行')
            if t.get('order_id') and remaining(n,get(n['orders'],t['order_id']))<=0:raise ValueError('增值服务剩余次数不足')
        if action=='task_incomplete':
            if v.get('reason') not in REASONS or v.get('next_step') not in NEXT:raise ValueError('请选择未完成原因与后续安排')
            if v['next_step']=='下一班跟进' and (not v.get('receiver_id') or v.get('shift') not in {'早班','中班','晚班','夜班'}):raise ValueError('下一班跟进须明确接班账号与班次')
        if old is None:
            old=make(n['task_metadata'],'NTM',{**{k:deepcopy(x) for k,x in t.items() if k not in {'id','virtual'}},'ordinary':True})
            meta['task_ids'].append(ident)
            task={'facility_id':facility,'task_id':ident,'resident_id':t['resident_id'],'created_at':when+' 00:00:00','source_type':'照护计划','source_id':t['plan_id'],'title':t['title'],'dept':'照护部','owner':t['owner'] or who,'due_at':when+' '+t['slot']+':00','status':'待处理','accepted_at':'','accepted_by':'','performed_at':'','performed_source':'','completed_at':'','completed_by':'','result':'','reviewer':'','reviewed_at':'','_meta':{'nursing_task_ref':old['id'],'file':'认证护理日常服务','sheet':'护理执行','row':0}};new['tables'].setdefault('tasks',[]).append(task)
        else:task=get(new['tables']['tasks'],ident,'task_id')
        if task.get('_meta',{}).get('nursing_task_ref')!=old['id'] or task.get('source_id')!=old['plan_id'] or task.get('source_type')!='照护计划':raise ValueError('共享任务双向引用错误')
        if action=='task_complete':
            change(old,{'status':'completed','completed_at':at,'completed_by':who,'performed_at':v.get('performed_at',''),'performed_source':v.get('performed_source','')});task.update(status='已关闭',completed_at=stamp(at),completed_by=who,result='普通照护服务已完成（不代表费用结算或正式流程复核）',performed_at=stamp(v['performed_at'],at) if v.get('performed_at') else '',performed_source=v.get('performed_source',''))
        elif action=='task_incomplete':
            change(old,{'status':'incomplete','reason':v['reason'],'next_step':v['next_step'],'next_action':v.get('next_action',''),'incomplete_at':at,'incomplete_by':who});task.update(status='拒绝' if v['reason']=='老人拒绝' else '暂缓',result=v['reason']+'；'+v['next_step']+'；'+v.get('next_action',''))
            if v['next_step']=='下一班跟进':related=handover(t['resident_id'],t['title']+'未完成：'+v['reason'],v.get('next_action') or '下一班明确跟进此服务',v['shift'],v['receiver_id'],task=ident);old['handover_id']=related['id']
            elif v['next_step']=='护士站关注':related=observation(t['resident_id'],'其他',t['title']+'未完成：'+v['reason']);old['observation_id']=related['id']
        else:change(old,{'status':'pending'});task.update(status='待处理',result='上次未完成已保留，明确安排重试')
        row={**old,'id':ident,'virtual':False}
    elif action=='observation_create':
        keys(('resident_id','type','description'),('occurred_at',));row=observation(v['resident_id'],v['type'],v['description'],v.get('occurred_at',''))
    elif action=='observation_handle':
        keys(('status',),('note',));row=get(n['observations'],ident);resident(row['resident_id'])
        if v['status'] not in {'known','observe','adjust','reassess','handled'}:raise ValueError('护士站处理状态错误')
        change(row,{'status':v['status'],'handling_note':txt(v.get('note','')),'handled_by':who,'handled_at':at})
    elif action=='refer_medical':
        keys(('summary',));row=get(n['observations'],ident);resident(row['resident_id'])
        if row.get('medical_id'):raise ValueError('此异常已转医务，不重复生成')
        from .departments import make_row
        summary=txt(v['summary'],'必要医务摘要',True,1000)
        related=make_row(state,'medical',{'title':'护理观察待查看','owner':'medical','resident_id':row['resident_id'],'source':'护理异常 '+row['id'],'note':summary,'occurred_at':row.get('occurred_at','')},who,at);change(row,{'status':'medical','medical_id':related['id']})
    elif action=='handover_create':
        keys(('resident_id','observation','request','shift','receiver_id'),('done',));row=handover(v['resident_id'],v['observation'],v['request'],v['shift'],v['receiver_id'],v.get('done',''))
    elif action=='handover_receive':
        keys();row=get(new['tables']['handovers'],ident,'handover_id');m=get(n['handover_metadata'],ident,'handover_id')
        r=next((r for r,_ in active_residents(new,facility,today) if r['resident_id']==row['resident_id']),None)
        if not r or not scope(user,r):raise PermissionError('交班老人不在当前入住/授权范围')
        if row['receiver']!=who:raise PermissionError('只有指定认证接班账号可接收')
        if row['status']!='待接收':raise ValueError('此交班已接收')
        row.update(status='已接收',accepted_at=stamp(at));change(m,{'received_by':who,'received_at':at});row={'id':ident,**row}
    elif action=='admission_assess':
        keys(('preparation_id','conclusion','source','assessed_at'),('tool_name','tool_version'))
        prep=get(state.get('sales',{}).get('preparations',[]),v['preparation_id'])
        if prep['status']!='active':raise ValueError('只对有效入住准备登记评估')
        r=resident(prep['resident_id'],require_stay=False);conclusion=txt(v['conclusion'],'已确认评估正文',True,8000);source=txt(v['source'],'原评估来源',True)
        actual(v['assessed_at'],'原始记录',at)
        aid='NASM-'+uuid4().hex[:12]
        shared={'facility_id':facility,'assessment_id':aid,'resident_id':r['resident_id'],'assessed_at':stamp(v['assessed_at'],at),'tool_name':txt(v.get('tool_name','未登记工具（结果登记）')),'tool_version':txt(v.get('tool_version','未登记版本（结果登记）')),'score':'','conclusion':conclusion,'assessor':who,'review_due':'','source_ref':source,'note':'专业人员已确认资料登记；本系统不作诊断','_meta':{'nursing_admission':True}};new['tables'].setdefault('assessments',[]).append(shared)
        row=make(n['assessment_links'],'NAL',{'assessment_id':aid,'resident_id':r['resident_id'],'preparation_id':prep['id'],'recorded_at':at})
        before={k:deepcopy(x) for k,x in prep.items() if k!='history'};prep['items']['care']={'status':'已完成','assessment_id':aid,'recorded_at':at,'recorded_by':who};add_history(prep,who,at,'nursing_assessment_status',before)
        related={'preparation_id':prep['id'],'care_status':'已完成','assessment_id':aid}
    else:raise ValueError('未知护理动作')
    return {'record':row,'related':related}


def view(payload,state,user,who,at,mode,when,unit=''):
    mode_permission(user,mode);day(when);n=nursing_state(state)
    visible=[];all_active=active_residents(payload,user['facility_id'],when)
    for r,stay in all_active:
        if not scope(user,r) or unit and r.get('unit')!=unit or mode=='caregiver' and not own_assignment(n,r['resident_id'],who,when):continue
        bed=next((b for b in state['beds'] if b['id']==r.get('bed')),{});rid=r['resident_id']
        preferences=[{'source':'原共享主档','category':'生活','content':r.get('what_matters','')}] if r.get('what_matters') else []
        preferences += [{k:p.get(k,'') for k in ('source','category','content')} for p in state.get('sales',{}).get('preferences',[]) if p['resident_id']==rid]
        health=[deepcopy(h) for h in n['health_reminders'] if h['resident_id']==rid]
        visible.append({'id':rid,'label':r.get('display_name',rid),'bed':r.get('bed',''),'unit':r.get('unit',''),'room':bed.get('room',''),'care_level':r.get('care_level',''),'service_category':'护理（有效入住范围，非自动评定）','stay_start':stay['start_date'],'stay_status':stay['status'],'preferences':preferences,'health_reminders':health,'assigned_to':current_owner(n,rid,when)})
    ids={r['id'] for r in visible}
    tasks=[t for t in planned_tasks(payload,state,n,user['facility_id'],when) if t['resident_id'] in ids and (mode=='station' or t['owner']==who)]
    sharedplans={p['plan_id']:p for p in payload['tables'].get('plans',[]) if p.get('facility_id')==user['facility_id']}
    plans=[{**deepcopy(m),'id':m['plan_id'],'goal':sharedplans.get(m['plan_id'],{}).get('goal','')} for m in n['plan_metadata'] if m['resident_id'] in ids]
    orders=[{**deepcopy(o),'remaining':remaining(n,o),'today_scheduled':any(s['date']==when for s in o.get('schedules',[])),'unarranged_count':max(0,o.get('total_count',0)-len(o.get('schedules',[]))) if o['status']=='confirmed' else None} for o in n['orders'] if o['resident_id'] in ids]
    observations=[deepcopy(o) for o in n['observations'] if o['resident_id'] in ids]
    hm={h['handover_id']:h for h in n['handover_metadata']}
    active_by={r['resident_id']:r for r,_ in all_active if scope(user,r)}
    handovers=[{'id':h['handover_id'],'resident_label':active_by.get(h['resident_id'],{}).get('display_name',h['resident_id']),**deepcopy(h),**{k:m.get(k,'') for k in ('from_task_id','from_observation_id','received_by')}} for h in payload['tables'].get('handovers',[]) if (m:=hm.get(h['handover_id'])) and (h['resident_id'] in ids or mode=='caregiver' and h.get('receiver')==who and h['resident_id'] in active_by)]
    candidates=[];assessments=[]
    if mode=='station':
        activeids={r['resident_id'] for r,_ in all_active};byid={r['resident_id']:r for r in payload['tables']['residents'] if r.get('facility_id')==user['facility_id'] and scope(user,r)}
        candidates=[{'resident_id':p['resident_id'],'label':byid[p['resident_id']].get('display_name',p['resident_id']),'preparation_id':p['id'],'prospect_id':p['prospect_id'],'care_status':p['items']['care']['status']} for p in state.get('sales',{}).get('preparations',[]) if p['status']=='active' and p['resident_id'] in byid and p['resident_id'] not in activeids]
        permitted=ids|{c['resident_id'] for c in candidates};assessments=[{**deepcopy(a),'recorded_at':next((link.get('recorded_at',link.get('created_at','')) for link in n['assessment_links'] if link['assessment_id']==a['assessment_id']),'')} for a in payload['tables'].get('assessments',[]) if a.get('facility_id')==user['facility_id'] and a.get('resident_id') in permitted]
    stats={k:sum(t['status']==k for t in tasks) for k in ('pending','completed','incomplete')};stats.update(new_observations=sum(o['status']=='new' for o in observations),attention=sum(o['status'] not in {'handled'} for o in observations),reassessment=sum(o['status']=='reassess' for o in observations),unreceived_handovers=sum(h['status']=='待接收' for h in handovers))
    return {'mode':mode,'date':when,'actor':who,'residents':visible,'assignments':[deepcopy(a) for a in n['assignments'] if a['resident_id'] in ids],'plans':plans,'orders':orders,'tasks':tasks,'observations':observations,'handovers':handovers,'admission_candidates':candidates,'assessments':assessments,'statistics':stats,'legacy_plans':[{'id':p['plan_id'],'resident_id':p['resident_id'],'goal':p['goal'],'measure':p['measure'],'start_date':p['start_date'],'end_date':p.get('end_date',''),'status':p['status'],'source':'既有未结构化计划，不从文字自动猜排期'} for p in sharedplans.values() if mode=='station' and p['resident_id'] in ids and not any(m['plan_id']==p['plan_id'] for m in n['plan_metadata'])]}


def install(app,db,get_ws,touch):
    from fastapi import Request
    from sqlalchemy import select
    from zoneinfo import ZoneInfo
    from .database import facilities,users
    from .security import authenticate,actor,fail
    from .departments import seed
    @app.get('/api/nursing')
    def nursing_view(request:Request,mode:str='caregiver',date:str='',unit:str=''):
        with db.read() as c:
            u,t=authenticate(c,request);w=get_ws(c,u);f=c.execute(select(facilities).where(facilities.c.id==u['facility_id'])).mappings().one();at=datetime.now(ZoneInfo(f['timezone'])).isoformat(timespec='seconds')
            try:result=view(w['payload'],seed(w['payload']),u,actor(u),at,mode,date or at[:10],unit)
            except PermissionError as e:fail(403,str(e))
            except ValueError as e:fail(422,str(e))
            staff=[{'id':x['id'],'label':x['display_name'],'actor':'account:'+x['id'],'role':x['role'],'units':x['units']} for x in c.execute(select(users).where(users.c.facility_id==u['facility_id'],users.c.active==True)).mappings() if x['role'] in {'admin','caregiver','supervisor'}]
            return {'revision':w['revision'],'timezone':f['timezone'],'staff':staff,**result}
