"""Apply scope and field allow-lists BEFORE sending data to the browser."""
from copy import deepcopy
from uuid import UUID
from .resident_profile import NEW_FIELDS, PRIVATE_FIELDS
from .security import REVIEWERS, MAINT_ROLES, actor, fail

KINDS = {"admin":{"incident","maintenance","rectification"},
         "supervisor":{"incident","maintenance","rectification"},
         "caregiver":{"incident","maintenance"}, "maintenance":{"maintenance"},
         "viewer":{"incident","maintenance","rectification"},
         "maint_executor":{"maintenance"}, "maint_supervisor":{"maintenance"}}
REVIEW_COMMANDS={"review_task","review_workflow","waive_task","reopen_workflow","assign_task"}
EXEC_COMMANDS={"start_task","submit_task","add_evidence","set_task_exception","create_handover"}
FIELDS={
 "residents": "facility_id resident_id display_name unit bed what_matters support note".split() + list(NEW_FIELDS),
 "stays":"facility_id stay_id resident_id start_date end_date status unit source_ref".split(),
 "assessments":"facility_id assessment_id resident_id assessed_at tool_name tool_version score conclusion assessor review_due source_ref note".split(),
 "plans":"facility_id plan_id resident_id assessment_id start_date end_date goal measure owner status approver review_due note".split(),
 "tasks":"facility_id task_id resident_id created_at source_type source_id title dept owner due_at status accepted_at accepted_by performed_at performed_source completed_at completed_by result reviewer reviewed_at".split(),
 "incidents":"facility_id event_id resident_id occurred_at reported_at kind category location description harm immediate_action reporter owner followup_due status reviewer reviewed_at".split(),
 "handovers":"facility_id handover_id resident_id created_at shift sender receiver observation request due_at status accepted_at outcome verified_by verified_at".split(),
 "followups":"facility_id followup_id event_id created_at author note next_action due_at".split(),
 "workorders":"facility_id workorder_id resident_id created_at location equipment_id title description priority owner due_at status".split(),
 "findings":"facility_id finding_id resident_id created_at inspection_ref requirement_ref location description priority status".split(),
 "corrections":"facility_id correction_id finding_id resident_id created_at title owner due_at status".split()
}
IDS={"residents":"resident_id","stays":"stay_id","assessments":"assessment_id","plans":"plan_id","tasks":"task_id","incidents":"event_id","handovers":"handover_id","followups":"followup_id","workorders":"workorder_id","findings":"finding_id","corrections":"correction_id"}
FLOW_KEYS="id facility_id resident_id title kind subject_type subject_id finding_id template_id template_version status started_at last_action_at closed_at owner revision cycle cycle_started_at".split()


def repair_shared(user,flow):
    a=flow.get("repair_access",{})
    return (user["role"]=="maintenance" and flow.get("kind")=="maintenance"
            and flow.get("facility_id")==user["facility_id"] and bool(flow.get("resident_id"))
            and a.get("user_id")==user["id"] and a.get("cycle")==flow.get("cycle")
            and isinstance(a.get("brief"),dict) and bool(a["brief"].get("fault")))

def safe_account(value):
    """Only authenticated identifier syntax crosses the minimal repair projection.
    Old free-text personnel fields may themselves contain private notes.
    """
    if not value:return ""
    if isinstance(value,str) and value.startswith("account:"):
        try:
            return "account:"+str(UUID(value[8:]))
        except ValueError:pass
    return "历史人员未绑定账户"

