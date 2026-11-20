"""第 8 模块：长者与家属轻协同端（CCRC 扩展模块）。
严守「院内岗位核验后脱敏投影」与「唯一共享底座受控写事务」：
- 8.1 活力长者适老微端：大字周食谱与口味提交、已发布社团/水疗一键预报名（不冒充实到签到）、居室报修直连管家与工程四态进度。
- 8.2 家属安心透明端：仅投影 confirmed 已核对费用与独立押金、仅展示实际已签到活动与实拍影像、仅推送已闭环异常回执（剥离内部草稿、未接收交班与护理量表正文）。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from uuid import uuid4
import re

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from zoneinfo import ZoneInfo

from .activity_workbench import attachment_public
from .database import facilities, receipts, utcnow, workspaces
from .departments import apply_command, seed as dept_seed
from .nursing_workbench import active_residents, nursing_state, planned_tasks
from .sales_workbench import amount, effective_stays, fee_summary, sales_state
from .security import actor as actor_str, authenticate, digest, fail, log


def _clean_text(value: str, label: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise ValueError(f"{label}不能为空且长度不超过 {limit} 字")
    return value.strip()


def _four_stage_repair(flow_inst: dict | None) -> dict | None:
    """与前端 maintenance-display.js 同口径的四状态极简投影，且不暴露内部人员或敏感描述。"""
    if not flow_inst or flow_inst.get("kind") != "maintenance":
        return None
    status = flow_inst.get("status", "")
    tasks = flow_inst.get("tasks") or flow_inst.get("steps") or []
    if status == "closed":
        return {"stage_key": "done", "stage_label": "已完成", "step_index": 4}
    if status == "pending_review":
        return {"stage_key": "verify", "stage_label": "待验收", "step_index": 3}
    started = any(t.get("accepted_at") or t.get("status") in {"执行中", "待复核", "暂缓", "已关闭"} for t in tasks if isinstance(t, dict))
    if started:
        return {"stage_key": "working", "stage_label": "处理中", "step_index": 2}
    return {"stage_key": "pending", "stage_label": "待处理", "step_index": 1}


def list_portal_residents(payload: dict, facility_id: str, today_s: str) -> list[dict]:
    stays_by_rid = {s["resident_id"]: s for s in effective_stays(payload, facility_id, today_s)}
    items = []
    for r in payload.get("tables", {}).get("residents", []):
        rid = r.get("resident_id", "")
        if r.get("facility_id") != facility_id or rid not in stays_by_rid:
            continue
        stay = stays_by_rid[rid]
        care_level = r.get("care_level", "") or "未登记"
        is_vitality = r.get("service_category") == "活力自主" or any(k in care_level for k in ("康养", "自理", "乐养", "旅居"))
        items.append({
            "id": rid,
            "label": r.get("display_name", rid),
            "bed": r.get("bed", ""),
            "unit": r.get("unit", ""),
            "care_level": care_level,
            "stay_status": stay.get("status", "在院"),
            "guardian_name": r.get("guardian_name", "") or "已绑定家属",
            "is_vitality": is_vitality,
        })
    return items


def build_elder_view(payload: dict, facility_id: str, resident_id: str, at_s: str) -> dict:
    today_s = at_s[:10]
    residents = list_portal_residents(payload, facility_id, today_s)
    target = next((r for r in residents if r["id"] == resident_id), None)
    if not target:
        raise LookupError("未找到当前在院或保留床位的长者档案")

    raw_res = next(r for r in payload["tables"]["residents"] if r["resident_id"] == resident_id and r.get("facility_id") == facility_id)
    state = dept_seed(payload)
    s_state = sales_state(state)

    # 8.1.1 大字周食谱与口味偏好
    pub_menus = [m for m in state["rows"]["menus"] if m.get("status") == "published"]
    pub_menus.sort(key=lambda m: (m.get("week_start", ""), m.get("updated_at", "")), reverse=True)
    latest_menu = pub_menus[0] if pub_menus else None
    today_meals = [
        {"id": m["id"], "meal": m.get("meal", ""), "title": m.get("title", ""), "status_label": {"planned": "备餐中", "prepared": "已出锅待供餐", "served": "已供餐"}.get(m.get("status", ""), m.get("status", ""))}
        for m in state["rows"]["meals"]
        if m.get("date") == today_s
    ]
    diet_prefs = [
        {"id": p["id"], "content": p["content"], "source": p["source"], "created_at": p.get("created_at", "")[:16].replace("T", " ")}
        for p in s_state["preferences"]
        if p.get("resident_id") == resident_id and p.get("category") in {"饮食", "生活"}
    ]
    if raw_res.get("what_matters"):
        diet_prefs.insert(0, {"id": "master-what-matters", "content": raw_res["what_matters"], "source": "长者主档习惯", "created_at": "长期有效"})

    # 8.1.2 金陵社团与水疗课程报名（仅展示已发布/进行中/已完成，不含草稿与取消）
    activities = []
    for act in state["rows"]["activities"]:
        if act.get("status") in {"draft", "cancelled"}:
            continue
        participants = act.get("participants") or []
        attendance = act.get("attendance") or []
        activities.append({
            "id": act["id"],
            "title": act.get("title", ""),
            "activity_type": act.get("activity_type", "社区活动"),
            "scheduled_at": act.get("scheduled_at", "").replace("T", " "),
            "location": act.get("location", ""),
            "status": act.get("status", ""),
            "can_signup": act.get("status") in {"planned", "running"} and resident_id not in participants,
            "signed_up": resident_id in participants,
            "checked_in": bool(act.get("attendance_confirmed")) and resident_id in attendance,
            "participant_count": len(participants),
        })
    activities.sort(key=lambda x: x["scheduled_at"], reverse=True)

    # 8.1.3 居室一键报修进度与生活管家诉求
    flows_by_id = {f["id"]: f for f in payload.get("flow", {}).get("instances", []) if f.get("kind") == "maintenance"}
    tasks_by_id = {t["task_id"]: t for t in payload.get("tables", {}).get("tasks", [])}
    concierge_list = []
    for item in state["rows"]["concierge"]:
        if item.get("resident_id") != resident_id:
            continue
        flow_obj = None
        if item.get("repair_flow_id") and item["repair_flow_id"] in flows_by_id:
            raw_f = deepcopy(flows_by_id[item["repair_flow_id"]])
            raw_f["tasks"] = [tasks_by_id.get(s.get("task_id"), {}) for s in raw_f.get("steps", [])]
            flow_obj = _four_stage_repair(raw_f)
        concierge_list.append({
            "id": item["id"],
            "title": item.get("title", ""),
            "date": item.get("date", ""),
            "request": item.get("request", ""),
            "status_label": {"pending": "管家待接单", "in_progress": "管家跟进中", "completed": "已办结"}.get(item.get("status", ""), item.get("status", "")),
            "result": item.get("result", ""),
            "repair": flow_obj,
        })
    concierge_list.sort(key=lambda x: x["date"], reverse=True)

    return {
        "resident": target,
        "as_of": at_s,
        "menu": {
            "id": latest_menu["id"],
            "title": latest_menu.get("title", ""),
            "week_start": latest_menu.get("week_start", ""),
            "content": latest_menu.get("menu", ""),
            "note": latest_menu.get("note", ""),
        } if latest_menu else None,
        "today_meals": today_meals,
        "diet_preferences": diet_prefs,
        "activities": activities,
        "concierge_requests": concierge_list,
    }


def build_family_view(payload: dict, facility_id: str, resident_id: str, at_s: str, period_s: str = "") -> dict:
    today_s = at_s[:10]
    month_s = period_s or at_s[:7]
    residents = list_portal_residents(payload, facility_id, today_s)
    target = next((r for r in residents if r["id"] == resident_id), None)
    if not target:
        raise LookupError("未找到当前在院或保留床位的长者档案")

    state = dept_seed(payload)
    s_state = sales_state(state)
    n_state = nursing_state(state)

    # 8.2.1 已核对账单、预交抵扣与独立押金明细（严禁混入未核实的 customer_reported）
    summary = fee_summary(s_state, resident_id, month_s)
    charges = [
        {"id": c["id"], "period": c["period"], "description": c.get("description") or "月度照护与居住服务费", "amount": amount(c["amount_cents"]), "created_at": c.get("created_at", "")[:10]}
        for c in s_state["charges"]
        if c["resident_id"] == resident_id and c["period"] == month_s
    ]
    confirmed_payments = [
        {
            "id": p["id"],
            "category_label": {"period": "本期实收核销", "advance": "预交养老金入账", "deposit": "履约医疗备用押金（独立隔离）", "allocation": "预交款显式抵扣本期账单"}.get(p["category"], p["category"]),
            "category": p["category"],
            "period": p.get("period", ""),
            "amount": amount(p["amount_cents"]),
            "reference": p.get("confirmation_ref") or p.get("reference", ""),
            "confirmed_at": (p.get("confirmed_at") or p.get("created_at", ""))[:16].replace("T", " "),
        }
        for p in s_state["payments"]
        if p["resident_id"] == resident_id and p.get("status") == "confirmed"
    ]
    unverified_reports = [p for p in s_state["payments"] if p["resident_id"] == resident_id and p.get("status") == "customer_reported"]

    # 8.2.2 仅展示实际已签到（attendance_confirmed=True 且在 attendance 名单内）的活动与实拍照片
    verified_activities = []
    for act in state["rows"]["activities"]:
        if act.get("status") in {"draft", "cancelled"} or not act.get("attendance_confirmed"):
            continue
        if resident_id not in (act.get("attendance") or []):
            continue
        verified_activities.append({
            "id": act["id"],
            "title": act.get("title", ""),
            "activity_type": act.get("activity_type", "社区文娱"),
            "scheduled_at": act.get("scheduled_at", "").replace("T", " "),
            "location": act.get("location", ""),
            "summary": act.get("summary", ""),
            "attachments": [attachment_public(a) for a in act.get("attachments", [])],
        })
    verified_activities.sort(key=lambda x: x["scheduled_at"], reverse=True)

    # 8.2.3 照护履约客观摘要 + 经护士站/医务室闭环确认后的安心回执（彻底剥离内部交班草稿、未确认观察与评估量表正文）
    today_tasks = [t for t in planned_tasks(payload, state, n_state, facility_id, today_s) if t["resident_id"] == resident_id]
    completed_tasks = [
        {"title": t["title"], "slot": t.get("slot", ""), "performed_at": (t.get("performed_at") or t.get("completed_at", ""))[:16].replace("T", " ")}
        for t in today_tasks if t["status"] == "completed"
    ]
    respectful_deferred = [
        {"title": t["title"], "slot": t.get("slot", ""), "reason": t.get("reason", ""), "note": "尊重长者现场作息意愿改期跟进，不作强行催促"}
        for t in today_tasks if t["status"] == "incomplete"
    ]

    closed_receipts = []
    for obs in n_state["observations"]:
        if obs.get("resident_id") == resident_id and obs.get("status") == "handled" and obs.get("handling_note"):
            closed_receipts.append({
                "id": obs["id"],
                "source_label": "护士站闭环回执",
                "type": obs.get("type", "日常观察"),
                "occurred_at": (obs.get("occurred_at") or obs.get("created_at", ""))[:16].replace("T", " "),
                "handled_at": obs.get("handled_at", "")[:16].replace("T", " "),
                "summary": obs["handling_note"],
            })
    for med in state["rows"]["medical"]:
        if med.get("resident_id") == resident_id and med.get("status") in {"handled", "followup", "closed"}:
            closed_receipts.append({
                "id": med["id"],
                "source_label": "驻院医务与绿通协同通知",
                "type": med.get("title", "医务随访"),
                "occurred_at": med.get("created_at", "")[:16].replace("T", " "),
                "handled_at": med.get("updated_at", "")[:16].replace("T", " "),
                "summary": med.get("followup") or med.get("handling") or med.get("result", ""),
            })
    closed_receipts.sort(key=lambda x: x["handled_at"], reverse=True)

    visit_records = [
        {
            "id": c["id"],
            "title": c.get("title", ""),
            "date": c.get("date", ""),
            "request": c.get("request", ""),
            "status_label": {"pending": "待生活管家确认", "in_progress": "管家已安排接待", "completed": "已完成接待"}.get(c.get("status", ""), c.get("status", "")),
        }
        for c in state["rows"]["concierge"]
        if c.get("resident_id") == resident_id and "探视" in c.get("title", "")
    ]

    return {
        "resident": target,
        "period": month_s,
        "as_of": at_s,
        "privacy_notice": "仅展示经财务核销的账目、实际签到影像及护士站已闭环处置回执；院内未核实草稿、交接班细节与临床评估量表正文已按最小披露原则隔离。",
        "billing": {
            "period": month_s,
            "due": summary["due"] or "0.00",
            "confirmed_paid": summary["confirmed"] or "0.00",
            "outstanding": summary["outstanding"] or "0.00",
            "advance_balance": summary["advance_balance"] or "0.00",
            "deposit_balance": summary["deposit"] or "0.00",
            "unverified_count": len(unverified_reports),
            "unverified_amount": summary["pending"] or "0.00",
            "charges": charges,
            "confirmed_payments": confirmed_payments,
        },
        "verified_activities": verified_activities,
        "care_digest": {
            "date": today_s,
            "planned_total": len(today_tasks),
            "completed_count": len(completed_tasks),
            "deferred_count": len(respectful_deferred),
            "completed_items": completed_tasks,
            "deferred_items": respectful_deferred,
        },
        "closed_receipts": closed_receipts,
        "visit_records": visit_records,
    }


class ElderActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{12,70}$")
    expected_workspace_revision: int = Field(ge=0, strict=True)
    resident_id: str = Field(min_length=1, max_length=64)
    action: str = Field(pattern=r"^(signup_activity|submit_diet_preference|submit_concierge_request)$")
    values: dict = Field(default_factory=dict)


class FamilyVisitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{12,70}$")
    expected_workspace_revision: int = Field(ge=0, strict=True)
    resident_id: str = Field(min_length=1, max_length=64)
    visit_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    visitor_name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


def install_portal(app, db, get_ws, touch):

    @app.get("/api/portal/residents")
    def portal_residents(request: Request):
        with db.read() as c:
            u, _ = authenticate(c, request)
            w = get_ws(c, u)
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            return {
                "revision": w["revision"],
                "facility": {"id": f["id"], "name": f["name"], "timezone": f["timezone"]},
                "residents": list_portal_residents(w["payload"], u["facility_id"], at[:10]),
            }

    @app.get("/api/portal/elder")
    def portal_elder_get(request: Request, resident_id: str):
        with db.read() as c:
            u, _ = authenticate(c, request)
            w = get_ws(c, u)
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            try:
                data = build_elder_view(w["payload"], u["facility_id"], resident_id, at)
            except LookupError as e:
                fail(404, str(e))
            return {"revision": w["revision"], **data}

    @app.get("/api/portal/family")
    def portal_family_get(request: Request, resident_id: str, period: str = ""):
        with db.read() as c:
            u, _ = authenticate(c, request)
            w = get_ws(c, u)
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            try:
                data = build_family_view(w["payload"], u["facility_id"], resident_id, at, period)
            except LookupError as e:
                fail(404, str(e))
            except ValueError as e:
                fail(422, str(e))
            return {"revision": w["revision"], **data}

    @app.post("/api/portal/elder/action")
    def portal_elder_post(body: ElderActionBody, request: Request):
        raw_dump = body.model_dump()
        req_hash = digest({"portal_elder_action": raw_dump})
        with db.write() as c:
            u, tok = authenticate(c, request, True)
            w = get_ws(c, u)
            rec = c.execute(select(receipts).where(receipts.c.facility_id == u["facility_id"], receipts.c.request_id == body.request_id)).mappings().first()
            if rec:
                if rec["result"].get("cancelled") or rec["user_id"] != u["id"] or rec["digest"] != req_hash:
                    fail(409, "请求编号已被其他内容占用或已终止")
                touch(c, tok)
                return {**rec["result"], "replayed": True}
            if w["revision"] != body.expected_workspace_revision:
                fail(409, "工作区已更新，请刷新核对后再提交")
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            valid_ids = {r["id"] for r in list_portal_residents(w["payload"], u["facility_id"], at[:10])}
            if body.resident_id not in valid_ids:
                fail(404, "当前长者不在有效入住或保留床位名单中")

            ws = deepcopy(w["payload"])
            who = actor_str(u)
            try:
                if body.action == "signup_activity":
                    act_id = _clean_text(str(body.values.get("activity_id", "")), "活动编号", 80)
                    state = dept_seed(ws)
                    act_row = next((x for x in state["rows"]["activities"] if x["id"] == act_id), None)
                    if not act_row or act_row.get("status") not in {"planned", "running"}:
                        raise ValueError("该活动当前不开放预报名")
                    cur_p = list(act_row.get("participants") or [])
                    if body.resident_id not in cur_p:
                        cur_p.append(body.resident_id)
                    ws, out = apply_command(ws, {"kind": "activities", "action": "register", "id": act_id, "values": {"participants": cur_p}}, who, at, u["facility_id"], user=u)
                    entity_id = act_id
                elif body.action == "submit_diet_preference":
                    content = _clean_text(str(body.values.get("content", "")), "口味或饮食需求", 300)
                    ws, out = apply_command(
                        ws,
                        {"kind": "sales", "action": "preference_create", "values": {"resident_id": body.resident_id, "source": "老人本人", "category": "饮食", "content": content}},
                        who, at, u["facility_id"], user=u,
                    )
                    entity_id = out["record"]["id"]
                else:
                    req_text = _clean_text(str(body.values.get("request", "")), "呼叫管家或报修内容", 400)
                    need_repair = bool(body.values.get("need_repair"))
                    title_prefix = "【长者微端居室报修】" if need_repair else "【长者微端呼叫管家】"
                    ws, out = apply_command(
                        ws,
                        {
                            "kind": "concierge", "action": "create",
                            "values": {"title": title_prefix + req_text[:24], "owner": "concierge", "resident_id": body.resident_id, "date": at[:10], "request": req_text},
                        },
                        who, at, u["facility_id"], user=u,
                    )
                    entity_id = out["record"]["id"]
                    if need_repair:
                        ws, out_rep = apply_command(
                            ws,
                            {
                                "request_id": f"{body.request_id}-rep"[:70],
                                "kind": "concierge", "action": "link_repair", "id": entity_id,
                                "values": {"fault": f"长者微端发起居室检修：{req_text[:120]}", "equipment_id": ""},
                            },
                            who, at, u["facility_id"], user=u,
                        )
                        out["repair_flow"] = out_rep.get("related")
            except ValueError as e:
                fail(422, str(e))

            new_rev = w["revision"] + 1
            c.execute(update(workspaces).where(workspaces.c.facility_id == u["facility_id"]).values(payload=ws, revision=new_rev, updated_at=utcnow()))
            result = {"ok": True, "revision": new_rev, "request_id": body.request_id, "entity_id": entity_id}
            c.execute(receipts.insert().values(facility_id=u["facility_id"], request_id=body.request_id, user_id=u["id"], digest=req_hash, result=result))
            log(c, u, f"portal_elder:{body.action}", entity_id, body.request_id, summary={"resident_id": body.resident_id, "workspace_revision": new_rev})
            touch(c, tok)
            return result

    @app.post("/api/portal/family/visit")
    def portal_family_visit(body: FamilyVisitBody, request: Request):
        raw_dump = body.model_dump()
        req_hash = digest({"portal_family_visit": raw_dump})
        with db.write() as c:
            u, tok = authenticate(c, request, True)
            w = get_ws(c, u)
            rec = c.execute(select(receipts).where(receipts.c.facility_id == u["facility_id"], receipts.c.request_id == body.request_id)).mappings().first()
            if rec:
                if rec["result"].get("cancelled") or rec["user_id"] != u["id"] or rec["digest"] != req_hash:
                    fail(409, "请求编号已被其他内容占用或已终止")
                touch(c, tok)
                return {**rec["result"], "replayed": True}
            if w["revision"] != body.expected_workspace_revision:
                fail(409, "工作区已更新，请刷新核对后再提交")
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            valid_ids = {r["id"] for r in list_portal_residents(w["payload"], u["facility_id"], at[:10])}
            if body.resident_id not in valid_ids:
                fail(404, "当前长者不在有效入住或保留床位名单中")

            visitor = _clean_text(body.visitor_name, "探视家属姓名", 80)
            note = body.note.strip()
            detail_txt = f"探视人：{visitor}；计划到访日期：{body.visit_date}" + (f"；备注：{note}" if note else "")
            try:
                ws, out = apply_command(
                    w["payload"],
                    {
                        "kind": "concierge", "action": "create",
                        "values": {
                            "title": f"【家属探视预约】{visitor}（{body.visit_date}）",
                            "owner": "concierge", "resident_id": body.resident_id,
                            "date": body.visit_date, "request": detail_txt,
                        },
                    },
                    actor_str(u), at, u["facility_id"], user=u,
                )
            except ValueError as e:
                fail(422, str(e))

            new_rev = w["revision"] + 1
            c.execute(update(workspaces).where(workspaces.c.facility_id == u["facility_id"]).values(payload=ws, revision=new_rev, updated_at=utcnow()))
            result = {"ok": True, "revision": new_rev, "request_id": body.request_id, "entity_id": out["record"]["id"]}
            c.execute(receipts.insert().values(facility_id=u["facility_id"], request_id=body.request_id, user_id=u["id"], digest=req_hash, result=result))
            log(c, u, "portal_family:visit_booking", out["record"]["id"], body.request_id, summary={"resident_id": body.resident_id, "workspace_revision": new_rev})
            touch(c, tok)
            return result
