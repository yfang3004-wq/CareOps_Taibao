"""Opaque server sessions, Argon2id, explicit permissions; no browser-trusted roles."""
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request
from sqlalchemy import select, delete, update, func, or_
from .database import users, sessions, audit, login_attempts, utcnow

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY = HASHER.hash(secrets.token_urlsafe(30))
MAINT_ROLES = {"maint_executor", "maint_supervisor"}
ROLES = {"admin", "supervisor", "caregiver", "maintenance", "viewer"} | MAINT_ROLES
REVIEWERS = {"admin", "supervisor"}
ROLE_LABELS = {"admin":"机构管理员", "supervisor":"主管", "caregiver":"照护人员", "maintenance":"后勤人员", "viewer":"只读观察员", "maint_executor":"维修执行员", "maint_supervisor":"维修主管"}
COOKIE = "careops_session"

def ident(): return str(uuid4())
def sha(value: str): return hashlib.sha256(value.encode("utf-8")).hexdigest()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False)
def digest(value): return sha(canonical(value))
def fail(code: int, message: str): raise HTTPException(code, message)
def actor(user): return "account:"+user["id"]

def password_ok(password: str):
    if not isinstance(password,str) or not 12 <= len(password) <= 128:
        fail(422, "密码须为12至128个字符；请勿使用已有重要账户密码")

def verify(password, hashed):
    try: return HASHER.verify(hashed, password)
    except (VerificationError, InvalidHashError): return False

def public_user(user):
    return {k:user[k] for k in ("id","username","display_name","facility_id","role","units","active")}

def log(c, user, action, entity_id="", request_id="", outcome="ok", summary=None, facility_id=None):
    c.execute(audit.insert().values(id=ident(), facility_id=facility_id or user["facility_id"],
        user_id=user["id"], action=action, entity_id=entity_id, outcome=outcome,
        at=utcnow(), request_id=request_id, summary=summary or {}))

def csrf_for(token): return hmac.new(token.encode(), b"careops-csrf-v1", hashlib.sha256).hexdigest()

def authenticate(c, request: Request, writing=False):
    token = request.cookies.get(COOKIE, "")
    if len(token) < 30 or len(token) > 100: fail(401, "请先登录")
    row=c.execute(select(sessions).where(sessions.c.token_hash==sha(token))).mappings().first()
    now=int(time.time())
    if not row or row["expires"]<=now or row["last_seen"]<now-1800:
        fail(401, "登录已失效，请重新登录；未提交表单不会自动保存")
    user=c.execute(select(users).where(users.c.id==row["user_id"])).mappings().first()
    if not user or not user["active"]: fail(401, "登录已失效，请重新登录")
    if writing and not hmac.compare_digest(request.headers.get("X-CSRF-Token",""), csrf_for(token)):
        fail(403, "请求校验失败，请刷新页面")
    return dict(user), token

def login(c, username, password, ip):
    username=username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,60}", username) or not 1 <= len(password) <= 128:
        fail(401, "账号或密码不正确")
    now=int(time.time()); ak=sha(username); ik=sha(ip)
    c.execute(delete(login_attempts).where(login_attempts.c.at<now-900))
    a=c.execute(select(func.count()).select_from(login_attempts).where(login_attempts.c.account_key==ak)).scalar_one()
    b=c.execute(select(func.count()).select_from(login_attempts).where(login_attempts.c.ip_key==ik)).scalar_one()
    if a>=10 or b>=50: return {"error":429,"message":"登录尝试过多，请15分钟后重试"}
    row=c.execute(select(users).where(users.c.username==username)).mappings().first()
    valid=verify(password, row["password_hash"] if row else DUMMY)
    if not row or not valid or not row["active"]:
        c.execute(login_attempts.insert().values(id=ident(), account_key=ak, ip_key=ik, at=now))
        return {"error":401,"message":"账号或密码不正确"}
    user=dict(row)
    c.execute(delete(login_attempts).where(login_attempts.c.account_key==ak))
    token=secrets.token_urlsafe(32)
    c.execute(sessions.insert().values(token_hash=sha(token),user_id=user["id"],created=now,last_seen=now,expires=now+28800))
    log(c,user,"login")
    return {"token":token,"user":public_user(user),"csrf":csrf_for(token)}

def create_user(c, facility_id, username, display_name, password, role, units):
    username=username.strip().lower();display_name=display_name.strip()
    if not re.fullmatch(r"[a-z0-9_.-]{3,60}",username): fail(422,"账号使用3至60位小写字母、数字、点、下划线或连字符")
    if not 1<=len(display_name)<=60:fail(422,"显示名长度须为1至60")
    if role not in ROLES:fail(422,"未知角色")
    if not isinstance(units,list) or len(units)>30 or any(not isinstance(u,str) or not u.strip() or len(u)>80 for u in units):fail(422,"照料区授权格式无效")
    validate_maintenance_units(role, units)
    if role=="admin":units=["*"]
    if role=="maintenance":units=[]
    password_ok(password)
    if c.execute(select(users.c.id).where(users.c.username==username)).first():fail(409,"账号已存在")
    user={"id":ident(),"username":username,"display_name":display_name,"password_hash":HASHER.hash(password),"facility_id":facility_id,"role":role,"units":sorted(set(units)),"active":True,"created_at":utcnow()}
    c.execute(users.insert().values(**user))
    return user


def validate_maintenance_units(role, units):
    # 首批新岗位以精确公共位置白名单授权，不支持通配或空范围。
    if role in MAINT_ROLES and (not units or any(not isinstance(x, str) or not x.strip() or x != x.strip() or "*" in x for x in units)):
        fail(422, "维修岗位须配置非空精确公共位置，禁止通配符")
