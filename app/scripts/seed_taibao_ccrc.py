"""栖云颐养·金陵国际康养社区（CCRC 四业态合成演示种子库）。
通过真实业务命令接口灌入康养、颐养、静心认知症单元、乐养旅居四类场景数据，不绕过服务端校验规则。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import base64
import json
import os
import secrets
import struct
import zlib

from sqlalchemy import select, update
from zoneinfo import ZoneInfo

from careops.app import Settings
from careops.bridge import engine, ROOT
from careops.database import Database, facilities, workspaces, users, utcnow
from careops.departments import apply_command, seed as dept_seed, add_history
from careops.resident_profile import validate_profile
from careops.security import actor as actor_str, create_user, log

FACILITY_ID = "DEMO-01"
FACILITY_NAME = "栖云颐养·金陵国际康养社区（演示环境）"
TIMEZONE = "Asia/Shanghai"
MARKER_FILE = "TAIBAO_CCRC_SYNTHETIC_DEMO.txt"


def _make_clean_png(width: int = 640, height: int = 360) -> bytes:
    """生成带暖米白纸纹色块与金陵云锦几何纹样的合法 PNG 实拍占位图（纯合成，无真实人脸隐私）。"""
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            # Warm paper base (#FAF6F0) + subtle frame + teal/vermilion geometric motif
            r, g, b = 250, 246, 240
            if x < 18 or x >= width - 18 or y < 18 or y >= height - 18:
                r, g, b = 36, 91, 82  # #245B52 deep teal border
            elif 34 <= x <= width - 34 and 34 <= y <= height - 34 and (x in (34, width - 34) or y in (34, height - 34)):
                r, g, b = 201, 43, 39  # #C92B27 fine inner rule
            else:
                dx, dy = x - width // 2, y - height // 2
                dist2 = dx * dx + dy * dy
                if 3600 <= dist2 <= 11000:
                    r, g, b = 36, 91, 82
                elif dist2 < 3600 and ((x + y) // 16) % 2 == 0:
                    r, g, b = 216, 178, 120
                elif 115 <= y <= 245 and 90 <= x <= width - 90 and (y % 42 < 3):
                    r, g, b = 220, 212, 200
            row.extend((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def initialize_taibao_demo(data_dir: Path) -> dict:
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / MARKER_FILE
    db_file = data_dir / "careops.sqlite3"
    if db_file.exists() and not marker.is_file():
        raise RuntimeError("目标目录已存在非栖云颐养演示数据库；保留原件不覆盖，请换用新目录。")

    os.environ["CAREOPS_DATA_DIR"] = str(data_dir)
    settings = Settings.from_env()
    db = Database(settings.database_url)
    db.initialize()

    with db.read() as c:
        existing = c.execute(select(facilities).where(facilities.c.id == FACILITY_ID)).mappings().first()
        if existing and marker.is_file():
            creds = data_dir / f"INITIAL_CREDENTIALS_{FACILITY_ID}.txt"
            return {"initialized": False, "facility": dict(existing), "credentials_file": str(creds)}

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz).replace(microsecond=0)
    today = now.date()
    today_s = today.isoformat()
    yesterday_s = (today - timedelta(days=1)).isoformat()
    two_days_ago_s = (today - timedelta(days=2)).isoformat()
    tomorrow_s = (today + timedelta(days=1)).isoformat()
    three_days_later_s = (today + timedelta(days=3)).isoformat()
    month_s = today_s[:7]
    week_monday_s = (today - timedelta(days=today.weekday())).isoformat()

    base_ws = json.loads((ROOT / "fixtures/v03-synthetic-workspace.json").read_text(encoding="utf-8"))
    base_ws["classification"] = "栖云颐养·金陵国际康养社区 CCRC 合成演示库（人物、电话与证件号均为虚构数据）"
    base_ws["asOf"] = today_s

    personas = {
        "R001": {
            "display_name": "林素华", "sex": "女", "age": 79, "unit": "2F", "bed": "1栋2F-201A",
            "birthplace": "江苏南京", "former_occupation": "退休中学语文教师",
            "care_level": "康养自理", "dementia_status": "否", "service_category": "活力自主",
            "guardian_name": "陈敏", "guardian_phone": "13800138101",
            "what_matters": "与老伴陈伯钧分住康养区与颐养区，每日午后固定探望；喜爱金陵云锦手作与书法，饮食清淡少糖",
            "support": "视力轻度老花，适合大字菜单提醒；独立行走稳健",
            "note": "CCRC夫妻分住不分区样板（康养活力公寓）", "stay_status": "在院", "start_date": "2026-06-15",
        },
        "R002": {
            "display_name": "沈国栋", "sex": "男", "age": 76, "unit": "2F", "bed": "1栋2F-202A",
            "birthplace": "江苏苏州", "former_occupation": "退休土木工程师",
            "care_level": "康养自理", "dementia_status": "否", "service_category": "活力自主",
            "guardian_name": "沈嘉伟", "guardian_phone": "13800138102",
            "what_matters": "每周二、五参加恒温水疗馆水中运动训练；偏好苏式软烂面点",
            "support": "水疗入池需防滑扶手常规引导",
            "note": "康养活力公寓常住长者", "stay_status": "在院", "start_date": "2026-05-10",
        },
        "R003": {
            "display_name": "赵静娴", "sex": "女", "age": 74, "unit": "2F", "bed": "5栋1F-106A",
            "birthplace": "上海徐汇", "former_occupation": "退休出版社美术编辑",
            "care_level": "乐养旅居", "dementia_status": "否", "service_category": "活力自主",
            "guardian_name": "陆晓雯", "guardian_phone": "13800138103",
            "what_matters": "秋季仙林旅居短住体验，关注微缩金陵街景茶社与周边羊山公园步道，意向转康养长住",
            "support": "完全自主生活",
            "note": "乐养旅居短住体验样板（拟转康养常住）", "stay_status": "在院", "start_date": two_days_ago_s,
        },
        "R004": {
            "display_name": "顾明远", "sex": "男", "age": 80, "unit": "2F", "bed": "1栋2F-205A",
            "birthplace": "江苏无锡", "former_occupation": "退休气象研究员",
            "care_level": "康养自理", "dementia_status": "否", "service_category": "活力自主",
            "guardian_name": "顾清源", "guardian_phone": "13800138104",
            "what_matters": "晨起阅读报刊，喜爱门球与古典诗词讲座",
            "support": "夜间起夜需感应地灯常开",
            "note": "康养活力公寓常住长者", "stay_status": "在院", "start_date": "2026-04-20",
        },
        "R005": {
            "display_name": "陈伯钧", "sex": "男", "age": 82, "unit": "3F", "bed": "2栋3F-301A",
            "birthplace": "江苏南京", "former_occupation": "退休高校历史系教授",
            "care_level": "颐养二级协助", "dementia_status": "否", "service_category": "护理",
            "guardian_name": "陈敏", "guardian_phone": "13800138101",
            "what_matters": "林素华老伴；午休13:00-14:30习惯安静不被打扰，下午偏好温水擦浴，餐食需低盐软饭",
            "support": "左膝置换术后助行器辅助步行，沐浴与上下水疗池需照护师全程协助防滑",
            "note": "CCRC夫妻分住不分区样板（颐养协助生活区）", "stay_status": "在院", "start_date": "2026-06-15",
        },
        "R006": {
            "display_name": "吴桂芳", "sex": "女", "age": 84, "unit": "3F", "bed": "2栋3F-302A",
            "birthplace": "江苏镇江", "former_occupation": "退休纺织厂会计",
            "care_level": "颐养三级护理", "dementia_status": "否", "service_category": "护理",
            "guardian_name": "周海涛", "guardian_phone": "13800138106",
            "what_matters": "习惯早睡早起，饮水偏好温热陈皮水",
            "support": "轮椅转移需单人保护，目前经仙林绿通外院短期复诊保留床位",
            "note": "仙林三甲绿通（江苏省中医院紫东院区）外院就诊保留床位样板", "stay_status": "外院", "start_date": "2026-03-18",
        },
        "R007": {
            "display_name": "许世昌", "sex": "男", "age": 81, "unit": "3F", "bed": "2栋3F-303A",
            "birthplace": "安徽马鞍山", "former_occupation": "退休水文站站长",
            "care_level": "颐养二级协助", "dementia_status": "否", "service_category": "护理",
            "guardian_name": "许文博", "guardian_phone": "13800138107",
            "what_matters": "喜爱听评话与象棋对弈，痛风史需严格低嘌呤替代餐",
            "support": "穿脱袜鞋与足部护理需协助",
            "note": "颐养协助区特殊膳食联动样板", "stay_status": "在院", "start_date": "2026-07-02",
        },
        "R008": {
            "display_name": "孙佩兰", "sex": "女", "age": 78, "unit": "3F", "bed": "2栋3F-305A",
            "birthplace": "江苏扬州", "former_occupation": "退休小学音乐教师",
            "care_level": "颐养一级协助", "dementia_status": "否", "service_category": "护理",
            "guardian_name": "刘思远", "guardian_phone": "13800138108",
            "what_matters": "热衷合唱团伴奏与手工茶点活动",
            "support": "日常服药时间需当面核对提醒",
            "note": "颐养协助区常住长者", "stay_status": "在院", "start_date": "2026-08-01",
        },
        "R009": {
            "display_name": "周文渊", "sex": "男", "age": 85, "unit": "4F", "bed": "3栋4F-401A",
            "birthplace": "江苏南京", "former_occupation": "退休古籍出版社编审",
            "care_level": "认知症专护三级", "dementia_status": "是", "service_category": "护理",
            "guardian_name": "周清和", "guardian_phone": "13800138109",
            "what_matters": "午后与傍晚听昆曲《牡丹亭》选段可明显缓解焦躁；忌带刺鱼类与粘糯食物",
            "support": "静心非药物怀旧干预对象；黄昏时段易寻旧居，需熟悉照护师顺应情绪陪伴引导",
            "note": "静心认知症专属照护单元核心样板", "stay_status": "在院", "start_date": "2026-02-25",
        },
        "R010": {
            "display_name": "郑秀英", "sex": "女", "age": 83, "unit": "4F", "bed": "3栋4F-402A",
            "birthplace": "江苏常州", "former_occupation": "退休丝绸厂工艺师",
            "care_level": "认知症专护二级", "dementia_status": "是", "service_category": "护理",
            "guardian_name": "韩冬梅", "guardian_phone": "13800138110",
            "what_matters": "触摸丝绸布料与叠衣手工有安全感，早餐偏爱山药小米粥",
            "support": "定向力减退，卫生间及房门保留色彩记忆标识",
            "note": "静心认知症专属照护单元常住长者", "stay_status": "在院", "start_date": "2026-05-22",
        },
        "R011": {
            "display_name": "陆振华", "sex": "男", "age": 86, "unit": "4F", "bed": "3栋4F-403A",
            "birthplace": "江苏南通", "former_occupation": "退休轮船轮机长",
            "care_level": "专业全护理三级", "dementia_status": "待确认", "service_category": "护理",
            "guardian_name": "陆建军", "guardian_phone": "13800138111",
            "what_matters": "喜爱收听整点新闻广播，翻身拍背前希望提前轻声告知",
            "support": "长期卧床防压疮定时翻身，吞咽偏慢需半卧位进食",
            "note": "全护理区长者（认知症状态未获临床文书，诚实标为待确认）", "stay_status": "在院", "start_date": "2026-04-08",
        },
        "R012": {
            "display_name": "唐婉如", "sex": "女", "age": 80, "unit": "4F", "bed": "3栋4F-405A",
            "birthplace": "浙江杭州", "former_occupation": "退休图书管理员",
            "care_level": "专业护理二级", "dementia_status": "否", "service_category": "护理",
            "guardian_name": "宋佳宁", "guardian_phone": "13800138112",
            "what_matters": "喜欢靠窗晒太阳听江南丝竹，忌辛辣葱蒜",
            "support": "起身易体位性头晕，床旁站立需静候30秒",
            "note": "专业护理区常住长者", "stay_status": "在院", "start_date": "2026-07-19",
        },
    }

    for r in base_ws["tables"]["residents"]:
        rid = r["resident_id"]
        if rid in personas:
            p = personas[rid]
            for k in (
                "display_name", "sex", "age", "unit", "bed", "birthplace",
                "former_occupation", "care_level", "dementia_status",
                "service_category", "guardian_name", "guardian_phone",
                "what_matters", "support", "note",
            ):
                r[k] = p[k]
            validate_profile(r)

    for s in base_ws["tables"]["stays"]:
        rid = s["resident_id"]
        if rid in personas:
            s["status"] = personas[rid]["stay_status"]
            s["start_date"] = personas[rid]["start_date"]
            s["unit"] = personas[rid]["unit"]

    # Localize baseline workflow titles & refresh legacy October deadlines so live Taibao escalations surface cleanly
    for t in base_ws.get("tables", {}).get("tasks", []):
        if isinstance(t.get("due_at"), str) and t["due_at"].startswith("2026-10-"):
            t["due_at"] = f"{three_days_later_s} 18:00"
    maint_titles = [
        "2栋3F颐养走廊防滑扶手紧固（待跨人独立验收）",
        "1栋2F康养茶社直饮水机滤芯更换",
        "3栋4F认知症单元夜间门禁电磁锁复检",
        "微缩金陵街景活动厅地面防滑巡检",
    ]
    m_idx = 0
    for inst in base_ws.get("flow", {}).get("instances", []):
        if inst.get("kind") == "maintenance" and inst.get("status") in {"active", "pending_review"}:
            inst["title"] = maint_titles[m_idx % len(maint_titles)]
            m_idx += 1

    with db.write() as c:
        c.execute(facilities.insert().values(id=FACILITY_ID, name=FACILITY_NAME, timezone=TIMEZONE))
        specs = [
            ("admin", "周院长（全院总控演示）", "admin", ["*"]),
            ("supervisor", "林护士长（护理与复核主管）", "supervisor", ["*"]),
            ("caregiver", "张晓梅（一线照护师·2F/3F/4F）", "caregiver", ["2F", "3F", "4F"]),
            ("maintenance", "李建国（后勤维保技师）", "maintenance", []),
            ("viewer", "王顾问（只读观察演示）", "viewer", ["2F"]),
        ]
        lines = [
            "CareOps × 栖云颐养·金陵国际康养社区 本地合成演示初始账户",
            f"机构：{FACILITY_NAME}（{FACILITY_ID} · {TIMEZONE}）",
            "说明：全部资料均为合成脱敏数据，仅用于课程与产品原型演示，勿填入真实长者隐私。",
            "",
        ]
        created_users = {}
        for username, disp, role, units in specs:
            pwd = secrets.token_urlsafe(14)
            u = create_user(c, FACILITY_ID, username, disp, pwd, role, units)
            created_users[username] = {**u, "password": pwd}
            lines += [f"账号：{username}（{disp}）", f"密码：{pwd}", f"角色：{role}", ""]

        admin_user = created_users["admin"]
        sup_user = created_users["supervisor"]
        cg_user = created_users["caregiver"]
        admin_actor = actor_str(admin_user)
        sup_actor = actor_str(sup_user)
        cg_actor = actor_str(cg_user)

        # Initialize department_demo beds with rich CCRC 4-zone metadata
        state = dept_seed(base_ws)
        ccrc_beds = []
        bed_meta = {
            "1栋2F-201A": ("1号楼·康养活力公寓", "2F", "201", "阳光活力单人间", 7800),
            "1栋2F-202A": ("1号楼·康养活力公寓", "2F", "202", "阳光活力单人间", 7800),
            "5栋1F-106A": ("5号楼·乐养旅居公寓", "1F", "106", "金陵庭院旅居套房", 6800),
            "1栋2F-205A": ("1号楼·康养活力公寓", "2F", "205", "阳光活力单人间", 7800),
            "2栋3F-301A": ("2号楼·颐养协助生活区", "3F", "301", "适老协助单人间", 9800),
            "2栋3F-302A": ("2号楼·颐养协助生活区", "3F", "302", "适老协助单人间", 9800),
            "2栋3F-303A": ("2号楼·颐养协助生活区", "3F", "303", "适老协助双人间A床", 8600),
            "2栋3F-305A": ("2号楼·颐养协助生活区", "3F", "305", "适老协助单人间", 9800),
            "3栋4F-401A": ("3号楼·静心认知症单元", "4F", "401", "记忆照护专属单人间", 12800),
            "3栋4F-402A": ("3号楼·静心认知症单元", "4F", "402", "记忆照护专属单人间", 12800),
            "3栋4F-403A": ("3号楼·静心认知症单元", "4F", "403", "全护理医疗电动床位", 11600),
            "3栋4F-405A": ("3号楼·静心认知症单元", "4F", "405", "专业护理单人间", 10800),
        }
        for rid, p in personas.items():
            bid = p["bed"]
            bldg, flr, rm, rtype, price = bed_meta[bid]
            ccrc_beds.append({
                "id": bid, "label": f"{bid}（{rtype}）", "status": "occupied", "resident_id": rid,
                "building": bldg, "floor": flr, "room": rm, "room_type": rtype,
                "source": "栖云园区一期房态台账", "price_cents": price * 100, "price_source": "2026秋季公示价目表",
            })
        extra_beds = [
            ("1栋2F-208A", "1号楼·康养活力公寓", "2F", "208", "南向景观活力套房", 9200, "available", "", ""),
            ("2栋3F-308A", "2号楼·颐养协助生活区", "3F", "308", "适老无障碍单人间", 9800, "available", "", ""),
            ("3栋4F-406A", "3号楼·静心认知症单元", "4F", "406", "记忆照护专属单人间", 12800, "available", "", ""),
            ("5栋1F-108A", "5号楼·乐养旅居公寓", "1F", "108", "乐养旅居双人房", 6600, "unavailable", "恒温除湿新风季度深度维保", "工程部周检计划"),
        ]
        for bid, bldg, flr, rm, rtype, price, st, u_reason, u_src in extra_beds:
            item = {
                "id": bid, "label": f"{bid}（{rtype}）", "status": st, "resident_id": "",
                "building": bldg, "floor": flr, "room": rm, "room_type": rtype,
                "source": "栖云园区一期房态台账", "price_cents": price * 100, "price_source": "2026秋季公示价目表",
            }
            if st == "unavailable":
                item.update(unavailable_reason=u_reason, unavailable_source=u_src, expected_available_at=f"{three_days_later_s}T18:00")
            ccrc_beds.append(item)
        state["beds"] = ccrc_beds
        base_ws["department_demo"] = state

        ws = base_ws
        day_start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=tz)
        t_inq = (day_start - timedelta(days=3)).replace(hour=18, minute=0).isoformat(timespec="seconds")
        t_visit = (day_start - timedelta(days=2)).replace(hour=18, minute=0).isoformat(timespec="seconds")
        t_yest = (day_start - timedelta(days=1)).replace(hour=19, minute=0).isoformat(timespec="seconds")
        t_morn = now.replace(hour=8, minute=20, second=0)
        if t_morn > now:
            t_morn = max(day_start + timedelta(minutes=1), now - timedelta(minutes=15))
        t_morn_s = t_morn.isoformat(timespec="seconds")
        t_now_s = now.isoformat(timespec="seconds")

        def run_cmd(kind: str, action: str, values: dict | None = None, rec_id: str = "", who_user: dict = admin_user, at_s: str = t_now_s):
            nonlocal ws
            body = {"request_id": f"SEED-{uuid4().hex[:16]}", "kind": kind, "action": action, "id": rec_id, "values": values or {}}
            ws, out = apply_command(ws, body, actor_str(who_user), at_s, FACILITY_ID, user=who_user)
            return out

        # 1. Sales: Family contact Chen Min bound to couple R005 + R001, plus verified/unverified fees
        c_chen = run_cmd("sales", "contact_create", {"name": "陈敏（长女）", "phone": "13800138101"}, at_s=t_inq)["record"]
        run_cmd("sales", "relation_create", {"resident_id": "R005", "contact_id": c_chen["id"], "relationship": "长女", "is_primary": True, "is_fee_contact": True}, at_s=t_inq)
        run_cmd("sales", "relation_create", {"resident_id": "R001", "contact_id": c_chen["id"], "relationship": "长女", "is_primary": True, "is_fee_contact": True}, at_s=t_inq)
        run_cmd("sales", "preference_create", {"resident_id": "R005", "source": "老人本人", "category": "生活", "content": "午休13:00至14:30关灯静卧，请勿敲门测温"}, at_s=t_inq)
        run_cmd("sales", "preference_create", {"resident_id": "R005", "source": "家属", "contact_id": c_chen["id"], "category": "饮食", "content": "父亲高血压合并膝关节术后，汤品请控盐并去油"}, at_s=t_inq)

        # Charges & payments for R005 (颐养协助区陈伯钧) — demonstrates confirmed vs customer_reported & advance vs deposit!
        run_cmd("sales", "charge_create", {"resident_id": "R005", "period": month_s, "amount": "9800.00", "description": "颐养协助单人间床位与基础照护月费"}, at_s=t_yest)
        run_cmd("sales", "charge_create", {"resident_id": "R005", "period": month_s, "amount": "1800.00", "description": "适老定制三餐与水疗运动增值包"}, at_s=t_yest)
        p_dep = run_cmd("sales", "payment_report", {"resident_id": "R005", "amount": "50000.00", "category": "deposit", "reference": "入住履约医疗备用押金（合同编号TB-NJ-20260615）"}, at_s=t_yest)["record"]
        run_cmd("sales", "payment_confirm", {"confirmation_ref": "财务收据RC-20260615-01（押金独立专户，不冲抵月费）"}, rec_id=p_dep["id"], at_s=t_yest)
        p_adv = run_cmd("sales", "payment_report", {"resident_id": "R005", "amount": "30000.00", "category": "advance", "reference": "家属银行转账预交养老服务款"}, at_s=t_yest)["record"]
        run_cmd("sales", "payment_confirm", {"confirmation_ref": "园区财务银企直联流水202611-8802"}, rec_id=p_adv["id"], at_s=t_yest)
        run_cmd("sales", "apply_advance", {"resident_id": "R005", "period": month_s, "amount": "9800.00", "reference": f"{month_s}月度床位与照护费显式抵扣单"}, at_s=t_yest)
        # Unverified customer_reported payment (must NOT appear as settled in Family Portal!)
        run_cmd("sales", "payment_report", {"resident_id": "R005", "amount": "1800.00", "category": "period", "period": month_s, "reference": "家属口头告知昨晚手机银行补缴餐费，待财务核对回单"}, at_s=t_now_s)

        # Charges & payments for R001 (康养区林素华)
        run_cmd("sales", "charge_create", {"resident_id": "R001", "period": month_s, "amount": "7800.00", "description": "康养活力单人间月度居住与管家服务费"}, at_s=t_yest)
        p_r1 = run_cmd("sales", "payment_report", {"resident_id": "R001", "amount": "7800.00", "category": "period", "period": month_s, "reference": "尊享入住资格函权益月结"}, at_s=t_yest)["record"]
        run_cmd("sales", "payment_confirm", {"confirmation_ref": "财务核销单RC-202611-019"}, rec_id=p_r1["id"], at_s=t_yest)

        # Couple joint visit (1 visit event -> 2 elders)
        inq_at = (day_start - timedelta(days=3)).replace(hour=10, minute=30).isoformat(timespec="minutes")
        visit_at = (day_start - timedelta(days=2)).replace(hour=14, minute=0).isoformat(timespec="minutes")
        yest_assess_at = (day_start - timedelta(days=1)).replace(hour=11, minute=0).isoformat(timespec="minutes")
        inq_a = run_cmd("sales", "inquiry_create", {
            "occurred_at": inq_at, "display_name": "何建国", "sex": "男", "age": 78,
            "current_living": "南京鼓楼自有住宅夫妻同住", "contact_name": "何欣然", "phone": "13900139201",
            "relationship": "独生女", "source": "合作险企权益客户参观预约", "result": "老两口意向同住仙林社区，叔叔需半护理、阿姨自理",
        }, at_s=t_inq)
        inq_b = run_cmd("sales", "inquiry_create", {
            "occurred_at": inq_at, "display_name": "冯淑珍", "sex": "女", "age": 75,
            "current_living": "南京鼓楼自有住宅夫妻同住", "contact_id": inq_a["related"]["contact_id"],
            "relationship": "独生女", "source": "合作险企权益客户参观预约", "result": "与老伴何建国结伴咨询康养活力户型",
        }, at_s=t_inq)
        run_cmd("sales", "visit_create", {
            "resident_ids": [inq_a["related"]["resident_id"], inq_b["related"]["resident_id"]],
            "contact_id": inq_a["related"]["contact_id"], "occurred_at": visit_at,
            "areas": "微缩金陵地下街景、恒温水疗馆、1栋康养样板间与2栋颐养层",
            "concerns": "关心夫妻分楼栋居住后能否同桌就餐及三甲绿通响应速度",
            "result": "老两口对分住不分区模式非常认可，约定周末二访确认房号",
        }, at_s=t_visit)

        # Prospect Liang Huaijin -> Bed lock expiring in ~24 hours (<48h warning!) + Preparation + Nursing Privacy Assessment!
        inq_liang = run_cmd("sales", "inquiry_create", {
            "occurred_at": inq_at, "display_name": "梁怀瑾", "sex": "女", "age": 81,
            "current_living": "南京玄武区独居", "contact_name": "梁思成", "phone": "13900139288",
            "relationship": "长子", "source": "仙林鼓楼医院康复出院转介", "result": "出院后需过渡至颐养协助单人间",
        }, at_s=t_inq)
        lead_liang = next(x for x in ws["department_demo"]["rows"]["leads"] if x.get("resident_id") == inq_liang["related"]["resident_id"])
        run_cmd("sales", "visit_create", {
            "resident_ids": [inq_liang["related"]["resident_id"]], "contact_id": inq_liang["related"]["contact_id"],
            "occurred_at": visit_at, "areas": "2号楼3F颐养单人间、驻院医务室、康复大厅",
            "concerns": "出院后夜间照护响应与防跌倒细节", "result": "选定2栋3F-308A，申请锁床并启动入院护理评估",
        }, at_s=t_visit)
        run_cmd("sales", "stage_change", {"stage": "contacted"}, rec_id=lead_liang["id"], at_s=t_visit)
        run_cmd("sales", "stage_change", {"stage": "visited"}, rec_id=lead_liang["id"], at_s=t_visit)
        run_cmd("sales", "stage_change", {"stage": "intent"}, rec_id=lead_liang["id"], at_s=t_visit)
        run_cmd("sales", "intent_update", {"intent": "高"}, rec_id=lead_liang["id"], at_s=t_visit)
        expires_soon = (now + timedelta(hours=26)).isoformat(timespec="minutes")
        run_cmd("sales", "reserve", {
            "bed_id": "2栋3F-308A", "starts_at": yest_assess_at, "expires_at": expires_soon,
            "reason": "家属筹备出院小结与签约材料，申请锁定2栋3F-308A无障碍单人间（距到期不足48小时）",
        }, rec_id=lead_liang["id"], at_s=t_yest)
        prep_liang = run_cmd("sales", "preparation_create", {
            "planned_move_in_at": f"{three_days_later_s}T10:00",
        }, rec_id=lead_liang["id"], at_s=t_yest)["record"]
        run_cmd("sales", "preparation_update", {"item": "basic", "status": "已完成", "source": "长子提交身份证及出院证复印件"}, rec_id=prep_liang["id"], at_s=t_yest)
        run_cmd("sales", "preparation_update", {"item": "bed", "status": "已完成", "source": "2栋3F-308A已完成电动护理床调试"}, rec_id=prep_liang["id"], at_s=t_yest)
        # Nursing Admission Assessment (sensitive clinical text isolated from Sales!)
        run_cmd("nursing", "admission_assess", {
            "mode": "station", "preparation_id": prep_liang["id"],
            "assessed_at": yest_assess_at, "tool_name": "栖云静心综合照护分级与MMSE初筛表", "tool_version": "2026-V2",
            "source": "仙林鼓楼医院出院记录及护士长现场面访原表#A-1102",
            "conclusion": "【仅护理专业可见】Barthel指数65分，轻度步态不稳伴夜间轻度定向波动，建议按颐养二级协助配置防坠床感应垫与晚间2次巡视，不向销售顾问披露既往抑郁用药史。",
        }, who_user=sup_user, at_s=t_yest)

        # 2. Logistics: Weekly Nutrition Menu + Meals + Cleaning
        menu = run_cmd("menus", "create", {
            "title": f"金陵初冬适老养生周食谱（{week_monday_s}周）",
            "owner": "kitchen", "week_start": week_monday_s,
            "menu": (
                "【早餐】山药红枣小米粥 / 金陵鸭血粉丝清汤面（软烂去辣） / 无糖豆浆 / 蒸南瓜与水煮蛋\n"
                "【午餐】清蒸鲈鱼柳（去刺） / 萝卜炖牛腩（高压软烂） / 蒜蓉西兰花 / 荠菜豆腐羹 / 软米饭与杂粮馒头\n"
                "【午点】桂花山药酪（低糖温热） / 时令蒸苹果\n"
                "【晚餐】虾仁蒸水蛋 / 香菇青菜肉丸汤 / 软炒丝瓜毛豆 / 紫薯燕麦粥\n"
                "【特殊膳食替代】痛风低嘌呤餐（去菌菇肉汤改牛奶鸡蛋蛋白）、认知症防呛咳糊餐、糖尿病控糖定量主食"
            ),
            "change_reason": "营养师与厨师长联合审定初冬温补菜单，增加去刺鱼柳与软烂根茎菜",
            "note": "覆盖康养自助餐厅与颐养/认知症楼层送餐",
        }, at_s=t_yest)["record"]
        run_cmd("menus", "transition", {"action": "publish"}, rec_id=menu["id"], at_s=t_yest)

        meal_lunch = run_cmd("meals", "create", {
            "title": f"{today_s} 全院午餐与痛风/糊餐替代批次", "owner": "kitchen",
            "date": today_s, "meal": "午餐", "menu_id": menu["id"], "note": "2栋3F许世昌低嘌呤餐、3栋4F周文渊去刺软食已单独分装",
        }, at_s=t_morn_s)["record"]
        run_cmd("meals", "transition", {
            "action": "prepare", "result": "中心厨房10:50完成烹制与中心温度留样核验",
            "performed_at": t_morn_s[:16], "performed_source": "本人记录",
        }, rec_id=meal_lunch["id"], at_s=t_now_s)

        run_cmd("cleaning", "create", {
            "title": "恒温水疗馆池畔防滑地胶晨间消杀", "owner": "cleaning",
            "area": "中央会所B1恒温水疗与康复馆", "date": today_s, "frequency": "每日",
        }, at_s=t_morn_s)

        # 3. Activities: 1 completed with real PNG attachment + 2 upcoming published activities
        act_done = run_cmd("activities", "plan_create", {
            "title": "非遗金陵云锦团扇手作沙龙", "owner": "activities",
            "activity_type": "金陵非遗手作", "target_group": "康养自理及颐养轻度协助长者",
            "scheduled_at": f"{yesterday_s}T14:30", "planned_end": f"{yesterday_s}T16:00",
            "location": "微缩金陵地下街景·云锦工坊",
            "purpose": "结合南京本土云锦纹样开展精细手指训练与跨楼栋长者社交",
            "plan_content": "纹样讲解15分钟 → 团扇配色贴金45分钟 → 合影茶歇30分钟",
            "materials": "圆角安全剪、云锦织锦残片、素面团扇15把",
            "safety_plan": "颐养区长者由社工与照护师一对一陪同往返电梯",
        }, at_s=t_visit)["record"]
        run_cmd("activities", "publish", {}, rec_id=act_done["id"], at_s=t_visit)
        run_cmd("activities", "register", {"participants": ["R001", "R002", "R005", "R008", "R009"]}, rec_id=act_done["id"], at_s=t_yest)
        run_cmd("activities", "transition", {"action": "start", "performed_at": f"{yesterday_s}T14:30", "performed_source": "本人记录"}, rec_id=act_done["id"], at_s=t_yest)
        # Note: R008 signed up but did NOT check in — verifies Family Portal only counts actual checked-in elders!
        run_cmd("activities", "checkin", {"attendance": ["R001", "R002", "R005", "R009"]}, rec_id=act_done["id"], at_s=t_yest)
        run_cmd("activities", "complete", {
            "summary": "实到4位长者（林素华、陈伯钧老两口同桌完成并蒂莲团扇，周文渊在社工引导下安静配色40分钟），已留存现场作品照。",
            "performed_at": f"{yesterday_s}T16:05", "performed_source": "本人记录",
        }, rec_id=act_done["id"], at_s=t_yest)

        # Write real PNG attachment for act_done so /api/activities/.../attachments/... & Family Portal serve real image!
        png_bytes = _make_clean_png()
        att_id = uuid4().hex
        att_dir = data_dir / "attachments" / "activities" / act_done["id"]
        att_dir.mkdir(parents=True, exist_ok=True)
        att_path = att_dir / f"{att_id}.png"
        att_path.write_bytes(png_bytes)
        act_row = next(r for r in ws["department_demo"]["rows"]["activities"] if r["id"] == act_done["id"])
        act_row.setdefault("attachments", []).append({
            "attachment_id": att_id, "activity_id": act_done["id"], "filename": "金陵云锦团扇手作现场脱敏实拍.png",
            "mime_type": "image/png", "size": len(png_bytes), "created_at": t_yest,
            "created_by": admin_actor, "storage_name": att_path.name, "sha256": sha256(png_bytes).hexdigest(),
        })

        # Upcoming published activities (open for Elder Portal one-click signup!)
        act_kunqu = run_cmd("activities", "plan_create", {
            "title": "金陵茶社·昆曲《牡丹亭》折子戏赏析会", "owner": "activities",
            "activity_type": "戏曲人文", "target_group": "全院活力与认知症怀旧干预长者",
            "scheduled_at": f"{tomorrow_s}T14:30", "planned_end": f"{tomorrow_s}T15:45",
            "location": "微缩金陵街景·江南贡院茶社戏台",
            "purpose": "以传统昆曲唱段开展怀旧社交与情绪舒缓",
            "plan_content": "名段清唱赏析 → 曲牌互动哼唱 → 桂花茶点交流",
            "materials": "大字曲词折页20份、低糖茶歇", "safety_plan": "轮椅专区预留无障碍通道",
        }, at_s=t_yest)["record"]
        run_cmd("activities", "publish", {}, rec_id=act_kunqu["id"], at_s=t_yest)
        run_cmd("activities", "register", {"participants": ["R009", "R005"]}, rec_id=act_kunqu["id"], at_s=t_yest)

        act_spa = run_cmd("activities", "plan_create", {
            "title": "恒温水疗馆·水中关节减负平衡训练课", "owner": "activities",
            "activity_type": "水疗康体", "target_group": "康养自理及膝关节康复长者（限8人）",
            "scheduled_at": f"{three_days_later_s}T09:30", "planned_end": f"{three_days_later_s}T10:30",
            "location": "中央会所B1恒温水疗池",
            "purpose": "利用34℃恒温水浮力减轻下肢承重，提升平衡肌力",
            "plan_content": "岸上血压筛查10分钟 → 水中扶杆步态训练35分钟 → 淋浴保暖15分钟",
            "materials": "水中浮力带、防滑水疗鞋、浴巾", "safety_plan": "康复师与救生照护员双岗在池监护",
        }, at_s=t_yest)["record"]
        run_cmd("activities", "publish", {}, rec_id=act_spa["id"], at_s=t_yest)
        run_cmd("activities", "register", {"participants": ["R002"]}, rec_id=act_spa["id"], at_s=t_yest)

        # 4. Nursing: Assignments, Health Reminders, Plans, Dual-time Task, Refusal + Explicit Handover, Handled & Medical-Referred Observations
        run_cmd("nursing", "assign", {"mode": "station", "resident_id": "R005", "owner_id": cg_user["id"], "date_from": two_days_ago_s, "shift": "早班"}, who_user=sup_user, at_s=t_yest)
        run_cmd("nursing", "assign", {"mode": "station", "resident_id": "R009", "owner_id": cg_user["id"], "date_from": two_days_ago_s, "shift": "早班"}, who_user=sup_user, at_s=t_yest)
        run_cmd("nursing", "assign", {"mode": "station", "resident_id": "R007", "owner_id": admin_user["id"], "date_from": two_days_ago_s, "shift": "早班"}, who_user=sup_user, at_s=t_yest)

        run_cmd("nursing", "health_confirm", {"mode": "station", "resident_id": "R005", "category": "跌倒风险", "content": "左膝置换术后步态偏慢，进出淋浴间及水疗池须使用助行器并专人扶行", "source": "入院跌倒风险评估表#F-301"}, who_user=sup_user, at_s=t_yest)
        run_cmd("nursing", "health_confirm", {"mode": "station", "resident_id": "R009", "category": "吞咽饮食", "content": "严禁整颗汤圆、年糕及带刺鱼块；午后情绪烦躁时先放昆曲再协助饮水", "source": "认知症个案照护方案V2"}, who_user=sup_user, at_s=t_yest)
        run_cmd("nursing", "health_confirm", {"mode": "station", "resident_id": "R007", "category": "吞咽饮食", "content": "高尿酸痛风史，禁食浓肉汤、动物内脏与海鲜，每日温水不少于1800ml", "source": "家属提供体检报告核对件"}, who_user=sup_user, at_s=t_yest)

        plan_r5 = run_cmd("nursing", "plan_create", {
            "mode": "station", "resident_id": "R005", "start_date": two_days_ago_s,
            "goal": "维持左膝术后步行耐力，保障晨晚血压平稳与洗浴防跌倒安全",
            "source": "多学科团队个案照护会签单#MDT-202611",
            "rules": [
                {"name": "晨间血压脉搏监测与助行器检查", "category": "基础", "frequency": "daily", "weekdays": [], "times": ["08:00"]},
                {"name": "午后温水擦浴与下肢皮肤观察", "category": "基础", "frequency": "daily", "weekdays": [], "times": ["13:30"]},
                {"name": "晚间足部温水泡洗与防滑夜灯巡查", "category": "基础", "frequency": "daily", "weekdays": [], "times": ["20:00"]},
            ],
        }, who_user=sup_user, at_s=t_yest)["record"]

        plan_r9 = run_cmd("nursing", "plan_create", {
            "mode": "station", "resident_id": "R009", "start_date": two_days_ago_s,
            "goal": "静心非药物节律照护：规律日间定向引导，减少黄昏焦躁与走失风险",
            "source": "静心认知症专护组月度审定方案",
            "rules": [
                {"name": "晨间温和唤醒、穿衣引导与去刺早餐陪护", "category": "基础", "frequency": "daily", "weekdays": [], "times": ["08:15"]},
                {"name": "黄昏昆曲怀旧安抚与防走失巡视", "category": "基础", "frequency": "daily", "weekdays": [], "times": ["16:30"]},
            ],
        }, who_user=sup_user, at_s=t_yest)["record"]

        # Materialize today's tasks for R005: 1 dual-time completed, 1 resident refusal -> generates unreceived handover!
        from careops.nursing_workbench import nursing_state, planned_tasks
        cur_state = dept_seed(ws)
        today_tasks = planned_tasks(ws, cur_state, nursing_state(cur_state), FACILITY_ID, today_s)
        r5_morn = next(t for t in today_tasks if t["resident_id"] == "R005" and t["slot"] == "08:00")
        r5_noon = next(t for t in today_tasks if t["resident_id"] == "R005" and t["slot"] == "13:30")
        run_cmd("nursing", "task_complete", {
            "mode": "caregiver", "date": today_s,
            "performed_at": t_morn_s[:16], "performed_source": "本人记录",
        }, rec_id=r5_morn["id"], who_user=cg_user, at_s=t_now_s)
        run_cmd("nursing", "task_incomplete", {
            "mode": "caregiver", "date": today_s,
            "reason": "老人拒绝", "next_step": "下一班跟进",
            "next_action": "陈伯钧教授正午休阅读，表示希望改到16:30水疗结束后再擦浴，请晚班接力勿强行催促",
            "receiver_id": sup_user["id"], "shift": "晚班",
        }, rec_id=r5_noon["id"], who_user=cg_user, at_s=t_now_s)

        # Observations: 1 closed-loop handled (for Family Portal) + 1 referred to Medical Office (for Director & Medical Office)
        obs_r5 = run_cmd("nursing", "observation_create", {
            "mode": "caregiver", "resident_id": "R005", "type": "行动变化",
            "description": "水疗训练后自述左膝轻微酸胀，步态平稳无红肿擦伤",
            "occurred_at": f"{yesterday_s}T16:10",
        }, who_user=cg_user, at_s=t_yest)["record"]
        run_cmd("nursing", "observation_handle", {
            "mode": "station", "status": "handled",
            "note": "责任护士与康复师联合查体无关节积液，已调低次日水中阻力档位并增加热敷15分钟，长者体感舒适。",
        }, rec_id=obs_r5["id"], who_user=sup_user, at_s=t_yest)

        obs_r9 = run_cmd("nursing", "observation_create", {
            "mode": "caregiver", "resident_id": "R009", "type": "睡眠异常",
            "description": "凌晨02:30至04:10在走廊反复踱步寻找旧书房，晨起精神略显疲倦，进食减少约三分之一",
            "occurred_at": t_morn_s[:16],
        }, who_user=cg_user, at_s=t_now_s)["record"]
        run_cmd("nursing", "refer_medical", {
            "mode": "station",
            "summary": "认知症单元401A周文渊夜间睡眠觉醒节律波动伴晨间食欲下降，请驻院医生巡诊评估是否需复核晚间用药时间（仅传症状摘要，不含护理量表全文）。",
        }, rec_id=obs_r9["id"], who_user=sup_user, at_s=t_now_s)

        # Concierge request from R001 Lin Suhua + linked room repair
        conc = run_cmd("concierge", "create", {
            "title": "康养1栋201A阳台感应小夜灯偶发闪烁报修", "owner": "concierge",
            "resident_id": "R001", "date": today_s,
            "request": "林素华阿姨反映起夜时玄关感应地灯偶有闪烁，希望日间她去茶社活动时入室检修。",
            "contact": "生活管家小周已当面核实，约定14:30-16:00入室",
        }, at_s=t_morn_s)["record"]
        run_cmd("concierge", "link_repair", {
            "fault": "1栋201A玄关红外感应地灯接触不良闪烁（预约14:30-16:00入室，不含长者健康隐私）",
            "equipment_id": "LIGHT-1B-201",
        }, rec_id=conc["id"], at_s=t_now_s)

        # 5. Rehab (水疗与运动复评) & Medical (仙林三甲绿通随访)
        reh_done = run_cmd("rehab", "create", {
            "title": "恒温水疗池下肢减重步态与关节活动度训练", "owner": "rehab",
            "resident_id": "R005", "scheduled_at": f"{yesterday_s}T15:00",
            "plan": "康复治疗师已确认方案：34℃恒温水疗池扶杆行走20分钟 + 股四头肌等长收缩训练2组",
        }, at_s=t_visit)["record"]
        run_cmd("rehab", "transition", {
            "action": "complete", "training": "全程完成20分钟水中减重步行，心率维持82-90次/分，步幅较上周更稳。",
            "performed_at": f"{yesterday_s}T15:40", "performed_source": "本人记录",
        }, rec_id=reh_done["id"], at_s=t_yest)
        run_cmd("rehab", "transition", {
            "action": "review", "review": "阶段复评（来源：康复科周评记录#RH-112）：TUG起立行走测试缩短1.8秒，维持每周三次水疗频次。",
            "performed_at": f"{yesterday_s}T16:30", "performed_source": "本人记录",
        }, rec_id=reh_done["id"], at_s=t_yest)

        run_cmd("rehab", "create", {
            "title": "恒温水疗平衡核心训练（预约排程）", "owner": "rehab",
            "resident_id": "R002", "scheduled_at": f"{tomorrow_s}T10:00",
            "plan": "水中平衡垫站立与上肢划水阻抗练习30分钟",
        }, at_s=t_now_s)
        run_cmd("rehab", "create", {
            "title": "静心认知症多感官音乐节律律动训练", "owner": "rehab",
            "resident_id": "R009", "scheduled_at": f"{today_s}T15:30",
            "plan": "结合江南丝竹节拍开展坐式上肢协调拍击训练25分钟",
        }, at_s=t_morn_s)

        med_green = run_cmd("medical", "create", {
            "title": "仙林三甲绿通外院复诊状态跟踪（保留床位）", "owner": "medical",
            "resident_id": "R006", "source": "驻院医务室绿通转诊单#GC-202611",
            "referral": "经社区绿色通道由家属及医务专员陪同前往江苏省中医院紫东院区心内科短期住院调药；2栋3F-302A床位设为【外院保留】，严禁自动退住。",
            "note": "仅记录院际协同状态与护理交接事项，不替代三甲医院正式电子病历。",
        }, at_s=t_yest)["record"]
        run_cmd("medical", "transition", {
            "action": "handle", "handling": "已与紫东院区管床医生电话核对预计周四返院，通知护士站提前备好返院生命体征交接表。",
            "performed_at": t_morn_s[:16], "performed_source": "本人记录",
        }, rec_id=med_green["id"], at_s=t_now_s)
        run_cmd("medical", "transition", {
            "action": "followup", "followup": "今日上午随访家属周海涛：长者指标平稳，明日确认出院小结复印件带回存档。",
            "performed_at": t_now_s[:16], "performed_source": "本人记录",
        }, rec_id=med_green["id"], at_s=t_now_s)

        # Validate complete workspace against preserved Node v0.3 rule engine!
        validation = engine("validate", workspace=ws)
        errors = validation if isinstance(validation, list) else validation.get("errors", [])
        if errors:
            raise RuntimeError(f"种子工作区未通过规则校验：{errors}")

        c.execute(workspaces.insert().values(facility_id=FACILITY_ID, revision=1, payload=ws, updated_at=utcnow()))
        log(c, admin_user, "facility_initialized", FACILITY_ID, summary={"synthetic_demo": True, "preset": "taibao_ccrc_v1", "initial_users": len(created_users)})

        cred_file = data_dir / f"INITIAL_CREDENTIALS_{FACILITY_ID}.txt"
        fd = os.open(cred_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    marker.write_text("TAIBAO_CCRC_SYNTHETIC_DEMO=1\n", encoding="utf-8")
    return {
        "initialized": True,
        "facility": {"id": FACILITY_ID, "name": FACILITY_NAME, "timezone": TIMEZONE},
        "credentials_file": str(data_dir / f"INITIAL_CREDENTIALS_{FACILITY_ID}.txt"),
        "admin": {"username": "admin", "password": created_users["admin"]["password"]},
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="初始化栖云颐养·金陵国际康养社区合成演示库")
    p.add_argument("--data-dir", type=Path, default=ROOT.parent / "runtime-taibao")
    res = initialize_taibao_demo(p.parse_args().data_dir)
    print(json.dumps({k: v for k, v in res.items() if k != "admin"}, ensure_ascii=False, indent=2))
