#!/usr/bin/env python3
"""MVP-B1 alpha.4 package checks against the exact preserved M3 alpha.3 archive."""
from pathlib import Path
import argparse, ast, subprocess, hashlib, json, sys, tempfile, os, zipfile, importlib.metadata
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
p=argparse.ArgumentParser();p.add_argument('--baseline-zip',type=Path,required=True);a=p.parse_args();checks=[]
M3_SHA='e51cebf7d06e6b0a9a35fd395095a609f702f62082cf399b54198a31cbcca46a'
VERSION='0.4.0-alpha.4'

def ck(name,value):
    if not value:raise AssertionError(name)
    checks.append({'name':name,'status':'PASS'})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

ck('Exact M3 baseline ZIP selected',a.baseline_zip.is_file() and sha(a.baseline_zip)==M3_SHA)
with tempfile.TemporaryDirectory(prefix='careops-mvpb1-static-') as tmp:
    root=Path(tmp)
    with zipfile.ZipFile(a.baseline_zip) as z:
        ck('Baseline archive has safe relative paths',all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in z.namelist()));z.extractall(root)
    candidates=[p.parent.parent for p in root.rglob('careops/__init__.py') if (p.parent.parent/'web/app.js').is_file()]
    ck('M3 baseline package root found',len(candidates)==1);B=candidates[0]
    protected=[
      'requirements.txt','requirements-test.txt','requirements-postgres.txt','web/intents.js',
      'engine/engine.js','engine/io.js','engine/demo.js','engine/demo_v02.js',
      'reference_v03/careops_desk_v0.3_candidate.html','careops/database.py','careops/security.py'
    ]
    for rel in protected:ck('Protected M3 byte preserved: '+rel,sha(R/rel)==sha(B/rel))
    approved={'careops/__init__.py','careops/app.py','careops/policy.py','careops/experience.py','engine/core.js','engine/registry.js','engine/workflow.js','web/app.js','web/m3.js','web/app.css','web/fields.json'}
    changes=[]
    for rel in sorted(approved):
        ck('Approved B0/B1 file changed from M3: '+rel,sha(R/rel)!=sha(B/rel));changes.append(rel)
    runtime=[]
    for folder in ['careops','engine','web']:
        for f in (B/folder).rglob('*'):
            if f.is_file(): runtime.append(str(f.relative_to(B)))
        for f in (R/folder).rglob('*'):
            if f.is_file() and '__pycache__' not in f.parts: runtime.append(str(f.relative_to(R)))
    runtime=set(runtime)
    unexpected=[]
    for rel in sorted(runtime-approved):
        left=B/rel;right=R/rel
        if not left.is_file() or not right.is_file() or sha(left)!=sha(right):unexpected.append(rel)
    ck('No runtime changes outside approved B0/B1 files',not unexpected)
    (R/'reports/MVP_B1_CHANGESET.json').write_text(json.dumps({'baseline_zip_sha256':M3_SHA,'version':VERSION,'approved_runtime_files':changes,'unexpected_runtime_changes':unexpected,'old_package_modified':False,'user_mac_modified':False},ensure_ascii=False,indent=2))

for folder in ['careops','scripts','tests']:
    for f in (R/folder).rglob('*.py'):
        if '_mvp_refactor_backup' in f.parts:continue
        ast.parse(f.read_text(),filename=str(f))
ck('All shipped Python parses',True)
for f in list((R/'web').glob('*.js'))+list((R/'engine').glob('*.js'))+list((R/'engine').glob('*.cjs')):
    s=subprocess.run(['node','--check',str(f)],capture_output=True,text=True);ck('JavaScript syntax '+str(f.relative_to(R)),s.returncode==0)
for f in list(R.glob('*.command'))+[R/'scripts/start_local.sh']:
    s=subprocess.run(['bash','-n',str(f)],capture_output=True,text=True);ck('Entry script syntax '+str(f.relative_to(R)),s.returncode==0)

