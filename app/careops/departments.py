"""部门级 Demo：同一演示管理员、共享资料、可追溯业务闭环。"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from uuid import uuid4
import math
import re

DEPARTMENTS = [
    {"id": "logistics", "label": "后勤部", "modules": ["maintenance", "cleaning", "menus", "purchases", "meals"]},
    {"id": "activities", "label": "活动部", "modules": ["activities"]},
    {"id": "sales", "label": "销售部", "modules": ["leads"]},
    {"id": "care", "label": "护理体系", "modules": ["care", "nursing", "concierge"]},
    {"id": "rehab", "label": "康复科", "modules": ["rehab"]},
    {"id": "medical", "label": "医务室", "modules": ["medical"]},
]
STAFF = [{"id": k, "label": v} for k, v in [
    ("logistics", "后勤值班岗"), ("cleaning", "保洁值班岗"), ("kitchen", "膳食值班岗"),
    ("activities", "活动值班岗"), ("sales", "销售接待岗"), ("care", "护理员值班岗"),
    ("nursing", "护士站值班岗"), ("concierge", "管家值班岗"), ("rehab", "康复值班岗"), ("medical", "医务值班岗")]]


def field(key, label, type="text", required=False, options=None, source=None):
    return {"key": key, "label": label, "type": type, "required": required,
            "options": [{"value": x, "label": x} for x in (options or [])], "source": source}


def action(key, label, before, after, fields=()):
    return {"key": key, "label": label, "from": before, "to": after, "fields": list(fields)}


def schema(label, department, initial, fields, actions):
    return {"label": label, "department": department, "initial": initial,
            "fields": [field("title", "事项名称", required=True), field("owner", "业务责任岗", "select", True, source="staff")] + fields,
            "columns": ["title", "status", "owner", "updated_at"], "actions": actions}


R = lambda: field("resident_id", "长者", "select", True, source="residents")
D = lambda: field("date", "日期", "date", True)
NOTE = field("note", "备注", "textarea")
SCHEMAS = {
 "cleaning": schema("保洁", "logistics", "pending", [field("area", "清扫区域", required=True), D(), field("frequency", "清扫频率", "select", options=["临时", "每日", "每周"]), field("reason", "临时清扫原因"), field("result", "清扫结果", "textarea"), NOTE], [action("start", "开始清扫", ["pending", "exception"], "in_progress"), action("complete", "完成清扫", ["in_progress"], "completed", ["result"]), action("exception", "登记异常", ["pending", "in_progress"], "exception", ["reason"])]),
 "menus": schema("周食谱", "logistics", "draft", [field("week_start", "周起始日", "date", True), field("menu", "周食谱内容", "textarea", True), field("change_reason", "版本说明", "textarea"), field("replaces_id", "替代食谱版本", "select", source="published_menus"), NOTE], [action("publish", "发布食谱", ["draft"], "published"), action("supersede", "停止使用此版本", ["published"], "superseded", ["change_reason"])]),
 "purchases": schema("采购", "logistics", "recorded", [D(), field("item", "采购品项", required=True), field("quantity", "数量", "number", True), field("unit", "计量单位", required=True), field("amount", "金额", "number"), field("source", "采购来源", required=True), field("menu_id", "已发布食谱", "select", source="published_menus"), NOTE], []),
 "meals": schema("供餐", "logistics", "planned", [D(), field("meal", "餐次", "select", True, ["早餐", "午餐", "晚餐", "加餐"]), field("menu_id", "已发布食谱", "select", required=True, source="published_menus"), field("result", "制作／供餐记录", "textarea"), field("change_reason", "变更原因", "textarea"), NOTE], [action("prepare", "完成制作", ["planned"], "prepared", ["result"]), action("serve", "完成供餐", ["prepared"], "served", ["result"])]),
 "activities": schema("活动", "activities", "planned", [field("scheduled_at", "活动时间", "datetime-local", True), field("location", "场地", required=True), field("participants", "参与长者", "multi", source="residents"), field("attendance", "实际签到", "multi", source="residents"), field("summary", "活动总结", "textarea"), NOTE], [action("start", "开始活动", ["planned"], "running"), action("complete", "结束并归档", ["running"], "completed", ["summary"])]),
 "leads": schema("咨询与入住", "sales", "new", [field("contact", "咨询称呼", required=True), field("visit_at", "来访预约", "datetime-local"), field("followup", "跟进记录", "textarea"), field("fees", "已说明费用", "textarea"), field("bed_id", "预留床位", "select", source="beds"), field("admission_date", "入住日期", "date"), field("unit", "照料区"), NOTE], [action("visit", "登记来访", ["new"], "visit", ["visit_at"]), action("intent", "确认意向", ["visit"], "intent", ["fees"]), action("reserve", "预留床位", ["intent"], "reserved", ["bed_id"]), action("followup", "追加跟进", ["new", "visit", "intent", "reserved"], None, ["followup"])]),
 "care": schema("护理员今日照护", "care", "pending", [R(), D(), field("shift", "班次", "select", True, ["早班", "中班", "晚班", "夜班"]), field("measure", "已确认照护措施", "textarea", True), field("result", "执行结果", "textarea"), field("reason", "异常观察", "textarea"), NOTE], [action("complete", "完成照护", ["pending"], "completed", ["result"]), action("exception", "登记异常", ["pending"], "exception", ["reason"])]),
 "nursing": schema("护士站观察与交班", "care", "pending", [R(), D(), field("observation", "观察事实", "textarea", True), field("confirmed_plan", "专业人员已确认安排", "textarea"), field("handover", "交班内容", "textarea"), field("result", "处理结果", "textarea"), field("reason", "异常说明", "textarea"), NOTE], [action("complete", "完成并交班", ["pending"], "completed", ["result", "handover"]), action("exception", "登记异常", ["pending"], "exception", ["reason"])]),
 "concierge": schema("管家生活服务", "care", "pending", [R(), D(), field("request", "住户诉求", "textarea", True), field("contact", "联络记录", "textarea"), field("result", "服务结果", "textarea"), NOTE], [action("start", "开始办理", ["pending"], "in_progress"), action("complete", "办理完成", ["in_progress"], "completed", ["result"])]),
 "rehab": schema("康复计划与训练", "rehab", "scheduled", [R(), field("scheduled_at", "预约时间", "datetime-local", True), field("plan", "专业人员已确认康复计划", "textarea", True), field("training", "训练记录", "textarea"), field("reason", "未完成原因", "textarea"), field("review", "阶段复评记录／报告引用", "textarea"), NOTE], [action("complete", "完成训练", ["scheduled"], "completed", ["training"]), action("miss", "登记未完成", ["scheduled"], "missed", ["reason"]), action("review", "登记复评", ["completed", "missed"], "reviewed", ["review"])]),
 "medical": schema("医务处置与随访", "medical", "pending", [R(), field("source", "事项来源", required=True), field("handling", "已确认专业处置记录", "textarea"), field("followup", "随访记录", "textarea"), field("referral", "外院就诊／转诊记录", "textarea"), field("result", "结束说明", "textarea"), NOTE], [action("handle", "登记处置", ["pending"], "handled", ["handling"]), action("followup", "登记随访", ["handled"], "followup", ["followup"]), action("close", "结束事项", ["followup"], "closed", ["result"])])
}


# 活动精修只扩展既有活动记录，保留其他部门字段与状态机。
SCHEMAS['activities']['fields'].extend([
    field('activity_type','活动类型'),field('target_group','活动对象'),
    field('planned_end','计划结束时间','datetime-local'),field('purpose','活动目的','textarea'),
    field('plan_content','活动流程','textarea'),field('materials','物资准备','textarea'),
    field('division','大型活动分工','textarea'),field('budget','预算','number'),
    field('transport','交通安排','textarea'),field('safety_plan','安全及应急安排','textarea'),
    field('exception_type','异常类型','select',options=['临时中止','老人身体不适','场地问题','其他情况']),
    field('exception_detail','异常说明','textarea')])


for _kind in ("cleaning", "meals", "activities", "care", "nursing", "concierge", "rehab", "medical"):
    SCHEMAS[_kind]["fields"].extend([field("performed_at", "实际执行时间", "datetime-local"), field("performed_source", "实际时间来源", "select", options=["本人记录", "原始记录", "交班记录"])])


def seed(payload):
    """返回独立副本；GET 不在数据库写入初始化数据。"""
    value = deepcopy(payload.get("department_demo", {"version": 1, "rows": {}, "drafts": [], "beds": []}))
    if not isinstance(value, dict): raise ValueError("部门数据结构错误")
    if value.get("version") != 1:
        raise ValueError("部门数据版本不兼容")
    if not isinstance(value.get("rows", {}), dict) or not isinstance(value.get("drafts", []), list) or not isinstance(value.get("beds", []), list):
        raise ValueError("部门数据集合结构错误")
    for rows in value.get("rows", {}).values():
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("status"), str) or not isinstance(row.get("history", []), list) for row in rows):
            raise ValueError("部门业务记录结构错误")
    if any(not isinstance(d, dict) or not isinstance(d.get("actor"), str) or not isinstance(d.get("kind"), str) or not isinstance(d.get("values"), dict) for d in value.get("drafts", [])):
        raise ValueError("部门草稿结构错误")
    if any(not isinstance(b, dict) or not isinstance(b.get("id"), str) or not isinstance(b.get("status"), str) for b in value.get("beds", [])):
        raise ValueError("部门床位结构错误")
    for kind in SCHEMAS:
        value.setdefault("rows", {}).setdefault(kind, [])
    value.setdefault("drafts", [])
    if not value.get("beds"):
        occupied = [{"id": r["bed"], "label": r["bed"], "status": "occupied", "resident_id": r["resident_id"]}
                    for r in payload.get("tables", {}).get("residents", []) if r.get("bed")]
        occupied = list({b["id"]: b for b in occupied}.values())
        for b in occupied: b["source"] = "既有长者主档（非空床普查）"
        value["beds"] = occupied + [{"id": "DEMO-BED-" + str(n), "label": "合成演示床位 " + str(n), "status": "available", "resident_id": "", "source": "纯合成 Demo 床位，不代表机构库存"} for n in range(1, 5)]
    # 主档后来新增的实际占床必须覆盖演示预留；缺失主档不推断为空床。
    by_bed = {b["id"]: b for b in value["beds"]}
    for r in payload.get("tables", {}).get("residents", []):
        if not r.get("bed"): continue
        bed = by_bed.get(r["bed"])
        if not bed:
            bed = {"id": r["bed"], "label": r["bed"], "source": "既有长者主档（非空床普查）"}
            value["beds"].append(bed); by_bed[r["bed"]] = bed
        bed.update(status="occupied", resident_id=r["resident_id"])
    return value


def is_sales_candidate(payload, resident):
    rid=resident['resident_id']
    has_stay=any(x.get('resident_id')==rid and not x.get('end_date') for x in payload.get('tables',{}).get('stays',[]))
    return not resident.get('bed') and not has_stay and any(p.get('sales_version')==1 and p.get('resident_id')==rid and p.get('stage')!='admitted' for p in payload.get('department_demo',{}).get('rows',{}).get('leads',[]))


def residents(payload):
    # 部门共享目录只投影列表必需字段；证件及监护联系方式留在原授权主档。
    return [{"id": r["resident_id"], "label": r.get("display_name", r["resident_id"]),
             "bed": r.get("bed", ""), "sex": r.get("sex", ""),
             "age": r.get("age", ""), "care_level": r.get("care_level", "")}
            for r in payload.get("tables", {}).get("residents", []) if not is_sales_candidate(payload,r)]


def validate(values, kind, payload, state, partial=False, existing=None):
    defs = {f["key"]: f for f in SCHEMAS[kind]["fields"]}
    if set(values) - set(defs):
        raise ValueError("存在未识别业务字段")
    for key, f in defs.items():
        v = values.get(key)
        if f["required"] and not partial and (v is None or v == "" or v == [] or isinstance(v, str) and not v.strip()):
            raise ValueError(f["label"] + "为必填项")
        if v is None or v == "":
            continue
        typ = f["type"]
        if typ == "multi":
            if not isinstance(v, list) or len(v) > 500 or any(not isinstance(x, str) for x in v) or len(set(v)) != len(v):
                raise ValueError(f["label"] + "格式错误")
        elif typ == "number":
            if isinstance(v, bool): raise ValueError(f["label"] + "须为非负数")
            try: number = float(v)
            except (ValueError, TypeError): raise ValueError(f["label"] + "须为非负数")
            if not math.isfinite(number) or number < 0: raise ValueError(f["label"] + "须为非负数")
        else:
            if not isinstance(v, str) or len(v) > 8000: raise ValueError(f["label"] + "格式或长度错误")
            if typ == "date": date.fromisoformat(v)
            if typ == "datetime-local": datetime.fromisoformat(v)
        if f["options"] and v not in [o["value"] for o in f["options"]]: raise ValueError("选项不正确")
        sources = {"residents": residents(payload), "beds": state["beds"], "staff": STAFF, "published_menus": [{"id": r["id"], "label": r["title"]} for r in state["rows"]["menus"] if r["status"] == "published"], "published_menus": [r for r in state["rows"]["menus"] if r["status"] == "published"]}
        if existing and key == "menu_id" and v == existing.get("menu_id"):
            sources["published_menus"] = state["rows"]["menus"]
        if f["source"]:
            options = {x["id"] for x in sources.get(f["source"], state["rows"].get(f["source"], []))}
            if any(x not in options for x in (v if typ == "multi" else [v])): raise ValueError(f["label"] + "引用不存在")
    if bool(values.get("performed_at")) != bool(values.get("performed_source")):
        raise ValueError("实际执行时间与时间来源须同时填写；可均留空")

    if kind in {"meals", "purchases"} and values.get("menu_id") and not (existing and values["menu_id"] == existing.get("menu_id")):
        if not any(r["id"] == values["menu_id"] and r["status"] == "published" for r in state["rows"]["menus"]):
            raise ValueError("只能关联已发布食谱")


def add_history(row, actor, at, action_name, before):
    row["updated_at"] = at
    after = {k: deepcopy(v) for k, v in row.items() if k != "history"}
    execution_event = action_name.startswith("transition:") or action_name in {"create", "update"}
    row.setdefault("history", []).append({"at": at, "actor": actor, "action": action_name,
        "performed_at": row.get("performed_at", "") if execution_event else "",
        "performed_source": row.get("performed_source", "") if execution_event else "",
        "before": before, "after": after})
    row["updated_at"] = at


def make_row(state, kind, values, actor, at):
    row = {"id": kind + "-" + uuid4().hex[:12], **deepcopy(values), "status": SCHEMAS[kind]["initial"], "created_at": at, "updated_at": at, "history": []}
    if kind == "cleaning": row["frequency"] = values.get("frequency") or "临时"
    add_history(row, actor, at, "create", None)
    state["rows"][kind].append(row)
    return row


def nursing_command(body):
    """旧护士站样板留在原rows；新专属动作才进入护理领域。"""
    return body.get('kind')=='nursing' and ('mode' in body.get('values',{}) or body.get('action') not in {'create','update','transition','refer_medical','draft_save'})


def apply_command(payload, body, actor, at, facility_id, user=None):
    """纯业务事务。调用者只在成功后写回完整工作区。"""
    new = deepcopy(payload); state = seed(new); kind = body["kind"]; command = body["action"]; values = deepcopy(body.get("values", {}))
    if nursing_command(body):
        from .nursing_workbench import apply_nursing
        outcome = apply_nursing(new,state,body,actor,at,facility_id,user)
        new['department_demo'] = state
        return new,outcome
    if kind == 'sales':
        from .sales_workbench import apply_sales
        outcome = apply_sales(new,state,body,actor,at,facility_id)
        new['department_demo'] = state
        return new,outcome
    if kind == 'leads' and any(r['id']==body.get('id') and r.get('sales_version')==1 for r in state['rows']['leads']):
        raise ValueError('新版销售对象请使用销售工作台动作，不通过旧咨询跳过实际事实')
    if kind not in SCHEMAS: raise ValueError("未知部门业务")
    if kind == 'activities':
        from .activity_workbench import COMMANDS, apply_activity
        activity_row = next((r for r in state['rows']['activities'] if r['id'] == body.get('id')),None)
        modern = bool(activity_row and activity_row.get('activity_version') == 1)
        if command == 'transition' and values.get('action') == 'complete' and modern:
            adapted = deepcopy(body); adapted['action'] = 'complete'; adapted['values'].pop('action',None)
            outcome = apply_activity(new,state,adapted,actor,at,facility_id)
            new['department_demo'] = state
            return new,outcome
        execution_fields = {'attendance','summary','exception_type','exception_detail'}
        if command == 'create' and 'attendance' in values:
            legacy_attendance = values.get('attendance')
            if not isinstance(legacy_attendance,list) or any(not isinstance(x,str) for x in legacy_attendance) or not set(legacy_attendance).issubset(set(values.get('participants',[]))): raise ValueError('旧版签到引用须来自旧参与名单')
        forbidden = execution_fields - {'attendance'} if command == 'create' else execution_fields
        if command in {'create','update','draft_save'} and forbidden.intersection(values):
            raise ValueError('签到及完成结果请使用活动签到/完成操作，策划不可写入执行事实')
        if command == 'transition' and modern and values.get('action') == 'start':
            if set(values) - {'action','performed_at','performed_source'}: raise ValueError('开始活动仅接受本次实际时间')
        if command == 'transition' and not modern and activity_row and activity_row.get('attendance_confirmed') and 'attendance' in values:
            raise ValueError('已确认签到名单只能通过签到操作修改')
        if command in COMMANDS:
            outcome = apply_activity(new,state,body,actor,at,facility_id)
            new['department_demo'] = state
            return new,outcome

    # FDE-TIME-P0：实际发生时间独立于登记时间；历史追记允许，未来实际时间拒绝。
    if values.get("performed_at"):
        if not isinstance(values["performed_at"], str): raise ValueError("实际执行时间格式错误")
        actual = datetime.fromisoformat(values["performed_at"])
        submitted = datetime.fromisoformat(at)
        if actual.tzinfo is None: actual = actual.replace(tzinfo=submitted.tzinfo)
        if actual > submitted: raise ValueError("实际执行时间不得晚于本次提交时间")
    row = next((x for x in state["rows"][kind] if x["id"] == body.get("id")), None)
    related = None
    context = body.get("draft_context")
    if context is not None and command != "draft_save": raise ValueError("草稿办理意图只允许用于保存草稿")
    if context is not None:
        if not isinstance(context, dict) or set(context) - {"action", "step"} or context.get("action") not in {"create", "update", "transition"}:
            raise ValueError("草稿办理意图格式错误")
        if context.get("action") == "transition":
            rule = next((a for a in SCHEMAS[kind]["actions"] if a["key"] == context.get("step")), None)
            if not row or not rule or row["status"] not in rule["from"]: raise ValueError("草稿流转动作与当前业务状态不符")
        elif "step" in context: raise ValueError("非流转草稿不接受动作步骤")
        if context["action"] == "create" and body.get("id"): raise ValueError("新建草稿不接受已有记录编号")
        if context["action"] == "update" and not row: raise ValueError("修改草稿须关联已有记录")
    if command == "draft_save":
        if body.get("id") and not row: raise ValueError("草稿关联记录不存在")
        validate(values, kind, new, state, partial=True)
        state["drafts"] = [d for d in state["drafts"] if not (d["actor"] == actor and d["kind"] == kind and d.get("record_id", "") == body.get("id", ""))]
        state["drafts"].append({"id": uuid4().hex, "actor": actor, "kind": kind, "record_id": body.get("id", ""), "values": values, "intent": deepcopy(context or {"action": "update" if row else "create"}), "updated_at": at})
    elif command == "create":
        validate(values, kind, new, state); row = make_row(state, kind, values, actor, at)
        if kind == "activities": row["attendance_confirmed"] = False
        if kind in {"meals", "purchases"} and values.get("menu_id"):
            row["menu_snapshot"] = deepcopy(next(r for r in state["rows"]["menus"] if r["id"] == values["menu_id"]))
        if kind == "menus" and values.get("replaces_id"):
            if not any(x["id"] == values["replaces_id"] for x in state["rows"]["menus"]): raise ValueError("被替代版本不存在")
    else:
        if not row: raise ValueError("业务记录不存在")
        before = {k: deepcopy(v) for k, v in row.items() if k != "history"}
        if command == "update":
            if row["status"] not in {SCHEMAS[kind]["initial"], "in_progress", "running", "exception", "visit", "intent", "reserved", "draft"}: raise ValueError("归档或发布记录不可直接修改，请保留新版本")
            if kind == 'activities' and row['status'] == 'draft' and row.get('plan_actor') not in {None,actor}: raise ValueError('策划草稿属于其他使用者')
            merged = {f["key"]: row.get(f["key"], "") for f in SCHEMAS[kind]["fields"]}; merged.update(values)
            validate(merged, kind, new, state, partial=(kind == 'activities' and row['status']=='draft'), existing=row)
            if kind == "meals" and values and not values.get("change_reason"): raise ValueError("餐食变更须说明原因")
            if kind == "leads" and row["status"] == "reserved" and "bed_id" in values and values["bed_id"] != row["bed_id"]: raise ValueError("已预留床位不可直接改动")
            row.update(values)
            if kind in {"meals", "purchases"} and values.get("menu_id") and values["menu_id"] != before.get("menu_id"):
                row["menu_snapshot"] = deepcopy(next(r for r in state["rows"]["menus"] if r["id"] == values["menu_id"]))
        elif command == "transition":
            key = values.pop("action", None); rule = next((a for a in SCHEMAS[kind]["actions"] if a["key"] == key), None)
            if not rule or row["status"] not in rule["from"]: raise ValueError("当前状态不支持此操作")
            # 本次状态动作未知实际时间时留空，不能继承上次制作/处置等实际时间。
            if any(f["key"] == "performed_at" for f in SCHEMAS[kind]["fields"]):
                values.setdefault("performed_at", "")
                values.setdefault("performed_source", "")
            merged = {f["key"]: row.get(f["key"], "") for f in SCHEMAS[kind]["fields"]}; merged.update(values)
            validate(merged, kind, new, state, existing=row)
            for key in rule["fields"]:
                if not merged.get(key) or isinstance(merged.get(key), str) and not merged[key].strip(): raise ValueError("请填写：" + next(f["label"] for f in SCHEMAS[kind]["fields"] if f["key"] == key))
            if kind == "leads" and rule["key"] == "reserve":
                bed = next(b for b in state["beds"] if b["id"] == merged["bed_id"])
                if bed["status"] != "available": raise ValueError("床位已被占用或预留")
                bed.update(status="reserved", lead_id=row["id"])
            if kind == "menus" and rule["key"] == "publish":
                conflicts = [x for x in state["rows"]["menus"] if x["id"] != row["id"] and x["status"] == "published" and x["week_start"] == row["week_start"]]
                if any(x["id"] != row.get("replaces_id") for x in conflicts): raise ValueError("该周已有发布食谱，请指定替代版本")
            if kind == "menus" and rule["key"] == "publish" and row.get("replaces_id"):
                old = next(x for x in state["rows"]["menus"] if x["id"] == row["replaces_id"])
                if old["status"] != "published": raise ValueError("仅可替代当前已发布版本")
                old_before = {k: deepcopy(v) for k,v in old.items() if k != "history"};old["status"] = "superseded";add_history(old, actor, at, "version_replaced", old_before)
            row.update(values); row["status"] = rule["to"] or row["status"]
            if kind == 'activities':
                if rule['key'] == 'start': row['actual_start'] = values.get('performed_at','')
                if rule['key'] == 'complete':
                    row.setdefault('attendance_confirmed',False)
                    row.update(completed_at=at,actual_end=values.get('performed_at',''))
            command += ":" + rule["key"]
        elif command == "link_repair":
            if kind != "concierge" or row["status"] not in {"pending", "in_progress"}: raise ValueError("仅待办理或办理中管家事项可转报修")
            if row.get("repair_flow_id"): raise ValueError("此管家事项已关联报修流程")
            if set(values) - {"fault", "equipment_id"}: raise ValueError("报修仅接受最小故障描述和设施编号")
            fault = values.get("fault")
            equipment = values.get("equipment_id", "")
            if not isinstance(fault, str) or not fault.strip() or len(fault) > 2000: raise ValueError("请填写不超过2000字的最小故障描述")
            if not isinstance(equipment, str) or len(equipment) > 100: raise ValueError("设施编号格式错误")
            resident = next((r for r in new.get("tables", {}).get("residents", []) if r.get("resident_id") == row["resident_id"] and r.get("facility_id") == facility_id), None)
            if not resident or not resident.get("bed"): raise ValueError("长者主档未登记床位，请先核对位置资料")
            template = next((t for t in reversed(new["flow"]["templates"]) if t.get("kind") == "maintenance" and t.get("status") == "published"), None)
            if not template: raise ValueError("未找到已发布维修流程模板")
            from .bridge import engine
            command_data = {"type": "start", "template_id": template["id"], "template_version": template["version"], "request_id": body["request_id"], "facility_id": facility_id,
                            "actor": actor, "owner": actor, "role": "主管复核", "at": datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M:%S"),
                            "resident_id": row["resident_id"], "location": resident["bed"], "description": fault.strip(), "equipment_id": equipment,
                            "title": "管家登记设施报修", "due_at": "", "priority": "普通"}
            original_count = len(new["flow"]["instances"]); audit_count = len(new.get("audit", []))
            new = engine("command", workspace=new, command=command_data)
            if len(new["flow"]["instances"]) != original_count + 1 or len(new.get("audit", [])) != audit_count + 1:
                raise ValueError("维修请求编号已被旧流程使用，请刷新后重试新请求")
            row["repair_flow_id"] = new["flow"]["instances"][-1]["id"]
            related = {"id": row["repair_flow_id"], "kind": "maintenance"}
        elif command == "plan_next":
            if kind != "cleaning" or row["status"] != "completed": raise ValueError("仅已完成清扫可生成下次计划")
            frequency = row.get("frequency") or "临时"
            if frequency not in {"每日", "每周"}: raise ValueError("临时清扫不生成周期计划")
            if values: raise ValueError("下次清扫计划无需重复填写字段")
            if row.get("next_id"): raise ValueError("此记录已生成下次清扫计划")
            next_date = (date.fromisoformat(row["date"]) + timedelta(days=1 if frequency == "每日" else 7)).isoformat()
            related = make_row(state, "cleaning", {"title": row["title"], "owner": row["owner"], "area": row["area"], "date": next_date, "frequency": frequency, "reason": "周期清扫计划接续", "parent_id": row["id"]}, actor, at)
            row["next_id"] = related["id"]
        elif command == "link_cleaning":
            if kind != "activities" or row["status"] != "completed": raise ValueError("只有已完成活动可创建清扫")
            if row.get("cleaning_id"): raise ValueError("此活动已关联清扫记录")
            if set(values) - {'cleaning_need'}: raise ValueError('清扫只接受必要需求')
            cleaning_need = values.get('cleaning_need','普通清扫')
            if not isinstance(cleaning_need,str) or not cleaning_need.strip() or len(cleaning_need)>500: raise ValueError('请填写简短清扫需求')
            related = make_row(state, "cleaning", {"title": row["title"] + "活动后清扫", "owner": "cleaning", "area": row["location"], "date": at[:10], "reason": cleaning_need, "activity_id": row["id"], "activity_end": row.get("actual_end", ""), "activity_recorded_completed_at": row.get("completed_at", "")}, actor, at)
            row["cleaning_id"] = related["id"]
        elif command == "refer_medical":
            if kind not in {"care", "nursing"} or row["status"] != "exception": raise ValueError("仅已登记护理异常可交医务室")
            if row.get("medical_id"): raise ValueError("已生成医务事项")
            if values: raise ValueError("移交事项无需重复填写字段")
            related = make_row(state, "medical", {"title": row["title"] + "医务跟进", "owner": "medical", "resident_id": row["resident_id"], "source": kind + ":" + row["id"], "note": row.get("reason", "")}, actor, at)
            row["medical_id"] = related["id"]
        elif command == "convert_resident":
            if kind != "leads" or row["status"] != "reserved": raise ValueError("须先完成意向和床位预留")
            if set(values) - {"admission_date", "unit"}: raise ValueError("入住字段不正确")
            start = values.get("admission_date") or row.get("admission_date")
            if not start: raise ValueError("请输入已确认入住日期")
            date.fromisoformat(start)
            bed = next(b for b in state["beds"] if b["id"] == row["bed_id"])
            if bed.get("lead_id") != row["id"] or bed["status"] != "reserved": raise ValueError("预留床位状态已变化")
            rid = "RES-" + uuid4().hex[:12]; unit = values.get("unit", row.get("unit", ""))
            if not isinstance(unit, str) or len(unit) > 80: raise ValueError("照料区格式错误")
            resident = {"facility_id": facility_id, "resident_id": rid, "display_name": row["contact"], "bed": bed["id"], "unit": unit, "what_matters": "", "support": "", "note": "销售入住交接 " + row["id"]}
            new.setdefault("tables", {}).setdefault("residents", []).append(resident)
            new["tables"].setdefault("stays", []).append({"facility_id": facility_id, "stay_id": "STAY-" + uuid4().hex[:12], "resident_id": rid, "start_date": start, "end_date": "", "status": "在院", "unit": unit, "source_ref": row["id"]})
            bed.update(status="occupied", resident_id=rid);row.update(status="admitted", resident_id=rid, admission_date=start, unit=unit);related=resident
        else: raise ValueError("未知操作")
        add_history(row, actor, at, command, before)
    if command != "draft_save":
        state["drafts"] = [d for d in state["drafts"] if not (d["actor"] == actor and d["kind"] == kind and d.get("record_id", "") == body.get("id", ""))]
    new["department_demo"] = state
    return new, {"record": row, "related": related}


def install_departments(app, db, get_ws, touch):
    from fastapi import Request
    from pydantic import BaseModel, ConfigDict, Field
    from sqlalchemy import select, update
    from zoneinfo import ZoneInfo
    from .database import workspaces, receipts, facilities, utcnow
    from .security import authenticate, fail, actor, digest, log
    from . import policy

    class DepartmentCommand(BaseModel):
        model_config = ConfigDict(extra="forbid")
        request_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{12,70}$")
        expected_workspace_revision: int = Field(ge=0, strict=True)
        action: str = Field(max_length=40)
        kind: str = Field(max_length=40)
        id: str = Field(default="", max_length=80)
        values: dict = Field(default_factory=dict)
        draft_context: dict | None = None

    def check(user,kind=""):
        if kind == "nursing" and user["role"] in {"admin","supervisor","caregiver"}: return
        if user["role"] != "admin" or "*" not in user["units"]:
            fail(403, "部门演示仅限现有全院演示管理员；未建立部门账号")

    @app.get("/api/departments")
    def department_view(request: Request):
        with db.read() as c:
            user, token = authenticate(c, request); check(user); w = get_ws(c, user); state = seed(w["payload"])
            facility = dict(c.execute(select(facilities).where(facilities.c.id == user["facility_id"])).mappings().one())
            at = datetime.now(ZoneInfo(facility["timezone"])).isoformat(timespec="seconds")
            from .director_overview import build_director_overview
            ov = build_director_overview(w["payload"], user, facility, at)
            state["rows"]["activities"] = [r for r in state["rows"]["activities"] if r["status"] != "draft" or r.get("plan_actor") in {None,actor(user)}]
            return {"revision": w["revision"], "departments": DEPARTMENTS, "schemas": SCHEMAS, "data": state["rows"], "residents": residents(w["payload"]), "beds": state["beds"], "staff": STAFF, "published_menus": [{"id": r["id"], "label": r["title"]} for r in state["rows"]["menus"] if r["status"] == "published"],
                    "summary": [{"kind": k, "label": s["label"], "total": len(state["rows"][k]), "pending": sum(r["status"] not in {"completed", "served", "superseded", "admitted", "reviewed", "closed", "recorded"} for r in state["rows"][k])} for k,s in SCHEMAS.items()],
                    "drafts": [d for d in state["drafts"] if d["actor"] == actor(user)], "maintenance": [policy.detail(user, w["payload"], i) for i in w["payload"]["flow"]["instances"] if i.get("kind") == "maintenance" and policy.flow_visible(user,w["payload"],i)],
                    "director_todos": ov["todos"], "ccrc_zones": ov["ccrc_zones"], "out_of_hospital_count": ov["out_of_hospital_count"]}

    @app.post("/api/departments/command")
    def department_command(body: DepartmentCommand, request: Request):
        data = body.model_dump()
        if data.get("draft_context") is None: data.pop("draft_context", None)
        request_hash = digest({"department_command": data})
        with db.write() as c:
            user, token = authenticate(c, request, True); check(user,body.kind if nursing_command(data) else ""); w = get_ws(c,user)
            if nursing_command(data):
                from .nursing_workbench import authorize
                try: authorize(c,user,data,w["payload"])
                except PermissionError as e: fail(403,str(e))
            receipt = c.execute(select(receipts).where(receipts.c.facility_id == user["facility_id"], receipts.c.request_id == body.request_id)).mappings().first()
            if receipt:
                if receipt["result"].get("cancelled"): fail(409, "此请求已终止，请使用新请求编号")
                if receipt["user_id"] != user["id"] or receipt["digest"] != request_hash: fail(409, "请求编号已被不同内容使用")
                touch(c,token); return {**receipt["result"], "replayed": True}
            if body.expected_workspace_revision != w["revision"]: fail(409, "工作区已更新，请刷新核对后再提交")
            facility = c.execute(select(facilities).where(facilities.c.id == user["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(facility["timezone"])).isoformat(timespec="seconds")
            try: new, outcome = apply_command(w["payload"], data, actor(user), at, user["facility_id"],user=user)
            except PermissionError as e: fail(403,str(e))
            if body.action == "link_repair":
                new["audit"][-1].update(actor_type="authenticated-session", authenticated_user_id=user["id"], authenticated_role=user["role"])
            revision = w["revision"] + 1
            c.execute(update(workspaces).where(workspaces.c.facility_id == user["facility_id"]).values(payload=new, revision=revision, updated_at=utcnow()))
            result = {"ok": True, "revision": revision, "request_id": body.request_id, **outcome}
            c.execute(receipts.insert().values(facility_id=user["facility_id"], request_id=body.request_id, user_id=user["id"], digest=request_hash, result=result))
            log(c,user,"department:" + body.action, (outcome["record"] or {}).get("id", ""),body.request_id,summary={"kind":body.kind,"workspace_revision":revision,"before_sha256":digest(w["payload"]),"after_sha256":digest(new)})
            touch(c,token); return result
