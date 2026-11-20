"""CareOps v0.4 M1: authenticated, durable local workflows. Not production release."""
import json
import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update, delete, func
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .bridge import engine, ROOT
from .database import Database, facilities, workspaces, users, sessions, receipts, previews, audit, utcnow
from .security import (COOKIE, ROLES, REVIEWERS, HASHER, ROLE_LABELS, ident, sha, digest, actor,
                       canonical, fail, public_user, csrf_for, authenticate, login, create_user, log, password_ok, verify)
from . import policy
from .resident_profile import validate_profile
from .drafts import DraftRef, check_ref as check_draft_ref, consume as consume_draft
from .experience import apply_command as experience_command, enrich_command, EXPERIENCE_COMMANDS

@dataclass
class Settings:
    database_url: str
    origin: str = "http://127.0.0.1:8765"
    testing: bool = False
    allow_cloud_demo: bool = False

    def __post_init__(self):
        u=urlparse(self.origin)
        if u.scheme not in {"http","https"} or not u.hostname or u.path or u.query or u.fragment:
            raise ValueError("CAREOPS_ORIGIN须为无路径的完整来源，如http://127.0.0.1:8765")
        if u.hostname not in {"127.0.0.1","localhost","::1"} and not (self.testing or self.allow_cloud_demo):
            raise ValueError("本候选版仅开放本机回环访问；不直接部署公网或局域网")

    @classmethod
    def from_env(cls):
        data=Path(os.getenv("CAREOPS_DATA_DIR", str(ROOT/"data"))).expanduser().resolve()
        data.mkdir(parents=True,exist_ok=True,mode=0o700)
        allow_cloud=os.getenv("CAREOPS_ALLOW_CLOUD_DEMO","").strip().lower() in {"1","true","yes"}
        return cls(
            os.getenv("CAREOPS_DATABASE_URL", "sqlite:///"+str(data/"careops.sqlite3")),
            os.getenv("CAREOPS_ORIGIN","http://127.0.0.1:8765"),
            allow_cloud_demo=allow_cloud,
        )

class Strict(BaseModel): model_config=ConfigDict(extra="forbid")
class LoginBody(Strict):
    username:str=Field(max_length=80)
    password:str=Field(max_length=128)
class PasswordBody(Strict):
    current_password:str=Field(max_length=128)
    new_password:str=Field(min_length=12,max_length=128)
class UserBody(Strict):
    username:str=Field(max_length=60)
    display_name:str=Field(max_length=60)
    password:str=Field(min_length=12,max_length=128)
    role:str
    units:list[str]=Field(default_factory=list,max_length=30)
class UserPatch(Strict):
    role:str
    units:list[str]=Field(max_length=30)
    active:bool
class CommandBody(Strict):
    draft_ref:DraftRef|None=None
    type:str=Field(max_length=40)
    request_id:str=Field(pattern=r"^[A-Za-z0-9_.-]{12,70}$")
    expected_workspace_revision:int=Field(ge=0,strict=True)
    instance_id:str=Field(default="",max_length=80)
    expected_revision:int|None=Field(default=None,ge=0,strict=True)
    data:dict=Field(default_factory=dict)
class RecordBody(Strict):
    draft_ref:DraftRef|None=None
    request_id:str|None=Field(default=None,pattern=r"^[A-Za-z0-9_.-]{12,70}$")
    expected_workspace_revision:int=Field(ge=0,strict=True)
    row:dict
    reason:str=Field(min_length=1,max_length=1000)
class ImportBody(Strict):
    raw_text:str=Field(max_length=5_000_000)
class CommitBody(Strict):
    preview_id:str=Field(max_length=60)
    expected_workspace_revision:int=Field(ge=0,strict=True)
    source_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    confirm:bool