from careops import __version__
ck('Package version is alpha.4',__version__==VERSION)
from careops.app import Settings,create_app
from careops.database import meta, versions
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect,select
try:Settings('sqlite:///:memory:','http://0.0.0.0:8765')
except ValueError:denied=True
else:denied=False
ck('Non-loopback serving origin rejected',denied)
with tempfile.TemporaryDirectory() as tmp:
    app=create_app(Settings('sqlite:///'+str(Path(tmp)/'test.sqlite3'),'http://testserver',testing=True))
    db=app.state.db;api=app.openapi()
    with db.read() as c:v=c.execute(select(versions.c.version)).scalars().all()
    ck('Fresh DB remains schema2 with private draft table',v==[2] and 'user_drafts' in inspect(db.engine).get_table_names());db.engine.dispose()
ck('Required M3 APIs preserved',all(k in api['paths'] for k in ['/api/today','/api/residents/{rid}/profile','/api/drafts','/api/drafts/{did}','/api/drafts/{did}/delete','/api/tabular/inspect','/api/tabular/preview','/api/tabular/commit','/api/reports/{table}','/api/receipts/{rid}']))
(R/'docs/API.openapi.json').write_text(json.dumps(api,ensure_ascii=False,indent=2))
sql='-- MVP-B1 alpha.4 keeps DB schema2. SQLAlchemy compilation only; PostgreSQL runtime NOT TESTED.\n\n'+'\n\n'.join(str(CreateTable(t).compile(dialect=postgresql.dialect()))+';' for t in meta.sorted_tables)
(R/'deploy/schema.postgresql.sql').write_text(sql)
ck('PostgreSQL DDL still compiles draft table','CREATE TABLE user_drafts' in sql)

reports=[
 ('reports/browser-regression/browser-harness-tests.json','checks_passed',31),
 ('reports/browser-m2/m2-browser-tests.json','checks_passed',27),
 ('reports/browser-m3/m3-browser-tests.json','checks_passed',27),
 ('reports/m2-to-m3-upgrade.json','checks_passed',21),
]
for rel,key,passed in reports:
    result=json.loads((R/rel).read_text());ck('Complete report: '+rel,result['status']=='PASS' and result[key]==passed)
mvp=json.loads((R/'reports/browser-mvp-refactor/browser-mvp-refactor.json').read_text())
ck('MVP-B1 public maintenance DOM chain passes',mvp['status']=='PASS' and len(mvp['checks'])==22 and all(x['status']=='PASS' for x in mvp['checks']))

ship=[p for p in R.rglob('*') if p.is_file() and '.venv' not in p.parts and '__pycache__' not in p.parts and '_mvp_refactor_backup' not in p.parts]
ck('No database or font files in shipped tree',not any(p.suffix.lower() in ['.sqlite3','.sqlite','.db','.ttf','.otf','.woff','.woff2'] for p in ship))
ck('No generated credential files in shipped tree',not any(p.name.startswith('INITIAL_CREDENTIALS_') for p in ship))
ck('No Git repository present',not (R/'.git').exists())
(R/'reports/package-checks.json').write_text(json.dumps({'status':'PASS','version':VERSION,'baseline_zip_sha256':M3_SHA,'checks_passed':len(checks),'checks':checks,'postgres_runtime':'NOT_TESTED'},ensure_ascii=False,indent=2))
env={'python':sys.version.split()[0],'node':subprocess.check_output(['node','--version'],text=True).strip(),'sqlite':__import__('sqlite3').sqlite_version,'libraries':{k:importlib.metadata.version(k) for k in ['fastapi','uvicorn','SQLAlchemy','argon2-cffi','pydantic','pytest','httpx']},'os':os.uname().sysname,'mac_native':'NOT_TESTED','real_staff_validation':'NOT_RUN','user_db_accessed':False}
(R/'reports/TEST_ENVIRONMENT.json').write_text(json.dumps(env,ensure_ascii=False,indent=2))
print(json.dumps({'status':'PASS','version':VERSION,'checks':len(checks)}))
