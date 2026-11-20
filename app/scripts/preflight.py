#!/usr/bin/env python3
"""Read-only dependency/port check. No downloads, package installs or Git calls."""
import importlib.util
import json
import shutil
import socket
import subprocess
import sys

checks={'python':sys.version.split()[0],'python_ok':sys.version_info>=(3,11),'node_path':shutil.which('node')}
if checks['node_path']:
    r=subprocess.run([checks['node_path'],'--version'],capture_output=True,text=True,timeout=3)
    checks['node_version']=r.stdout.strip();checks['node_ok']=r.returncode==0 and int(r.stdout.strip().lstrip('v').split('.')[0])>=22
else:checks['node_ok']=False
for name in ['fastapi','uvicorn','sqlalchemy','argon2','pydantic']:checks[name]=importlib.util.find_spec(name) is not None
with socket.socket() as s:
    checks['port_8765_free']=s.connect_ex(('127.0.0.1',8765))!=0
checks['git_required']=False;checks['docker_required_for_sqlite']=False
print(json.dumps(checks,ensure_ascii=False,indent=2))
sys.exit(0 if checks['python_ok'] and checks['node_ok'] and checks['port_8765_free'] else 2)
