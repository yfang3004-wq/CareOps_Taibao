"""M3 workday projections and explicit responsibility commands.
Administrative collaboration only. No clinical triage or automatic risk scoring.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Request
from sqlalchemy import select
from . import policy
from .database import users, facilities
from .security import authenticate, actor, REVIEWERS, fail
from .bridge import engine

EXPERIENCE_COMMANDS={'takeover_task','share_repair','revoke_repair'}
BRIEF_KEYS=('location','equipment','fault','access_window','contact_role')

def apply_command(c,user,ws,command):
    new=deepcopy(ws);i=policy.get_flow(user,new,command['instance_id'])
    if i['status']!='active' or i['revision']!=command.get('expected_revision'):fail(409,'流程不在办理中或修订已变化，请刷新核对')
    if command['at']<i['last_action_at']:fail(409,'服务器业务时间早于最后操作，保留现场')
    typ=command['type']; reason=command.get('reason','').strip()
    if not reason:fail(422,'责任接续、发布或撤回必须说明理由')
    before={'revision':i['revision']};event={'at':command['at'],'actor':actor(user),'action':typ,'cycle':i['cycle'],'reason':reason}
    if typ=='takeover_task':
        t,h=policy.takeover_target(user,new,i,command.get('handover_id',''))
        before['owner']=t['owner'];event.update(task_id=t['task_id'],from_owner=t['owner'],to_owner=actor(user),handover_id=h['handover_id'])
        t['owner']=actor(user);t['_assignment_epoch']=t.get('_assignment_epoch',0)+1
        # Keep status and every historical execution/review field intact.
    else:
        if i['kind']!='maintenance' or not i.get('resident_id'):fail(422,'最小信息发布仅用于关联长者的房间维修')
        t=next(t for t in new['tables']['tasks'] if t['task_id']==i['steps'][0]['task_id'])
        if t['status'] in {'待复核','已关闭'}:fail(409,'任务已提交复核或关闭，不能重新发布或撤回；请走正式退回/重开')
        if user['role'] not in REVIEWERS and t['owner']!=actor(user):fail(403,'只有当前负责人或主管可以发布/撤回维修范围')
        before['repair_access']=deepcopy(i.get('repair_access'))
        if typ=='share_repair':
            target=c.execute(select(users).where(users.c.id==command.get('recipient_user_id'),users.c.facility_id==user['facility_id'],users.c.active==True,users.c.role=='maintenance')).mappings().first()
            if not target:fail(422,'请选择同机构有效后勤账户')
            if t['owner'].startswith('account:') and t['owner'] not in {actor(user),actor(target)}:
                owner=c.execute(select(users).where(users.c.id==t['owner'][8:])).mappings().first()
                if owner and owner['role']=='maintenance':fail(409,'任务已由另一后勤账户负责；先由主管明确收回或重新分派')
            brief={k:command.get(k,'').strip() for k in BRIEF_KEYS}
            if any(not v or len(v)>500 for v in brief.values()):fail(422,'五项维修必要信息须填写，每项最多500字符；不要复制长者健康记录')
            i['repair_access']={'user_id':target['id'],'brief':brief,'published_by':actor(user),'at':command['at'],'cycle':i['cycle']}
            event.update(task_id=t['task_id'],recipient_user_id=target['id'])
        else:
            old=i.pop('repair_access',None)
            if not old:fail(409,'目前没有可撤回的最小维修授权')
            if t['owner']=='account:'+old['user_id']:
                if user['role'] not in REVIEWERS:fail(403,'后勤已经接续；须主管撤回并接回责任')
                event.update(task_id=t['task_id'],from_owner=t['owner'],to_owner=actor(user))
                t['owner']=actor(user);t['_assignment_epoch']=t.get('_assignment_epoch',0)+1
    i.setdefault('responsibility_events',[]).append(event)
    i['revision']+=1;i['last_action_at']=command['at']
    new['audit'].append({'time':command['at'],'actor':actor(user),'actor_type':'authenticated-session','role':command['role'],'action':typ,'table':'workflow','id':i['id'],'facility':i['facility_id'],'request_id':command['request_id'],'reason':reason,'before':before,'after':{'revision':i['revision'],**event}})
    new['flow']['receipts'].append({'id':command['request_id'],'payload':__import__('json').dumps(command,ensure_ascii=False,sort_keys=True,separators=(',',':'))})
    engine('validate',workspace=new)
    return new

def enrich_command(new,old,command):
    """Record delegation generation without modifying old identities or task state."""
    typ=command['type']
    if typ not in {'assign_task','create_handover'}:return new
    i=next(i for i in new['flow']['instances'] if i['id']==command['instance_id'])
    task=next(t for t in new['tables']['tasks'] if t['task_id']==command['task_id'])
    if typ=='assign_task':
        previous=next(t for t in old['tables']['tasks'] if t['task_id']==task['task_id'])
        task['_assignment_epoch']=previous.get('_assignment_epoch',0)+1
        i.setdefault('responsibility_events',[]).append({'action':'assign_task','at':command['at'],'actor':command['actor'],'cycle':i['cycle'],'task_id':task['task_id'],'from_owner':previous['owner'],'to_owner':task['owner'],'reason':command['reason']})
    else:
        link=new['flow']['handover_links'][-1]
        link['cycle']=i['cycle'];link['assignment_epoch']=task.get('_assignment_epoch',0)
    return new

def make_today(user,ws,now,queue='mine',kind='',owner='',due_before='',page=1,page_size=30):
    if queue not in {'mine','closure','handover','review','overdue','all'}:fail(422,'未知待办队列')
    if kind and kind not in policy.KINDS[user['role']]:fail(422,'未知或无权查看的事项类型')
    if due_before:
        try:datetime.strptime(due_before,'%Y-%m-%d')
        except ValueError:fail(422,'期限筛选须为YYYY-MM-DD')
    items=[];counts={k:0 for k in ['mine','closure','handover','review','overdue','all']}
    for i in ws['flow']['instances']:
        if not policy.flow_visible(user,ws,i) or i['status']=='closed':continue
        detail=policy.detail(user,ws,i)
        tasks=detail['tasks']
        for t in tasks:
            waived=any(v['instance_id']==i['id'] and v.get('task_id')==t['task_id'] and v['cycle']==i['cycle'] and v['decision']=='waive' for v in ws['flow']['reviews'])
            if t['status']=='已关闭' or waived:continue
            due=t.get('due_at','');overdue=bool(due and due<now)
            queues=['all'];need_review=t['status']=='待复核' and policy.can_review(user,i) and t.get('completed_by')!=actor(user)
            if t.get('owner','')==actor(user) and t['status']!='待复核' and i['status']=='active' and user['role']!='viewer':queues.append('mine')
            if need_review:queues.append('review')
            if overdue:queues.append('overdue')
            items.append({'key':'task:'+t['task_id'],'item_type':'task','label':'任务复核' if need_review else '执行任务','flow_id':i['id'],'kind':i['kind'],'title':t.get('title') or '已授权任务','entity_id':t['task_id'],'owner':t.get('owner',''),'due_at':due,'status':t['status'],'overdue':overdue,'queues':queues})
        for h in detail['handovers']:
            if h.get('receiver')==actor(user) and h['status']=='待接收' and user['role']!='viewer':
                items.append({'key':'handover:'+h['handover_id'],'item_type':'handover','label':'交接待接收','flow_id':i['id'],'kind':i['kind'],'title':detail['title'],'entity_id':h['handover_id'],'owner':actor(user),'due_at':h.get('due_at',''),'status':h['status'],'overdue':False,'queues':['handover','all']})
        for h in detail['takeover_options']:
            items.append({'key':'takeover:'+h['handover_id'],'item_type':'takeover','label':'已接收，待确认接续责任','flow_id':i['id'],'kind':i['kind'],'title':detail['title'],'entity_id':h['task_id'],'owner':actor(user),'due_at':next(t.get('due_at','') for t in tasks if t['task_id']==h['task_id']),'status':'待确认接续','overdue':False,'queues':['mine','all']})
        # B1 only: public/simple maintenance becomes visible when all required tasks are independently closed.
        # Loading this projection never submits the workflow. The responsible owner must explicitly submit.
        if i['kind']=='maintenance' and i['status']=='active' and detail['can_submit']:
            mine_close=i.get('owner')==actor(user) and user['role']!='viewer'
            queues=['all']+(['mine','closure'] if mine_close else [])
            items.append({'key':'closure:'+i['id'],'item_type':'closure','label':'待提交最终复核' if mine_close else '待负责人提交最终复核','flow_id':i['id'],'flow_revision':i['revision'],'kind':i['kind'],'title':detail['title'],'entity_id':i['id'],'owner':i.get('owner',''),'due_at':'','status':'待收口','overdue':False,'queues':queues})
        if detail['can_review']:
            items.append({'key':'review:'+i['id'],'item_type':'workflow','label':'最终复核','flow_id':i['id'],'kind':i['kind'],'title':detail['title'],'entity_id':i['id'],'owner':'','due_at':'','status':'待最终复核','overdue':False,'queues':['review','all']})
    # Only counts/rows in authorised scope AND current filters; counts are not institutional totals.
    items=[x for x in items if (not kind or x['kind']==kind) and (not owner or x['owner']=='account:'+owner) and (not due_before or x['due_at'] and x['due_at'][:10]<=due_before)]
    for x in items:
        for q in x['queues']:counts[q]+=1
    selected=[x for x in items if queue in x['queues']]
    selected.sort(key=lambda x:(not x['overdue'],x['due_at'] or '9999',x['key']))
    size=max(1,min(page_size,100));page=max(1,page);start=(page-1)*size
    return {'items':selected[start:start+size],'counts':counts,'total':len(selected),'page':page,'page_size':size,'as_of':now,'queue':queue,'scope_note':'仅当前账户获准查看、已登记的活跃流程事项；不包含全部历史独立台账，不代表全机构工作量。各队列可能重叠。'}

def resident_profile(user,ws,rid,timezone,page=1,page_size=40):
    original=next((r for r in ws['tables']['residents'] if r['resident_id']==rid and policy.resident_visible(user,r)),None)
    if not original:fail(404,'长者不存在或不在授权范围')
    resident=policy.fields(user,'residents',original)
    related={};events=[]
    dates={'stays':'start_date','assessments':'assessed_at','plans':'start_date','tasks':'created_at','incidents':'occurred_at','handovers':'created_at','followups':'created_at','workorders':'created_at','findings':'created_at','corrections':'created_at'}
    names={'stays':'在院状态','assessments':'评估登记','plans':'照护计划','tasks':'任务','incidents':'事件登记','handovers':'交接','followups':'后续跟进','workorders':'维修','findings':'检查发现','corrections':'整改'}
    visible_flows=[i for i in ws['flow']['instances'] if i.get('resident_id')==rid and policy.flow_visible(user,ws,i)]
    event_ids={e['event_id'] for e in policy.rows_for(user,ws,'incidents') if e.get('resident_id')==rid}
    for table,timekey in dates.items():
        rows=[r for r in policy.rows_for(user,ws,table) if r.get('resident_id')==rid or table=='followups' and r.get('event_id') in event_ids]
        related[table]={'count':len(rows),'items':rows[:50],'truncated':len(rows)>50}
        for row in rows:
            eid=row[policy.IDS[table]]
            flow=next((i for i in visible_flows if i['subject_type']==table and i['subject_id']==eid or table=='tasks' and any(s['task_id']==eid for s in i['steps']) or table=='handovers' and any(h['instance_id']==i['id'] and h['handover_id']==eid for h in ws['flow']['handover_links'])),None)
            events.append({'key':table+':'+eid,'table':table,'entity_id':eid,'flow_id':flow['id'] if flow else '', 'at':row.get(timekey,''),'label':names[table], 'title':row.get('title') or row.get('tool_name') or row.get('goal') or row.get('category') or names[table], 'status':row.get('status','')})
    if user['role']!='viewer':
        for i in visible_flows:
            for r in ws['flow']['reviews']:
                if r['instance_id']==i['id']:
                    events.append({'key':'review:'+r['id'],'table':'','entity_id':r['id'],'flow_id':i['id'],'at':r['at'],'label':'复核记录','title':{'approve':'复核通过','reject':'复核退回','waive':'明确豁免'}.get(r['decision'],'复核'),'status':'','actor':r['actor'],'reason':r.get('reason','')})
            for n,e in enumerate(i.get('responsibility_events',[])):
                events.append({'key':'responsibility:'+i['id']+':'+str(n),'table':'','entity_id':e.get('task_id',i['id']),'flow_id':i['id'],'at':e['at'],'label':'责任与授权记录','title':{'takeover_task':'确认接续责任','share_repair':'发布后勤必要信息','revoke_repair':'撤回后勤信息','assign_task':'重新分派任务'}.get(e['action'],'责任变更'),'actor':e['actor'],'status':'','reason':e['reason']})
    # Original timestamps remain labelled; zone is institution's, never browser's.
    def order(e):
        try:
            dt=datetime.fromisoformat(e['at'].replace('Z','+00:00'))
            if not dt.tzinfo:dt=dt.replace(tzinfo=ZoneInfo(timezone))
            return dt.timestamp(),e['key']
        except (ValueError,TypeError):return float('-inf'),e['key']
    events.sort(key=order,reverse=True);size=max(1,min(page_size,100));page=max(1,page)
    return {'resident':resident,'related':related,'timeline':events[(page-1)*size:page*size],'timeline_total':len(events),'page':page,'page_size':size,'timezone':timezone,'flows':[policy.summary(user,ws,i) for i in visible_flows],'note':'来源登记与复核时间线，不是临床判断；原始时间缺失/错误排在末尾，不补造。'}

def install(app,db,get_ws,touch):
    @app.get('/api/today')
    def today(request:Request,queue:str='mine',kind:str='',owner_user_id:str='',due_before:str='',page:int=1,page_size:int=30):
        with db.write() as c:
            u,t=authenticate(c,request);w=get_ws(c,u)
            f=c.execute(select(facilities).where(facilities.c.id==u['facility_id'])).mappings().one()
            now=datetime.now(ZoneInfo(f['timezone'])).strftime('%Y-%m-%d %H:%M:%S')
            r=make_today(u,w['payload'],now,queue,kind,owner_user_id,due_before,page,page_size);touch(c,t)
            return {**r,'revision':w['revision'],'timezone':f['timezone']}
    @app.get('/api/residents/{rid}/profile')
    def profile(rid:str,request:Request,page:int=1,page_size:int=40):
        with db.write() as c:
            u,t=authenticate(c,request);w=get_ws(c,u)
            f=c.execute(select(facilities).where(facilities.c.id==u['facility_id'])).mappings().one()
            r=resident_profile(u,w['payload'],rid,f['timezone'],page,page_size);touch(c,t)
            return {**r,'revision':w['revision']}