def repair_row(user,flow,table,row):
    """Whitelist projection from explicitly released repair brief, NOT raw notes.
    No resident ID, original title, health-related description, third-party result
    or arbitrary evidence crosses this boundary. Plaintext in the brief itself is
    human-reviewed; this is not automated anonymisation.
    """
    b=flow["repair_access"]["brief"]
    if table=="tasks":
        keys="facility_id task_id created_at dept owner due_at status accepted_at accepted_by performed_at performed_source completed_at completed_by reviewer reviewed_at".split()
        out={k:row.get(k,"") for k in keys}
        for k in ("owner","accepted_by","completed_by","reviewer"):out[k]=safe_account(out.get(k))
        out.update(title=b["equipment"]+"："+b["fault"],result=row.get("result","") if row.get("completed_by")==actor(user) else "")
        return out
    if table=="workorders":
        out={k:row.get(k,"") for k in "facility_id workorder_id created_at priority owner due_at status".split()}
        out["owner"]=safe_account(out.get("owner"))
        out.update(location=b["location"],title=b["equipment"],description=b["fault"]+"；入室安排："+b["access_window"]+"；联系岗位："+b["contact_role"])
        return out
    return {}

def takeover_target(user,ws,i,handover_id):
    """Validate explicit continuation; acknowledgment itself never transfers ownership."""
    if i["status"]!="active":fail(409,"流程不在办理中，不能接续责任")
    link=next((h for h in ws["flow"]["handover_links"] if h["instance_id"]==i["id"] and h["handover_id"]==handover_id),None)
    h=next((h for h in ws["tables"]["handovers"] if h["handover_id"]==handover_id and h["facility_id"]==user["facility_id"]),None)
    if not link or not h:fail(404,"交接不存在")
    if h["receiver"]!=actor(user):fail(403,"只有指定接班账户可以确认接续")
    if h["status"]!="已接收":fail(409,"须先确认接收信息，再单独确认接续责任")
    t=next(t for t in ws["tables"]["tasks"] if t["task_id"]==link["task_id"] and t["facility_id"]==user["facility_id"])
    if t["status"] not in {"待处理","执行中","暂缓"}:fail(409,"只有尚待执行的任务可以接续，复核或终结状态不可接续")
    if link.get("cycle")!=i["cycle"] or link.get("assignment_epoch")!=t.get("_assignment_epoch",0) or h["sender"]!=t["owner"]:
        fail(409,"交接对应的轮次或负责人已变化；请重新交接或由主管分派")
    if t["owner"]==actor(user):fail(409,"你已是当前负责人，无需再次接续")
    return t,h

def completion_blockers(ws,i):
    out=[]
    for step in i["steps"]:
        t=next(t for t in ws["tables"]["tasks"] if t["task_id"]==step["task_id"] and t["facility_id"]==i["facility_id"])
        waived=any(r["instance_id"]==i["id"] and r.get("task_id")==t["task_id"] and r["cycle"]==i["cycle"] and r["decision"]=="waive" for r in ws["flow"]["reviews"])
        if step["required"] and t["status"]!="已关闭" and not waived:
            out.append({"task_id":t["task_id"],"message":t["task_id"]+"：必要任务尚未复核关闭或明确豁免"})
    if i["kind"]=="incident" and not any(x["instance_id"]==i["id"] and x["cycle"]==i["cycle"] for x in ws["flow"]["followup_links"]):
        out.append({"task_id":"","message":"本轮尚缺后续评价；请完成对应跟进任务后再提交"})
    return out

def in_unit(user,unit):return "*" in user["units"] or unit in user["units"]
def resident_visible(user,row):
    return row.get("facility_id")==user["facility_id"] and user["role"] not in MAINT_ROLES|{"maintenance"} and in_unit(user,row.get("unit",""))

def visible_residents(user,ws):return {r["resident_id"] for r in ws["tables"]["residents"] if resident_visible(user,r)}

def flow_visible(user,ws,flow):
    if flow.get("facility_id")!=user["facility_id"] or flow["kind"] not in KINDS[user["role"]]:return False
    if user["role"] in MAINT_ROLES:
        if flow.get("resident_id") or flow["kind"] != "maintenance":return False
        subject=next((r for r in ws["tables"]["workorders"] if r["workorder_id"]==flow.get("subject_id") and r["facility_id"]==user["facility_id"]),None)
        location=subject.get("location", "") if subject else flow.get("location", "")
        return bool(location) and location in user["units"] and "*" not in user["units"]
    rid=flow.get("resident_id","")
    if rid:
        if user["role"]=="maintenance":return repair_shared(user,flow)
        return rid in visible_residents(user,ws)
    # Public incident data is restricted; public maintenance can be assigned without a resident.
    if user["role"]=="maintenance":return flow["kind"]=="maintenance"
    if user["role"]=="caregiver":return flow["kind"]=="maintenance"
    return user["role"] in REVIEWERS or user["role"]=="viewer"