COMMAND_FIELDS={
 "takeover_task":{"handover_id","reason"},
 "share_repair":set("recipient_user_id location equipment fault access_window contact_role reason".split()),
 "revoke_repair":{"reason"},
 "start":set("template_id template_version title description resident_id location equipment_id due_at priority category occurred_at inspection_ref requirement_ref existing_subject_id owner_user_id".split()),
 "start_task":{"task_id"},"add_evidence":{"task_id","kind","value"},
 "submit_task":set("task_id result evidence_kind evidence_value performed_at performed_source".split()),"review_task":{"task_id","decision","reason"},
 "set_task_exception":{"task_id","status","reason"},"waive_task":{"task_id","reason"},
 "create_handover":{"task_id","receiver_user_id","observation","request","shift"},
 "ack_handover":{"handover_id"},"submit_workflow":{"harm","harm_source"},
 "review_workflow":{"decision","reason","task_id"},"reopen_workflow":{"task_id","reason"},
 "publish_template":{"template","reason"},"assign_task":{"task_id","owner_user_id","reason"}
}

def get_ws(c,u):
    row=c.execute(select(workspaces).where(workspaces.c.facility_id==u["facility_id"])).mappings().first()
    if not row:fail(409,"机构工作区尚未初始化")
    return dict(row)

def touch(c,token):
    c.execute(update(sessions).where(sessions.c.token_hash==sha(token)).values(last_seen=int(time.time())))

def require_role(user,roles):
    if user["role"] not in roles:fail(403,"当前账号没有此操作权限")

def has_business(ws):return bool(ws["flow"]["instances"]) or any(ws["tables"].values())

def schema_scope(ws,facility):
    ids={r.get("facility_id") for rows in ws["tables"].values() for r in rows}
    ids|={r.get("facility_id") for r in ws["flow"]["instances"]}
    if ids and ids!={facility}:fail(422,"只接受与当前机构编号一致的单机构文件；不自动改写编号或合并")

