#!/usr/bin/env python3
"""Original M2 code -> explicit M3 migration -> new-path restore, synthetic only.
Requires the preserved M2 ZIP, not a running/user database. No credentials reported.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,sqlite3,subprocess,sys,tempfile,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from migrate_m3 import migrate
from prepare_upgrade import prepare,inspect_database
from restore_sqlite import restore
from careops.app import Settings,create_app
from fastapi.testclient import TestClient
M2_SHA='04ca53cca0a564679f18ac85213be78d7040104ac225e44ff03c18cb3bb2e5e1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def state(p):
    with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as c:
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old=sorted(names-{'schema_versions','user_drafts'})
        result={n:hashlib.sha256(json.dumps(c.execute('SELECT * FROM "'+n+'" ORDER BY 1').fetchall(),sort_keys=True).encode()).hexdigest() for n in old}
        ws=json.loads(c.execute('SELECT payload FROM workspaces ORDER BY facility_id').fetchone()[0])
        return result,ws,names,c.execute('SELECT version FROM schema_versions').fetchall()
def main(source,out):
    checks=[]
    def ck(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'status':'PASS'})
    ck('Preserved original M2 ZIP identity matches',sha(source)==M2_SHA)
    with tempfile.TemporaryDirectory(prefix='careops-m2-m3-check-') as tmp:
        base=Path(tmp);oldroot=base/'old'
        with zipfile.ZipFile(source) as z:
            for n in z.namelist():
                p=Path(n)
                if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive path')
            z.extractall(oldroot)
        oldroot=next(p.parent.parent for p in oldroot.rglob('careops/__init__.py'))
        data=base/'data';env={**os.environ,'CAREOPS_DATA_DIR':str(data),'CAREOPS_ORIGIN':'http://127.0.0.1:8765'}
        env.pop('CAREOPS_DATABASE_URL',None);env.pop('PYTHONPATH',None)
        run=lambda root:subprocess.run([sys.executable,'-m','careops.cli','init','--demo'],cwd=root,env=env,capture_output=True,text=True)
        r=run(oldroot);ck('Original M2 CLI creates isolated synthetic v1 database',r.returncode==0)
        p=data/'careops.sqlite3';cp=data/'INITIAL_CREDENTIALS_DEMO-01.txt';ch=sha(cp)
        before,ws,names,versions=state(p);closed=next(i['id'] for i in ws['flow']['instances'] if i['status']=='closed')
        ck('Baseline contains schema1, five accounts and eleven synthetic flows',versions==[(1,)] and inspect_database(p)['accounts']==5 and len(ws['flow']['instances'])==11)
        r=run(ROOT);ck('M3 refuses implicit schema1 initialization',r.returncode!=0)
        ck('Refused implicit migration leaves all legacy table contents intact',state(p)[0]==before and state(p)[3]==[(1,)])
        migration=migrate(p);snapshot=Path(migration['snapshot'])
        ck('Explicit additive migration succeeds only with verified schema1 snapshot',migration['migration']=='SCHEMA_1_TO_2' and sha(snapshot)==migration['snapshot_sha256'] and state(snapshot)[3]==[(1,)])
        after,_,newnames,v=state(p)
        ck('All old tables INCLUDING accounts/passwords/receipts/audit preserved exactly',after==before and sha(cp)==ch)
        ck('Only new draft table and schema2 metadata added',newnames-names=={'user_drafts'} and v==[(2,)])
        ck('Snapshot is the complete pre-migration legacy table state',state(snapshot)[0]==before)
        ck('First schema2 checkpoint and subsequent repeat are bounded/idempotent',migrate(p)['migration']=='ALREADY_SCHEMA_2' and migrate(p)['status']=='ALREADY_CHECKED')
        r=run(ROOT);ck('M3 CLI accepts existing migrated data without reseeding',r.returncode==0 and '保持原数据' in r.stdout)
        ck('CLI re-entry preserves account/workspace and original credential file',state(p)[0]==before and sha(cp)==ch)
        r=run(oldroot);ck('Old M2 code refuses schema2 rather than downgrading',r.returncode!=0 and state(p)[0]==before and state(p)[3]==[(2,)])
        creds=dict(re.findall(r'账号：([^\n]+)\n密码：([^\n]+)',cp.read_text()))
        app=create_app(Settings('sqlite:///'+str(p),'http://testserver',testing=True))
        with TestClient(app) as c:
            c.headers['Origin']='http://testserver'
            r=c.post('/api/auth/login',json={'username':'admin','password':creds['admin']});ck('Old random account logs in after migration',r.status_code==200)
            c.headers['X-CSRF-Token']=r.json()['csrf']
            r=c.get('/api/workflows/'+closed);ck('Original closed flow ID and task statuses remain',r.status_code==200 and r.json()['item']['status']=='closed' and all(t['status']=='已关闭' for t in r.json()['item']['tasks']))
            rev=c.get('/api/workflows').json()['revision'];did='synthetic-upgrade-draft-001'
            r=c.put('/api/drafts/'+did,json={'kind':'flow','context':{'workflow_kind':'maintenance','template_id':'WFT-maintenance','template_version':'1.0'},'values':{'title':'合成升级草稿'},'expected_draft_revision':0,'expected_workspace_revision':rev})
            ck('New account-private draft table actually supports a write',r.status_code==200)
        app.state.db.engine.dispose()
        app=create_app(Settings('sqlite:///'+str(p),'http://testserver',testing=True))
        with TestClient(app) as c:
            c.headers['Origin']='http://testserver';r=c.post('/api/auth/login',json={'username':'admin','password':creds['admin']})
            r=c.get('/api/drafts/'+did);ck('Reopened service reads the same private draft',r.status_code==200 and r.json()['values']['title']=='合成升级草稿')
        app.state.db.engine.dispose()
        destination=base/'restored'/'copy.sqlite3';restore(snapshot,destination,sha(snapshot))
        with sqlite3.connect(destination) as c:
            ck('Old backup restored ONLY into new path, migrated, sessions revoked',c.execute('SELECT version FROM schema_versions').fetchall()==[(2,)] and c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0)
        st,_,_,_=state(destination)
        ck('Restored preupgrade account and workspace match preupgrade state',all(st[k]==before[k] for k in ['users','workspaces','facilities','command_receipts']) and sha(snapshot)==migration['snapshot_sha256'])
        ck('Original live synthetic database and saved draft unaffected by new-path restore',state(p)[3]==[(2,)] and state(p)[0]['workspaces']==before['workspaces'])
        ck('Source M2 ZIP remains byte-identical',sha(source)==M2_SHA)
        result={'status':'PASS','checks_passed':len(checks),'checks':checks,'baseline_zip_sha256':M2_SHA,'migration':'schema1 -> schema2; additive user_drafts','environment':'Linux, temporary synthetic SQLite, original M2 CLI subprocess, actual M3 HTTP TestClient. NOT Mac native upgrade.','user_data_accessed':False,'secrets_in_report':False}
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':'PASS','checks':len(checks)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--m2-zip',type=Path,required=True);p.add_argument('--out',type=Path,default=ROOT/'reports/m2-to-m3-upgrade.json');a=p.parse_args();main(a.m2_zip,a.out)