def get_flow(user,ws,id):
    flow=next((i for i in ws["flow"]["instances"] if i["id"]==id and flow_visible(user,ws,i)),None)
    if not flow:fail(404,"记录不存在或不在授权范围内")
    return flow

def field_keys(user,table):
    allowed=FIELDS[table]
    if table=="residents" and user["role"] not in {"admin","supervisor"}:
        allowed=[k for k in allowed if k not in PRIVATE_FIELDS]
        if user["role"] in MAINT_ROLES|{"maintenance","viewer"}: allowed=[k for k in allowed if k not in NEW_FIELDS]
    if user["role"]=="viewer":
        allowed=[k for k in allowed if k in {"facility_id","resident_id",IDS[table],"display_name","unit","bed","status","created_at","due_at","start_date","end_date"}]
    return allowed

def fields(user,table,row):
    return {k:deepcopy(row.get(k,"")) for k in field_keys(user,table)}

def nursing_assessment(ws,row):
    """仅双向可核验的新护理评估适用专业正文隔离，不改旧评估语义。"""
    if not isinstance(row.get('_meta'),dict) or row['_meta'].get('nursing_admission') is not True:return False
    demo=ws.get('department_demo',{})
    if not isinstance(demo,dict):return False
    state=demo.get('nursing',{})
    if not isinstance(state,dict) or state.get('version')!=1:return False
    links=state.get('assessment_links',[])
    if not isinstance(links,list):return False
    return any(isinstance(link,dict) and link.get('assessment_id')==row.get('assessment_id')
               and link.get('resident_id')==row.get('resident_id') for link in links)

def rows_for(user,ws,table):
    if table not in FIELDS:fail(404,"未知记录类型")
    visible=visible_residents(user,ws)
    flows=[i for i in ws["flow"]["instances"] if flow_visible(user,ws,i)]
    subjects={(i["subject_type"],i["subject_id"]) for i in flows}
    task_ids={s["task_id"] for i in flows for s in i["steps"]}
    event_ids={r["event_id"] for r in ws["tables"]["incidents"] if r.get("resident_id") in visible}
    out=[]
    for row in ws["tables"].get(table,[]):
        if row.get("facility_id")!=user["facility_id"]:continue
        if user["role"] in MAINT_ROLES|{"maintenance"}:
            permitted=(table=="tasks" and row["task_id"] in task_ids) or (table, row.get(IDS[table])) in subjects
        elif table=="residents":permitted=row["resident_id"] in visible
        elif table=="followups":permitted=row["event_id"] in event_ids
        elif row.get("resident_id"):permitted=row["resident_id"] in visible
        else:permitted=user["role"] in REVIEWERS or (table=="tasks" and row["task_id"] in task_ids) or (table,row.get(IDS[table])) in subjects
        # Caregiver does not receive rectification or clinical assessments/plans administrative history outside care scope.
        if user["role"]=="caregiver" and table in {"findings","corrections"}:permitted=False
        if user["role"]=="caregiver" and table=="assessments" and nursing_assessment(ws,row):permitted=False
        if permitted:
            private_flow=next((f for f in flows if repair_shared(user,f) and ((table=="tasks" and row.get("task_id") in {x["task_id"] for x in f["steps"]}) or (table==f["subject_type"] and row.get(IDS[table])==f["subject_id"]))),None)
            out.append(repair_row(user,private_flow,table,row) if private_flow else fields(user,table,row))
    return out