def create_app(settings=None):
    settings=settings or Settings.from_env();db=Database(settings.database_url);db.initialize()
    app=FastAPI(title="CareOps Local", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db=db;app.state.settings=settings
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["*"] if settings.allow_cloud_demo else [urlparse(settings.origin).hostname])

    def _is_https_request(request:Request)->bool:
        fwd_proto=request.headers.get("x-forwarded-proto","").split(",")[0].strip().lower()
        return settings.origin.startswith("https://") or fwd_proto=="https" or request.url.scheme=="https"

    def _is_allowed_origin(request:Request)->bool:
        origin=request.headers.get("origin")
        if not origin:return False
        if origin==settings.origin:return True
        if not settings.allow_cloud_demo:return False
        u=urlparse(origin)
        if u.scheme not in {"http","https"} or not u.netloc or u.path or u.query or u.fragment:return False
        req_host=(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
        return bool(req_host) and u.netloc.lower()==req_host

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Never echo password/request input in a validation response.
        return JSONResponse({"detail":"输入格式不正确", "fields":[{"loc":e["loc"],"message":e["msg"]} for e in exc.errors()]},status_code=422)

    @app.middleware("http")
    async def boundary(request:Request,call_next):
        writing=request.method not in {"GET","HEAD","OPTIONS"}
        if request.url.path.startswith("/api/") and writing:
            if not _is_allowed_origin(request):
                return JSONResponse({"detail":"拒绝非同源写入"},status_code=403)
            if request.headers.get("content-type","").split(";")[0]!="application/json":
                return JSONResponse({"detail":"写入须使用application/json"},status_code=415)
            try:length=int(request.headers.get("content-length","0"))
            except ValueError:length=9_000_000
            if length>6_000_000:return JSONResponse({"detail":"请求过大"},status_code=413)
            body=await request.body()
            if len(body)>6_000_000:return JSONResponse({"detail":"请求过大"},status_code=413)
        try:response=await call_next(request)
        except ValueError as exc:response=JSONResponse({"detail":str(exc)[:1000]},status_code=422)
        except RuntimeError as exc:response=JSONResponse({"detail":str(exc)[:500]},status_code=503)
        # Denials are recorded separately, without copying sensitive request bodies.
        if writing and response.status_code>=400 and request.url.path.startswith("/api/") and request.url.path!="/api/auth/login":
            try:
                with db.write() as c:
                    u,t=authenticate(c,request)
                    log(c,u,"request_denied",outcome="denied",summary={"path":request.url.path,"status":response.status_code})
            except Exception:pass
        response.headers.update({"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff",
          "Referrer-Policy":"no-referrer", "X-Frame-Options":"DENY",
          "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.get("/healthz")
    def health():
        with db.read() as c:c.execute(select(func.count()).select_from(workspaces))
        return {"status":"ok","version":__version__,"production_ready":False}

    @app.post("/api/auth/login")
    def login_endpoint(body:LoginBody,request:Request,response:Response):
        with db.write() as c:result=login(c,body.username,body.password,request.client.host if request.client else "unknown")
        if "error" in result:fail(result["error"],result["message"])
        response.set_cookie(COOKIE,result.pop("token"),max_age=28800,httponly=True,samesite="strict",secure=_is_https_request(request),path="/")
        return result

    @app.get("/api/auth/me")
    def me(request:Request):
        # Passive identity polling must not refresh user idle timeout.
        with db.read() as c:
            u,t=authenticate(c,request)
            f=c.execute(select(facilities).where(facilities.c.id==u["facility_id"])).mappings().one()
            return {"user":public_user(u),"csrf":csrf_for(t),"facility":dict(f),"version":__version__,"role_label":ROLE_LABELS[u["role"]]}

    @app.post("/api/auth/logout")
    def logout(request:Request,response:Response):
        with db.write() as c:
            u,t=authenticate(c,request,True);c.execute(delete(sessions).where(sessions.c.token_hash==sha(t)));log(c,u,"logout")
        response.delete_cookie(COOKIE,path="/");return {"ok":True}

    @app.post("/api/auth/password")
    def change_password(body:PasswordBody,request:Request,response:Response):
        with db.write() as c:
            u,t=authenticate(c,request,True)
            if not verify(body.current_password,u["password_hash"]):fail(403,"当前密码不正确")
            password_ok(body.new_password)
            c.execute(update(users).where(users.c.id==u["id"]).values(password_hash=HASHER.hash(body.new_password)))
            c.execute(delete(sessions).where(sessions.c.user_id==u["id"]));log(c,u,"password_changed")
        response.delete_cookie(COOKIE,path="/");return {"ok":True,"login_required":True}

    @app.get("/api/users")
    def list_users(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);require_role(u,{"admin"});touch(c,t)
            return [public_user(x) for x in c.execute(select(users).where(users.c.facility_id==u["facility_id"])).mappings()]

    @app.post("/api/users",status_code=201)
    def add_user(body:UserBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,{"admin"})
            new=create_user(c,u["facility_id"],**body.model_dump());log(c,u,"user_created",new["id"],summary={"role":new["role"],"units":new["units"]});touch(c,t)
            return public_user(new)

    @app.patch("/api/users/{uid}")
    def patch_user(uid:str,body:UserPatch,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,{"admin"})
            target=c.execute(select(users).where(users.c.id==uid,users.c.facility_id==u["facility_id"])).mappings().first()
            if not target:fail(404,"账户不存在")
            if body.role not in ROLES or any(not x.strip() or len(x)>80 for x in body.units):fail(422,"角色或照料区格式无效")
            if target["role"]=="admin" and target["active"] and (body.role!="admin" or not body.active):
                count=c.execute(select(func.count()).select_from(users).where(users.c.facility_id==u["facility_id"],users.c.role=="admin",users.c.active==True)).scalar_one()
                if count<=1:fail(409,"不能停用或降级最后一位机构管理员")
            from .security import validate_maintenance_units
            validate_maintenance_units(body.role, body.units)
            units=["*"] if body.role=="admin" else [] if body.role=="maintenance" else sorted(set(body.units))
            c.execute(update(users).where(users.c.id==uid).values(role=body.role,units=units,active=body.active))
            c.execute(delete(sessions).where(sessions.c.user_id==uid));log(c,u,"user_access_changed",uid,summary={"role":body.role,"units":units,"active":body.active})
            return {"ok":True,"sessions_revoked":True}

    @app.get("/api/directory")
    def directory(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);touch(c,t)
            from .security import MAINT_ROLES
            rows=c.execute(select(users).where(users.c.facility_id==u["facility_id"],users.c.active==True)).mappings()
            if u["role"] in MAINT_ROLES:
                return [{"id":r["id"],"display_name":r["display_name"],"role":r["role"],"units":r["units"]} for r in rows if r["role"] in MAINT_ROLES and set(r["units"]) & set(u["units"])]
            return [{"id":r["id"],"display_name":r["display_name"],"role":r["role"]} for r in rows]

    @app.get("/api/role-workbenches/maintenance")
    def maintenance_workbench(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);require_role(u,{"maint_executor","maint_supervisor"});touch(c,t)
            w=get_ws(c,u);ws=w["payload"]
            return {"revision":w["revision"],"updated_at":w["updated_at"],"locations":u["units"],"allowed_actions":["start"],"items":[policy.detail(u,ws,i) for i in ws["flow"]["instances"] if policy.flow_visible(u,ws,i)]}

    @app.get("/api/workflows")
    def list_flows(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);touch(c,t);w=get_ws(c,u);ws=w["payload"]
            return {"revision":w["revision"],"updated_at":w["updated_at"],"items":[policy.summary(u,ws,i) for i in ws["flow"]["instances"] if policy.flow_visible(u,ws,i)]}

    @app.get("/api/workflows/{wid}")
    def flow_detail(wid:str,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);touch(c,t);w=get_ws(c,u);i=policy.get_flow(u,w["payload"],wid)
            return {"revision":w["revision"],"item":policy.detail(u,w["payload"],i)}

    @app.get("/api/templates")
    def templates(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);touch(c,t);w=get_ws(c,u)
            return [{k:v[k] for k in ("id","version","kind","title","sop_ref")} for v in w["payload"]["flow"]["templates"] if v["kind"] in policy.KINDS[u["role"]]]

    @app.get("/api/records/{table}")
    def records(table:str,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);touch(c,t);w=get_ws(c,u)
            return {"revision":w["revision"],"items":policy.rows_for(u,w["payload"],table),"allowed_fields":policy.field_keys(u,table)}

    @app.post("/api/records/{table}")
    def upsert_record(table:str,body:RecordBody,request:Request):
        if table not in {"residents","stays","assessments","plans"}:fail(403,"此表只能通过流程操作或旧数据导入更新")
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,REVIEWERS);w=get_ws(c,u)
            fields=set(policy.field_keys(u,table))-{"facility_id"}
            if set(body.row)-fields or any(not isinstance(v,(str,int,float)) for v in body.row.values()):fail(422,"包含不允许的字段或值类型")
            row={**body.row,"facility_id":u["facility_id"]};key=policy.IDS[table]
            if not str(row.get(key,"")).strip():fail(422,"记录编号必填")
            old=next((r for r in w["payload"]["tables"][table] if r[key]==row[key]),None)
            if table=="residents":
                row={**(old or {}),**row}
                validate_profile(row)
                if not policy.resident_visible(u,row) or old and not policy.resident_visible(u,old):fail(403,"长者不在授权照料区内")
            else:
                if row.get("resident_id") not in policy.visible_residents(u,w["payload"]):fail(403,"长者不在授权范围")
                if old and old.get("resident_id") not in policy.visible_residents(u,w["payload"]):fail(403,"原记录不在授权范围")
            raw_body=body.model_dump()
            if body.draft_ref is None:raw_body.pop("draft_ref",None)
            request_hash=digest({"table":table,**raw_body})
            if body.request_id:
                rec=c.execute(select(receipts).where(receipts.c.facility_id==u["facility_id"],receipts.c.request_id==body.request_id)).mappings().first()
                if rec:
                    if rec["result"].get("cancelled"):fail(409,"此请求编号已终止；请核对后重新提交")
                    if rec["user_id"]!=u["id"] or rec["digest"]!=request_hash:fail(409,"请求编号已被不同内容使用")
                    touch(c,t);return {**rec["result"],"replayed":True}
            if body.expected_workspace_revision!=w["revision"]:fail(409,"工作区已更新，请刷新后核对再提交")
            draft=check_draft_ref(c,u,w["payload"],body.draft_ref,"record",{"table":table,"record_id":row[key] if old else ""})
            index=w["payload"]["tables"][table].index(old) if old else -1
            new=engine("upsert",workspace=w["payload"],table=table,row=row,index=index,actor=actor(u),reason=body.reason)
            new["audit"][-1]["actor_type"]="authenticated-session"
            new["audit"][-1]["authenticated_user_id"]=u["id"];new["audit"][-1]["authenticated_role"]=u["role"]
            c.execute(update(workspaces).where(workspaces.c.facility_id==u["facility_id"]).values(payload=new,revision=w["revision"]+1,updated_at=utcnow()))
            log(c,u,"record_upsert",row[key],summary={"table":table,"revision":w["revision"]+1});touch(c,t)
            result={"revision":w["revision"]+1,"id":row[key],"table":table,"request_id":body.request_id}
            consume_draft(c,u,draft,result)
            if body.request_id:c.execute(receipts.insert().values(facility_id=u["facility_id"],request_id=body.request_id,user_id=u["id"],digest=request_hash,result=result))
            return result

    @app.post("/api/commands")
    def commands(body:CommandBody,request:Request):
        if body.type not in COMMAND_FIELDS:fail(422,"未开放的操作")
        if set(body.data)-COMMAND_FIELDS[body.type]:fail(422,"包含不允许的命令字段；身份、角色和时间由服务器决定")
        if len(canonical(body.data))>16000:fail(422,"命令内容过长")
        for key,value in body.data.items():
            if key!="template" and (not isinstance(value,str) or len(value)>6000):fail(422,"命令字段必须为限长文本")
        body_dict=body.model_dump()
        if body.draft_ref is None:body_dict.pop("draft_ref",None)
        request_hash=digest(body_dict)
        with db.write() as c:
            u,t=authenticate(c,request,True);w=get_ws(c,u);ws=w["payload"]
            # Check current role and data scope before replay as well as execution.
            policy.authorize_command(u,ws,{**body.data,"type":body.type,"instance_id":body.instance_id})
            rec=c.execute(select(receipts).where(receipts.c.facility_id==u["facility_id"],receipts.c.request_id==body.request_id)).mappings().first()
            if rec:
                if rec["result"].get("cancelled"):fail(409,"此请求编号已终止；请核对后重新提交")
                if rec["user_id"]!=u["id"] or rec["digest"]!=request_hash:fail(409,"请求编号已被不同内容使用")
                touch(c,t);return {**rec["result"],"replayed":True}
            if body.expected_workspace_revision!=w["revision"]:fail(409,"其他操作已更新工作区；请刷新核对，系统不会覆盖较新记录")
            f=c.execute(select(facilities).where(facilities.c.id==u["facility_id"])).mappings().one()
            now=datetime.now(ZoneInfo(f["timezone"])).strftime("%Y-%m-%d %H:%M:%S")
            command={**body.data,"type":body.type,"request_id":body.request_id,"facility_id":u["facility_id"],"actor":actor(u),"role":"主管复核" if u["role"] in REVIEWERS|{"maint_supervisor"} else "执行人员","at":now}
            if body.instance_id:command.update(instance_id=body.instance_id,expected_revision=body.expected_revision)
            def select_target(uid,for_kind=None):
                target=c.execute(select(users).where(users.c.id==uid,users.c.facility_id==u["facility_id"],users.c.active==True)).mappings().first()
                if u["role"] in {"maint_executor","maint_supervisor"} and target and target["role"] not in {"maint_executor","maint_supervisor"}:fail(422,"目标须为本批维修岗位账户")
                if not target or target["role"]=="viewer":fail(422,"目标必须是同机构有效执行账户")
                if body.type=="start":
                    tpl=next(x for x in ws["flow"]["templates"] if x["id"]==body.data["template_id"] and x["version"]==body.data["template_version"])
                    scope={"facility_id":u["facility_id"],"kind":tpl["kind"],"resident_id":body.data.get("resident_id",""),"location":body.data.get("location", "")}
                    if tpl["kind"]=="maintenance" and body.data.get("existing_subject_id"):
                        existing=next((r for r in ws["tables"]["workorders"] if r["workorder_id"]==body.data["existing_subject_id"] and r["facility_id"]==u["facility_id"]),None)
                        if not existing:fail(404,"原维修记录不存在")
                        scope["location"]=existing["location"]
                else:scope=policy.get_flow(u,ws,body.instance_id)
                if not policy.flow_visible(dict(target),ws,scope):fail(422,"目标账户不具备此流程的数据范围")
                return actor(target)
            if body.type=="start":
                owner_id=command.pop("owner_user_id",u["id"])
                if owner_id!=u["id"] and u["role"] not in REVIEWERS|{"maint_supervisor"}:fail(403,"只有主管可以给其他账户分派新流程")
                command["owner"]=select_target(owner_id)
            if body.type=="assign_task":command["owner"]=select_target(command.pop("owner_user_id",""))
            if body.type=="create_handover":command["receiver"]=select_target(command.pop("receiver_user_id",""))
            draft_context={"workflow_kind":next(x["kind"] for x in ws["flow"]["templates"] if x["id"]==body.data["template_id"] and x["version"]==body.data["template_version"]),"template_id":body.data["template_id"],"template_version":body.data["template_version"]} if body.type=="start" else {"type":body.type,"instance_id":body.instance_id}
            draft=check_draft_ref(c,u,ws,body.draft_ref,"flow" if body.type=="start" else "command",draft_context)
            if draft and body.type!="start" and draft["context"].get("task_id") and draft["context"]["task_id"]!=body.data.get("task_id"):fail(422,"草稿与当前任务不符")
            new=experience_command(c,u,ws,command) if body.type in EXPERIENCE_COMMANDS else engine("command",workspace=ws,command=command)
            new=enrich_command(new,ws,command)
            if len(new["audit"])!=len(ws["audit"])+1:fail(409,"旧文件已有同号回执，但没有对应服务端提交记录；请刷新后使用新请求编号")
            new["audit"][-1]["actor_type"]="authenticated-session";new["audit"][-1]["authenticated_user_id"]=u["id"];new["audit"][-1]["authenticated_role"]=u["role"]
            rev=w["revision"]+1
            c.execute(update(workspaces).where(workspaces.c.facility_id==u["facility_id"]).values(payload=new,revision=rev,updated_at=utcnow()))
            entity=new["flow"]["instances"][-1]["id"] if body.type=="start" else body.instance_id
            result={"ok":True,"revision":rev,"instance_id":entity,"request_id":body.request_id}
            consume_draft(c,u,draft,result)
            c.execute(receipts.insert().values(facility_id=u["facility_id"],request_id=body.request_id,user_id=u["id"],digest=request_hash,result=result))
            log(c,u,body.type,entity,body.request_id,summary={"workspace_revision":rev,"before_sha256":digest(ws),"after_sha256":digest(new)})
            touch(c,t);return result

    @app.get("/api/receipts/{rid}")
    def receipt_status(rid:str,request:Request):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{12,70}",rid):fail(422,"请求编号格式错误")
        with db.write() as c:
            u,t=authenticate(c,request)
            rec=c.execute(select(receipts).where(receipts.c.facility_id==u["facility_id"],receipts.c.request_id==rid,receipts.c.user_id==u["id"])).mappings().first()
            if not rec:fail(404,"尚未找到提交回执；不代表旧请求永远不会到达")
            # Only the creator sees minimal outcome metadata, never saved payloads or identities.
            return {"found":True,"cancelled":bool(rec["result"].get("cancelled")),"result":{k:v for k,v in rec["result"].items() if k in {"ok","revision","instance_id","id","table","request_id","cancelled"}}}

    @app.post("/api/receipts/{rid}/cancel")
    def cancel_receipt(rid:str,request:Request):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{12,70}",rid):fail(422,"请求编号格式错误")
        with db.write() as c:
            u,t=authenticate(c,request,True)
            rec=c.execute(select(receipts).where(receipts.c.facility_id==u["facility_id"],receipts.c.request_id==rid)).mappings().first()
            if rec:
                if rec["user_id"]!=u["id"]:fail(404,"无权核对该请求")
                return {"cancelled":bool(rec["result"].get("cancelled")),"already_committed":not bool(rec["result"].get("cancelled")),"result":rec["result"]}
            result={"ok":False,"cancelled":True,"request_id":rid}
            c.execute(receipts.insert().values(facility_id=u["facility_id"],request_id=rid,user_id=u["id"],digest=sha("cancelled"),result=result))
            log(c,u,"request_fenced",request_id=rid,summary={"cancelled_before_commit":True});touch(c,t)
            return {"cancelled":True,"already_committed":False,"result":result}

    @app.post("/api/import/preview")
    def import_preview(body:ImportBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,{"admin"});w=get_ws(c,u)
            if has_business(w["payload"]):fail(409,"只允许导入空机构工作区；不覆盖现有记录。请保留备份，另建独立试用库")
            try:raw=json.loads(body.raw_text)
            except (json.JSONDecodeError,RecursionError):fail(422,"JSON文件格式无效")
            if not isinstance(raw,dict):fail(422,"工作区须为对象")
            source_version=raw.get("version")
            if raw.get("format")=="careops-server-export":
                if source_version!=4:fail(422,"不支持此服务端备份版本")
                raw=raw.get("workspace")
            normalized=engine("restore",workspace=raw)
            # Imported briefs/delegations are history, never live grants to local accounts.
            for inst in normalized["flow"]["instances"]:
                if "repair_access" in inst:inst["imported_repair_access_unverified"]=inst.pop("repair_access")
            for link in normalized["flow"]["handover_links"]:
                if "cycle" in link:link["imported_delegation_unverified"]={k:link.pop(k) for k in ("cycle","assignment_epoch") if k in link}
            schema_scope(normalized,u["facility_id"])
            issues=engine("validate",workspace=normalized)
            report={"source_version":source_version,"workspace_version":3,"counts":{k:len(v) for k,v in normalized["tables"].items()},"flows":len(normalized["flow"]["instances"]),"errors":sum(x["level"]=="error" for x in issues),"warnings":sum(x["level"]=="warning" for x in issues),"legacy_identity":"UNVERIFIED_PRESERVED","note":"错误行保留供核对，不自动补造历史审批；先预览再确认"}
            preview_id=ident();hash_=sha(body.raw_text)
            # Pending, expired previews contain potentially sensitive raw files; remove them, not committed provenance.
            c.execute(delete(previews).where(previews.c.committed==False,previews.c.expires<int(time.time())))
            c.execute(previews.insert().values(id=preview_id,facility_id=u["facility_id"],user_id=u["id"],digest=hash_,raw_text=body.raw_text,normalized=normalized,report=report,expires=int(time.time())+1800,committed=False))
            log(c,u,"import_preview",preview_id,summary={"sha256":hash_,"source_version":source_version});touch(c,t)
            return {"preview_id":preview_id,"sha256":hash_,"revision":w["revision"],"report":report}

    @app.post("/api/import/commit")
    def import_commit(body:CommitBody,request:Request):
        with db.write() as c:
            u,t=authenticate(c,request,True);require_role(u,{"admin"});w=get_ws(c,u)
            p=c.execute(select(previews).where(previews.c.id==body.preview_id,previews.c.facility_id==u["facility_id"],previews.c.user_id==u["id"])).mappings().first()
            if not p or p["normalized"].get("format")!="careops-workspace":fail(404,"JSON工作区预览不存在")
            if not body.confirm:fail(422,"尚未确认导入")
            if p["committed"]:fail(409,"此预览已导入，不重复执行")
            if p["expires"]<int(time.time()):fail(409,"预览已过期，请重新预览")
            if p["digest"]!=body.source_sha256:fail(409,"来源摘要不一致")
            if body.expected_workspace_revision!=w["revision"] or has_business(w["payload"]):fail(409,"目标已变化或非空；拒绝覆盖")
            rev=w["revision"]+1
            c.execute(update(workspaces).where(workspaces.c.facility_id==u["facility_id"]).values(payload=p["normalized"],revision=rev,updated_at=utcnow()))
            c.execute(update(previews).where(previews.c.id==p["id"]).values(committed=True))
            log(c,u,"import_committed",p["id"],summary={"sha256":p["digest"],"counts":p["report"]["counts"],"revision":rev,"historical_identity":"unverified"});touch(c,t)
            return {"ok":True,"revision":rev,"report":p["report"]}

    @app.get("/api/export")
    def export(request:Request):
        with db.write() as c:
            u,t=authenticate(c,request);require_role(u,{"admin"});w=get_ws(c,u);touch(c,t)
            log(c,u,"workspace_export",summary={"revision":w["revision"]})
            result={"format":"careops-server-export","version":4,"workspace_schema":3,"facility_id":u["facility_id"],"workspace_revision":w["revision"],"exported_at":utcnow(),"workspace":w["payload"],"server_audit":[dict(x) for x in c.execute(select(audit).where(audit.c.facility_id==u["facility_id"]).order_by(audit.c.at)).mappings()],"limitations":"不包含账户、密码、会话或真实附件；重新导入的历史不视为新系统认证审计"}
            return JSONResponse(result,headers={"Content-Disposition":'attachment; filename="CareOps_v04_workspace.json"'})

    @app.get("/api/audit")
    def audit_list(request:Request,limit:int=100):
        with db.write() as c:
            u,t=authenticate(c,request);require_role(u,REVIEWERS);touch(c,t)
            # Scoped supervisors cannot inspect institution-wide metadata; admin and unrestricted supervisor only.
            if "*" not in u["units"]:fail(403,"全机构审计只开放给全范围主管与机构管理员")
            return [dict(x) for x in c.execute(select(audit).where(audit.c.facility_id==u["facility_id"]).order_by(audit.c.at.desc()).limit(max(1,min(limit,300)))).mappings()]

    from .tabular_api import install as install_tabular
    install_tabular(app,db,get_ws,touch,require_role)

    from .experience import install as install_experience
    from .drafts import install as install_drafts
    install_experience(app,db,get_ws,touch)
    install_drafts(app,db,get_ws,touch)

    from .departments import install_departments
    install_departments(app,db,get_ws,touch)
    from .activity_workbench import install as install_activities
    install_activities(app,db,get_ws,touch)
    from .sales_workbench import install as install_sales
    install_sales(app,db,get_ws,touch)
    from .nursing_workbench import install as install_nursing
    install_nursing(app,db,get_ws,touch)
    from .portal import install_portal
    install_portal(app,db,get_ws,touch)
    from .director_overview import install_director_and_demo_auth
    install_director_and_demo_auth(app,db,get_ws,touch,settings)

    app.mount("/assets",StaticFiles(directory=ROOT/"web"),name="assets")
    @app.get("/")
    def index():return FileResponse(ROOT/"web/index.html")
    return app
