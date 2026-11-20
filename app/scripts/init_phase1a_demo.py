#!/usr/bin/env python3
"""仅在新目录建立公共维修合成样板，不导入或覆盖旧数据库。"""
import argparse,os,secrets,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from careops.database import Database,facilities,workspaces,utcnow
from careops.bridge import engine
from careops.security import create_user

def main():
 p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,required=True);args=p.parse_args()
 data=args.data_dir.resolve();dbpath=data/'careops.sqlite3';marker=data/'PHASE1A_SYNTHETIC_ONLY.txt'
 if dbpath.exists():
  if not marker.is_file():raise SystemExit('目标已有数据库且没有本样板标记；保持原件，请选新目录。')
  print('保留既有合成样板，未重新初始化或覆盖。');return
 data.mkdir(parents=True,exist_ok=True);db=Database('sqlite:///'+str(dbpath));db.initialize()
 lines=['CareOps公共维修合成样板随机账号；只保留本机，勿上传。']
 with db.write() as c:
  c.execute(facilities.insert().values(id='PHASE1A-SYN',name='公共维修合成样板机构',timezone='Asia/Shanghai'))
  ws=engine('empty');ws['classification']='纯合成岗位样板，无真实长者或员工资料'
  c.execute(workspaces.insert().values(facility_id='PHASE1A-SYN',payload=ws,revision=0,updated_at=utcnow()))
  for name,label,role,scope in [('repair2','二层维修执行者','maint_executor',['2F公共走廊']),('repair3','三层维修执行者','maint_executor',['3F公共走廊']),('checker2','二层维修主管','maint_supervisor',['2F公共走廊']),('fixtureadmin','合成管理员','admin',['*'])]:
   password=secrets.token_urlsafe(20);create_user(c,'PHASE1A-SYN',name,label,password,role,scope)
   lines+=['账号：'+name,'密码：'+password,'岗位：'+label,'']
 cred=data/'INITIAL_CREDENTIALS.txt';fd=os.open(cred,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write('\n'.join(lines))
 marker.write_text('PHASE1A_SYNTHETIC_ONLY\n')
 db.engine.dispose();print('合成样板已创建；随机账号请查看：'+str(cred))
if __name__=='__main__':main()