def repair_display_state(ws, flow):
    """Only a user-facing 4-state projection. Never rewrite underlying workflow state.

    Waiting/return are annotations inside 处理中, the two historical review gates
    are both 待验收, and final closed flows are 终结 events (not necessarily repaired).
    """
    if flow["status"] == "closed":
        return "done"
    if flow["status"] == "pending_review":
        return "inspect"
    ids = {step["task_id"] for step in flow["steps"]}
    tasks = [r for r in ws["tables"]["tasks"] if r["task_id"] in ids]
    if any(t["status"] in {"待复核", "已关闭"} for t in tasks):
        return "inspect"
    if any(t["status"] in {"执行中", "暂缓", "拒绝", "不适用", "已取消"} or t.get("accepted_at") for t in tasks):
        return "doing"
    return "todo"


def repair_completion_note(ws, flow):
    """A closed administratively-waived repair is not proof of physical repair."""
    if flow["status"] != "closed":
        return ""
    ids = {step["task_id"] for step in flow["steps"]}
    tasks = [r for r in ws["tables"]["tasks"] if r["task_id"] in ids]
    if tasks and all(t["status"] in {"已取消", "不适用"} for t in tasks):
        return "免做／取消结案；非实际维修完成"
    if any(t["status"] in {"已取消", "不适用"} for t in tasks):
        return "存在经复核批准的免做项目，查看历史"
    return ""


def summary(user,ws,i):
    keys=FLOW_KEYS if user["role"]!="viewer" else [k for k in FLOW_KEYS if k not in {"title","owner"}]
    item={k:i.get(k,"") for k in keys}
    if user["role"]=="viewer":item["title"]={"incident":"事件协作","maintenance":"设施维修","rectification":"检查整改"}[i["kind"]]
    if repair_shared(user,i):
        b=i["repair_access"]["brief"]
        item={k:i.get(k,"") for k in FLOW_KEYS if k not in {"resident_id","title","finding_id"}}
        item["owner"]=safe_account(item.get("owner"))
        item.update(title="房间维修 · "+b["equipment"],minimal_repair=True)
    ts=[t for t in ws["tables"]["tasks"] if t["task_id"] in {s["task_id"] for s in i["steps"]}]
    item["task_count"]=len(ts);item["closed_count"]=sum(t["status"]=="已关闭" for t in ts)
    if i["kind"] == "maintenance":
        item["repair_display_stage"] = repair_display_state(ws, i)
        item["repair_completion_note"] = repair_completion_note(ws, i)
        item["repair_due_at"] = ts[0].get("due_at", "") if ts else ""
    return item

