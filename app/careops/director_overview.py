"""院长工作台跨部门真实卡点督办聚合 + 本地合成演示多岗位快速切换。
- `/api/director/overview`：实时扫描当前工作区，提取 48h 临期床位预留、待核对申报款项、待跨人验收维修、已转医务待查看护理异常、未接收九要素交接班、生活管家新诉求与仙林三甲绿通外院保留床位。
- `/api/auth/demo-accounts` 与 `/api/auth/demo-switch`：仅当数据目录存在 `TAIBAO_CCRC_SYNTHETIC_DEMO.txt` 标记时启用，不影响常规安全与权限测试。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import os
import secrets
import time

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from zoneinfo import ZoneInfo

from .bridge import ROOT
from .database import facilities, sessions, users
from .departments import seed as dept_seed
from .nursing_workbench import nursing_state
from . import policy
from .sales_workbench import effective_stays, sales_state
from .security import (
    COOKIE,
    ROLE_LABELS,
    authenticate,
    csrf_for,
    fail,
    log,
    public_user,
    sha,
)

DEMO_MARKER = "TAIBAO_CCRC_SYNTHETIC_DEMO.txt"


def _is_taibao_demo() -> bool:
    data_dir = Path(os.getenv("CAREOPS_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    return (data_dir / DEMO_MARKER).is_file()


def build_director_overview(payload: dict, user: dict, facility: dict, at_s: str) -> dict:
    tz = ZoneInfo(facility["timezone"])
    now = datetime.fromisoformat(at_s)
    today_s = at_s[:10]
    facility_id = facility["id"]

    state = dept_seed(payload)
    s_state = sales_state(state)
    n_state = nursing_state(state)

    residents_by_id = {
        r["resident_id"]: r.get("display_name", r["resident_id"])
        for r in payload.get("tables", {}).get("residents", [])
        if r.get("facility_id") == facility_id
    }

    todos: list[dict] = []

    # 1. 销售部：48小时内即将到期的床位原子预留锁
    for res in s_state["reservations"]:
        if res.get("status") != "active":
            continue
        try:
            exp = datetime.fromisoformat(res["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=tz)
        except ValueError:
            continue
        remaining_hours = (exp - now).total_seconds() / 3600.0
        if remaining_hours <= 48:
            r_name = residents_by_id.get(res.get("resident_id", ""), res.get("resident_id", ""))
            todos.append({
                "id": f"live-res-{res['id']}",
                "title": f"床位预留临期督办：{res['bed_id']}（准长者 {r_name}）",
                "department": "销售部",
                "type": "approval",
                "created_at": res.get("created_at", at_s)[:16],
                "deadline": res["expires_at"][:16],
                "description": f"预留原因：{res.get('reason', '未填写')}。距离自动失效不足 48 小时，请督促顾问跟进签约或释放房源。",
                "target_page": "dept-sales",
                "source": "live",
            })

    # 2. 销售部：家属口头/凭证申报待财务核对款项（customer_reported）
    for pay in s_state["payments"]:
        if pay.get("status") != "customer_reported":
            continue
        r_name = residents_by_id.get(pay.get("resident_id", ""), pay.get("resident_id", ""))
        todos.append({
            "id": f"live-pay-{pay['id']}",
            "title": f"待核对家属申报款项：{r_name}",
            "department": "销售部",
            "type": "approval",
            "amount": round(pay.get("amount_cents", 0) / 100.0, 2),
            "created_at": pay.get("created_at", at_s)[:16],
            "deadline": "",
            "description": f"申报说明：{pay.get('reference', '未填写')}。未核对前已在家属透明端自动隔离，不计入实收。",
            "target_page": "dept-sales",
            "source": "live",
        })

    # 3. 后勤部：待跨人独立验收或进行中的居室/公区维修单
    for inst in payload.get("flow", {}).get("instances", []):
        if inst.get("kind") != "maintenance" or inst.get("status") not in {"pending_review", "active"}:
            continue
        if not policy.flow_visible(user, payload, inst):
            continue
        detail = policy.detail(user, payload, inst)
        is_review = inst["status"] == "pending_review"
        todos.append({
            "id": f"live-maint-{inst['id']}",
            "title": f"{'【待独立验收】' if is_review else '【维修跟进】'}{detail.get('title') or inst['id']}",
            "department": "后勤部",
            "type": "approval" if is_review else "feedback",
            "created_at": (detail.get("updated_at") or at_s)[:16].replace(" ", "T"),
            "deadline": (detail.get("repair_due_at") or "")[:16].replace(" ", "T"),
            "description": f"工单编号 {inst['id']} · 当前状态：{'待跨人独立验收（本人不可自验）' if is_review else '工程处理中'}。",
            "target_page": "dept-maintenance",
            "flow_id": inst["id"],
            "source": "live",
        })

    # 4. 护理体系 / 医务室：已转医务室待查看或新建未闭环的护理异常观察
    med_by_id = {m["id"]: m for m in state["rows"]["medical"]}
    for obs in n_state["observations"]:
        st = obs.get("status", "")
        if st == "handled":
            continue
        r_name = residents_by_id.get(obs.get("resident_id", ""), obs.get("resident_id", ""))
        if st == "medical":
            med_row = med_by_id.get(obs.get("medical_id", ""), {})
            if med_row.get("status") in {"handled", "followup", "closed"}:
                continue
            todos.append({
                "id": f"live-obs-{obs['id']}",
                "title": f"护理转介医务待巡诊：{r_name}（{obs.get('type', '异常观察')}）",
                "department": "医务室",
                "type": "feedback",
                "created_at": obs.get("created_at", at_s)[:16],
                "deadline": f"{today_s}T18:00",
                "description": f"客观观察：{obs.get('description', '')}。护士站已按最小症状摘要转介驻院医务室，医务处置前原护理异常保持挂起。",
                "target_page": "dept-medical",
                "source": "live",
            })
        elif st in {"new", "reassess"}:
            todos.append({
                "id": f"live-obs-{obs['id']}",
                "title": f"护士站重点异常待研判：{r_name}（{obs.get('type', '异常')}）",
                "department": "护理部",
                "type": "feedback",
                "created_at": obs.get("created_at", at_s)[:16],
                "deadline": f"{today_s}T17:30",
                "description": obs.get("description", ""),
                "target_page": "dept-nursing",
                "source": "live",
            })

    # 5. 护理体系：已发起但接班人尚未显式点击接收的九要素交接班
    hm_ids = {m["handover_id"] for m in n_state["handover_metadata"]}
    for ho in payload.get("tables", {}).get("handovers", []):
        if ho.get("handover_id") not in hm_ids or ho.get("status") != "待接收":
            continue
        r_name = residents_by_id.get(ho.get("resident_id", ""), ho.get("resident_id", ""))
        todos.append({
            "id": f"live-ho-{ho['handover_id']}",
            "title": f"交接班待显式接收：{r_name}（转{ho.get('shift', '')}）",
            "department": "护理部",
            "type": "feedback",
            "created_at": ho.get("created_at", at_s)[:16].replace(" ", "T"),
            "deadline": "",
            "description": f"交班事实：{ho.get('observation', '')}；下班必做：{ho.get('request', '')}（写交班不等于接班，须接班账户确认接收）。",
            "target_page": "dept-nursing",
            "source": "live",
        })

    # 6. 生活管家新诉求与家属探视预约
    for conc in state["rows"]["concierge"]:
        if conc.get("status") != "pending":
            continue
        r_name = residents_by_id.get(conc.get("resident_id", ""), conc.get("resident_id", ""))
        todos.append({
            "id": f"live-conc-{conc['id']}",
            "title": f"管家待办：{conc.get('title') or r_name}",
            "department": "护理体系·管家",
            "type": "read",
            "created_at": conc.get("created_at", at_s)[:16],
            "deadline": "",
            "description": conc.get("request", ""),
            "target_page": "dept-concierge",
            "source": "live",
        })

    # 7. 仙林三甲绿通外院保留床位状态提醒
    for stay in effective_stays(payload, facility_id, today_s):
        if stay.get("status") != "外院":
            continue
        r_name = residents_by_id.get(stay["resident_id"], stay["resident_id"])
        todos.append({
            "id": f"live-stay-{stay['stay_id']}",
            "title": f"三甲绿通外院保床跟踪：{r_name}",
            "department": "医务室",
            "type": "read",
            "created_at": f"{stay.get('start_date', today_s)}T09:00",
            "deadline": "",
            "description": "长者当前处于外院绿通就诊状态，院内床位与主档持续保留，返院前请驻院医务室持续跟进出院交接。",
            "target_page": "dept-medical",
            "source": "live",
        })

    # CCRC 四业态楼栋房态分布
    zones: dict[str, dict[str, int]] = {}
    for b in state["beds"]:
        zone = b.get("building") or "未分区床位"
        bucket = zones.setdefault(zone, {"total": 0, "occupied": 0, "available": 0, "reserved": 0, "unavailable": 0})
        bucket["total"] += 1
        st = b.get("status", "available")
        if st in bucket:
            bucket[st] += 1

    return {
        "as_of": at_s,
        "timezone": facility["timezone"],
        "todos": todos,
        "ccrc_zones": [{"zone": k, **v} for k, v in zones.items()],
        "out_of_hospital_count": sum(1 for s in effective_stays(payload, facility_id, today_s) if s.get("status") == "外院"),
    }


class DemoSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=60)


def install_director_and_demo_auth(app, db, get_ws, touch, settings):

    @app.get("/api/director/overview")
    def director_overview_endpoint(request: Request):
        with db.read() as c:
            u, _ = authenticate(c, request)
            if u["role"] != "admin" or "*" not in u["units"]:
                fail(403, "院长总控聚合仅开放给全院管理员")
            w = get_ws(c, u)
            f = c.execute(select(facilities).where(facilities.c.id == u["facility_id"])).mappings().one()
            at = datetime.now(ZoneInfo(f["timezone"])).isoformat(timespec="seconds")
            return {"revision": w["revision"], **build_director_overview(w["payload"], u, dict(f), at)}

    @app.get("/api/auth/demo-accounts")
    def demo_accounts_endpoint():
        if not _is_taibao_demo():
            return {"enabled": False, "accounts": []}
        with db.read() as c:
            rows = c.execute(select(users).where(users.c.active == True).order_by(users.c.created_at.asc())).mappings().all()
            return {
                "enabled": True,
                "accounts": [
                    {
                        "username": r["username"],
                        "display_name": r["display_name"],
                        "role": r["role"],
                        "role_label": ROLE_LABELS.get(r["role"], r["role"]),
                        "units": r["units"],
                    }
                    for r in rows
                ],
            }

    @app.post("/api/auth/demo-switch")
    def demo_switch_endpoint(body: DemoSwitchBody, request: Request, response: Response):
        if not _is_taibao_demo():
            fail(404, "当前非本地合成演示模式，未启用快捷角色切换")
        uname = body.username.strip().lower()
        now_ts = int(time.time())
        with db.write() as c:
            row = c.execute(select(users).where(users.c.username == uname, users.c.active == True)).mappings().first()
            if not row:
                fail(404, "未找到该演示角色账户")
            target_user = dict(row)
            token = secrets.token_urlsafe(32)
            c.execute(sessions.insert().values(
                token_hash=sha(token), user_id=target_user["id"],
                created=now_ts, last_seen=now_ts, expires=now_ts + 28800,
            ))
            log(c, target_user, "demo_role_switch", target_user["username"])
        is_https = (
            settings.origin.startswith("https://")
            or request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
            or request.url.scheme == "https"
        )
        response.set_cookie(
            COOKIE, token, max_age=28800, httponly=True,
            samesite="strict", secure=is_https, path="/",
        )
        return {"user": public_user(target_user), "csrf": csrf_for(token)}
