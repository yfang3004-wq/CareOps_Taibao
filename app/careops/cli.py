"""Local administration. Never touches the user's old CareOps directory or Git."""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from sqlalchemy import select
from .app import Settings
from .database import Database, facilities, workspaces, users, utcnow
from .bridge import engine, ROOT
from .security import create_user, log


def main():
    p=argparse.ArgumentParser(description="CareOps 本地服务初始化")
    p.add_argument("action",choices=["init","check"])
    p.add_argument("--facility",default="DEMO-01")
    p.add_argument("--name",default="CareOps 合成试用机构")
    p.add_argument("--timezone",default="Asia/Shanghai")
    p.add_argument("--demo",action="store_true",help="仅首次初始化时载入全合成 v0.3 工作区")
    args=p.parse_args()
    settings=Settings.from_env();db=Database(settings.database_url);db.initialize()
    # Fail early, before creating accounts, when the preserved rules cannot run.
    empty=engine("empty")
    from zoneinfo import ZoneInfo
    ZoneInfo(args.timezone)
    if args.action=="check":
        with db.read() as c:
            print(json.dumps({"database":db.engine.dialect.name,"node_rules":"OK","facilities":len(c.execute(select(facilities.c.id)).all()),"production_ready":False}))
        return
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",args.facility):raise SystemExit("机构编号格式无效")
    data=Path(os.getenv("CAREOPS_DATA_DIR",str(ROOT/"data"))).expanduser().resolve()
    credential_file=data/("INITIAL_CREDENTIALS_"+args.facility+".txt")
    if credential_file.exists():
        with db.read() as c:exists=c.execute(select(facilities.c.id).where(facilities.c.id==args.facility)).first()
        if not exists:raise SystemExit("已有同名凭据文件但数据库没有对应机构；保留现场，勿自动覆盖。请使用独立数据目录")
    with db.write() as c:
        if c.execute(select(facilities.c.id).where(facilities.c.id==args.facility)).first():
            print("已有机构：保持原数据、账户与凭据不变。");return
        c.execute(facilities.insert().values(id=args.facility,name=args.name,timezone=args.timezone))
        workspace=json.loads((ROOT/"fixtures/v03-synthetic-workspace.json").read_text()) if args.demo else empty
        if args.demo and args.facility!="DEMO-01":raise SystemExit("固定演示只对应DEMO-01，不能无提示改写历史机构编号")
        c.execute(workspaces.insert().values(facility_id=args.facility,revision=0,payload=workspace,updated_at=utcnow()))
        lines=["CareOps 本地初始账户（随机生成，请妥善保管；勿上传此文件）", "机构："+args.facility,"仅用于合成数据试用；管理员不是临床授权证明。", ""]
        specs=[("admin","机构管理员","admin",["*"]), ("supervisor","复核主管","supervisor",["*"]), ("caregiver","照护员工","caregiver",["2F","3F","4F"]), ("maintenance","后勤员工","maintenance",[]), ("viewer","只读观察员","viewer",["2F"])]
        if not args.demo:specs=specs[:1]
        prefix="" if args.facility=="DEMO-01" else args.facility.lower()+"."
        created=[]
        for username,name,role,units in specs:
            password=secrets.token_urlsafe(18)
            u=create_user(c,args.facility,prefix+username,name,password,role,units);created.append(u)
            lines += ["账号："+u["username"],"密码："+password,"角色："+role,""]
        log(c,created[0],"facility_initialized",args.facility,summary={"synthetic_demo":args.demo,"initial_users":len(created)})
        # O_EXCL refuses to overwrite existing credentials.
        fd=os.open(credential_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:f.write("\n".join(lines))
    print("初始化完成。随机初始账户保存在："+str(credential_file))
    print("后续修改密码会撤销旧会话；此文件不会自动更新。")

if __name__=="__main__":main()