def detail(user,ws,i):
    out=summary(user,ws,i)
    task_ids={s["task_id"] for s in i["steps"]}
    out["steps"]=deepcopy(i["steps"])
    out["tasks"]=[fields(user,"tasks",t) for t in ws["tables"]["tasks"] if t["task_id"] in task_ids]
    out["subject"]=next((r for r in rows_for(user,ws,i["subject_type"]) if r.get(IDS[i["subject_type"]])==i["subject_id"]),{})
    out["handover_links"]=[dict(h) for h in ws["flow"]["handover_links"] if h["instance_id"]==i["id"]]
    hids={h["handover_id"] for h in out["handover_links"]}
    out["handovers"]=[fields(user,"handovers",h) for h in ws["tables"]["handovers"] if h["handover_id"] in hids]
    out["evidence"]=[{k:e.get(k,"") for k in "id task_id cycle kind value actor at storage".split()} for e in ws["flow"]["evidence"] if e["instance_id"]==i["id"]]
    out["reviews"]=[{k:r.get(k,"") for k in "id task_id cycle scope decision actor at reason".split()} for r in ws["flow"]["reviews"] if r["instance_id"]==i["id"]]
    if user["role"]=="viewer":
        out["evidence"]=[];out["reviews"]=[];out["handovers"]=[];out["handover_links"]=[];out["steps"]=[{k:s[k] for k in ("key","task_id","required") if k in s} for s in out["steps"]]
    out["template_note"]=i["template_snapshot"].get("sop_ref","")
    out["completion_blockers"]=completion_blockers(ws,i) if user["role"]!="viewer" else []
    out["can_submit"]=user["role"]!="viewer" and i["status"]=="active" and not out["completion_blockers"] and (i["kind"]!="maintenance" or bool(i.get("resident_id")) or i["owner"]==actor(user))
    out["can_review"]=can_review(user,i) and i["status"]=="pending_review" and i.get("submitted_by")!=actor(user)
    out["takeover_options"]=[]
    for h in out["handovers"]:
        if h.get("receiver")==actor(user) and h["status"]=="已接收":
            try:
                t,_=takeover_target(user,ws,i,h["handover_id"])
                out["takeover_options"].append({"handover_id":h["handover_id"],"task_id":t["task_id"]})
            except Exception as e:
                from fastapi import HTTPException
                if not isinstance(e,HTTPException):raise
    out["responsibility_events"]=deepcopy(i.get("responsibility_events",[])) if user["role"]!="viewer" else []
    if user["role"] in REVIEWERS or user["role"]=="caregiver":
        out["repair_access"]=deepcopy(i.get("repair_access"))
    if repair_shared(user,i):
        brief=deepcopy(i["repair_access"]["brief"])
        out["maintenance_brief"]=brief
        out["steps"]=[{k:s[k] for k in ("key","task_id","required") if k in s} for s in i["steps"]]
        out["tasks"]=[repair_row(user,i,"tasks",t) for t in ws["tables"]["tasks"] if t["task_id"] in task_ids]
        out["subject"]=repair_row(user,i,"workorders",next(t for t in ws["tables"]["workorders"] if t["workorder_id"]==i["subject_id"]))
        out["handovers"]=[{**{k:h.get(k,"") for k in "handover_id created_at sender receiver due_at status accepted_at".split()},"observation":brief["fault"],"request":"入室安排："+brief["access_window"]+"；联系岗位："+brief["contact_role"]} for h in ws["tables"]["handovers"] if h["handover_id"] in hids and h["receiver"]==actor(user)]
        allowed_hids={h["handover_id"] for h in out["handovers"]}
        out["handover_links"]=[{k:h[k] for k in ("handover_id","task_id")} for h in ws["flow"]["handover_links"] if h["handover_id"] in allowed_hids]
        out["evidence"]=[e for e in out["evidence"] if e["actor"]==actor(user)]
        out["reviews"]=[{k:safe_account(r[k]) if k=="actor" else r[k] for k in r if k!="reason"} for r in out["reviews"]]
        for h in out["handovers"]:h["sender"]=safe_account(h.get("sender"));h["receiver"]=safe_account(h.get("receiver"))
        out["responsibility_events"]=[]
        out["template_note"]="后勤最小维修视图：仅接收明确发布的维修信息；不开放长者档案或原始照护记录。"
    out["stage"]=maintenance_stage(out) if i["kind"]=="maintenance" else i["status"]
    out["allowed_actions"]=allowed_actions(user,ws,i,out)
    return out

def authorize_command(user,ws,command):
    typ=command["type"]
    if user["role"]=="viewer":fail(403,"只读账户不能修改记录")
    if typ=="publish_template":
        if user["role"]!="admin":fail(403,"只有机构管理员可以发布模板版本")
        return
    if typ=="start":
        template=next((t for t in ws["flow"]["templates"] if t["id"]==command.get("template_id") and t["version"]==command.get("template_version")),None)
        if not template:fail(422,"请选择有效模板")
        fake={"facility_id":user["facility_id"],"kind":template["kind"],"resident_id":command.get("resident_id",""),"location":command.get("location", "")}
        if template["kind"]=="incident" and not fake["resident_id"]:fail(422,"事件协作必须关联已授权长者")
        if not flow_visible(user,ws,fake):fail(403,"无权在此范围创建该流程")
        if command.get("existing_subject_id"):
            rows=rows_for(user,ws,{"incident":"incidents","maintenance":"workorders","rectification":"corrections"}[template["kind"]])
            if not any(r[IDS[{"incident":"incidents","maintenance":"workorders","rectification":"corrections"}[template["kind"]]]]==command["existing_subject_id"] for r in rows):fail(404,"原记录不存在或不在授权范围")
        return
    i=get_flow(user,ws,command.get("instance_id",""))
    if typ=="submit_workflow" and i["kind"]=="maintenance" and not i.get("resident_id") and i["owner"]!=actor(user):
        fail(403,"只有整单负责人可提交维修验收")
    if user["role"] in MAINT_ROLES and typ not in {"start_task","submit_task","add_evidence","set_task_exception","submit_workflow","assign_task","review_task","review_workflow","waive_task","reopen_workflow"}:
        fail(403,"首批维修岗位未开放此操作")
    if repair_shared(user,i) and typ not in {"start_task","submit_task","add_evidence","set_task_exception","ack_handover","takeover_task","submit_workflow"}:
        fail(403,"后勤最小维修范围未开放此操作")
    if typ in {"share_repair","revoke_repair"} and user["role"] not in REVIEWERS|{"caregiver"}:fail(403,"只有可见完整维修的照护人员或主管可以发布或撤回维修信息")
    if typ in REVIEW_COMMANDS and not can_review(user,i):fail(403,"此操作需要主管复核权限")
    if typ in EXEC_COMMANDS:
        task=next((t for t in ws["tables"]["tasks"] if t["task_id"]==command.get("task_id") and any(s["task_id"]==t["task_id"] for s in i["steps"])),None)
        if not task:fail(404,"任务不属于当前流程")
        if task.get("owner","")!=actor(user):
            fail(403,"任务已指派其他账户；不能冒充执行者")
    # Acknowledgment is checked again by the engine against immutable account IDs.


def can_review(user,flow):
    return user["role"] in REVIEWERS or (user["role"]=="maint_supervisor" and flow["kind"]=="maintenance" and not flow.get("resident_id"))

def maintenance_stage(detail):
    if detail["status"]=="closed":return "已验收"
    if detail["status"]=="pending_review":return "待整单验收"
    statuses={t["status"] for t in detail["tasks"]}
    if detail["completion_blockers"]==[]:return "待提交整单验收"
    if "待复核" in statuses:return "修理结果待检查"
    if "暂缓" in statuses:return "暂停维修"
    if "执行中" in statuses:return "维修中"
    return "待接单"

def allowed_actions(user,ws,i,detail):
    # 状态和主体一致生成可用动作；命令端仍独立强制校验。
    if user["role"]=="viewer":return []
    actions=set()
    if detail["can_submit"]:actions.add("submit_workflow")
    if detail["can_review"]:actions.add("review_workflow")
    review=can_review(user,i)
    if i["status"]=="closed":
        if review:actions.add("reopen_workflow")
        return sorted(actions)
    if i["status"]!="active":return sorted(actions)
    waived={r.get("task_id") for r in ws["flow"]["reviews"] if r["instance_id"]==i["id"] and r["cycle"]==i["cycle"] and r["decision"]=="waive"}
    for t in detail["tasks"]:
        status=t["status"]
        if t["owner"]==actor(user):
            if status in {"待处理","暂缓","拒绝","不适用","已取消"} and t["task_id"] not in waived:actions.add("start_task")
            if status=="执行中":actions.add("submit_task")
            if status in {"待处理","执行中"}:actions.add("set_task_exception")
            if status in {"待处理","执行中","待复核"}:actions.add("add_evidence")
        if review:
            if status not in {"待复核","已关闭"}:actions.add("assign_task")
            if status=="待复核" and t.get("completed_by")!=actor(user):actions.add("review_task")
            if status in {"不适用","已取消"} and t["task_id"] not in waived and i["kind"]!="incident":actions.add("waive_task")
    return sorted(actions)
