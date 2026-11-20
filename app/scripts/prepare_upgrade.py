#!/usr/bin/env python3
"""Read-only upgrade gate and consistent snapshot. No schema or source mutation.

Recognizes schema 1 (M1/M2) and schema 2 (M3). Checks unknown/future/partial stores before
launch; writes private snapshots and receipts to fixed sibling directories.
A snapshot on the same disk is not an off-device/disaster recovery backup.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from backup_sqlite import backup
from careops import __version__

TABLES={'service_lock','schema_versions','facilities','users','sessions','workspaces','command_receipts','server_audit','import_previews','login_attempts'}

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def sha_file(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def private_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())

def release_fingerprint():
    parts=[]
    for folder in ['careops','engine','web','scripts']:
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and p.suffix in {'.py','.cjs','.js','.css','.html','.sh','.json'} and '__pycache__' not in p.parts:
                parts.append([str(p.relative_to(ROOT)),sha_file(p)])
    return digest(parts)

def inspect_database(source:Path):
    if not source.is_file() or source.is_symlink():raise ValueError('数据库不存在或为符号链接；请核对实际本机路径')
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as c:
        c.execute('PRAGMA query_only=ON')
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not TABLES.issubset(names):raise ValueError('未知或不完整数据库，拒绝启动/初始化覆盖')
        versions=c.execute('SELECT version,applied_at FROM schema_versions').fetchall()
        if [r[0] for r in versions] not in ([1],[2]):raise ValueError('数据库版本不是已支持的1或2，不能自动升级或降级')
        schema=versions[0][0]
        if schema==2 and 'user_drafts' not in names:raise ValueError('M3草稿表缺失；拒绝启动')
        if schema==1 and 'user_drafts' in names:raise ValueError('旧结构出现未知草稿表；保留现场')
        if c.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('数据库快速完整性检查失败')
        if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('数据库外键核对失败')
        identities=c.execute('SELECT id,facility_id,created_at FROM users ORDER BY id').fetchall()
        workspace=[]
        for fid,revision,payload in c.execute('SELECT facility_id,revision,payload FROM workspaces ORDER BY facility_id'):
            p=json.loads(payload)
            if p.get('format')!='careops-workspace' or p.get('version')!=3:raise ValueError('业务工作区不是兼容的v0.3结构')
            workspace.append({'facility_id':fid,'revision':revision,'payload_sha256':digest(p),'rows':sum(len(v) for v in p['tables'].values()),'flows':len(p['flow']['instances'])})
        return {'schema_version':schema,'accounts':len(identities),'account_ids_sha256':digest(identities),
                'workspaces':workspace,'lineage':digest([versions,c.execute('SELECT id,timezone FROM facilities ORDER BY id').fetchall()]),'quick_check':'PASS','foreign_key_check':'PASS'}

def prepare(source:Path,manual=False):
    # Resolve parent, but reject symlinks at the selected database itself.
    source=source.expanduser().absolute()
    if source.is_symlink():raise ValueError('拒绝符号链接数据库')
    if not source.exists():return {'status':'NO_EXISTING_DATABASE','version':__version__,'note':'尚无旧库；后续初始化将在新目录创建合成试用库'}
    before=inspect_database(source);fingerprint=release_fingerprint();st=source.stat()
    marker=source.parent/'upgrades'/f'm3-{fingerprint[:16]}-s{before["schema_version"]}-{before["lineage"][:12]}-{st.st_ino}.json'
    if marker.exists() and not manual:
        if marker.is_symlink():raise ValueError('升级标记不能是符号链接')
        old=json.loads(marker.read_text())
        if old.get('source_inode')==[st.st_dev,st.st_ino] and old.get('lineage')==before['lineage']:
            snap=Path(old['snapshot'])
            if snap.is_symlink() or not snap.is_file() or sha_file(snap)!=old['backup']['sha256']:
                raise ValueError('原升级备份缺失或摘要改变；保留现场，不自动覆盖备份标记')
            return {'status':'ALREADY_CHECKED','version':__version__,'snapshot':str(snap),'schema_version':before['schema_version'],'source_untouched':True}
        # Replaced DB at same path must get a fresh snapshot, never overwrite old receipt.
        marker=source.parent/'upgrades'/f'm3-{fingerprint[:16]}-{uuid4().hex[:10]}.json'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:10]
    folder=source.parent/'backups'/f'{"manual" if manual else "before-M3"}-{stamp}'
    folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    snap=folder/'careops.sqlite3'
    b=backup(source,snap);snapshot_summary=inspect_database(snap)
    # Snapshot is a consistent point in time; live concurrent writes may continue.
    report={'status':'PASS','version':__version__,'release_fingerprint':fingerprint,'created_at':datetime.now(timezone.utc).isoformat(),
            'snapshot':str(snap),'backup':b,'snapshot_summary':snapshot_summary,'source_inode':[st.st_dev,st.st_ino],'lineage':before['lineage'],
            'source_untouched':True,'schema_changed':False,'schema_version':before['schema_version'],'off_device_backup':False,'contains_credentials':True,'encrypted':False,
            'note':'仅本机敏感快照；应用不向浏览器提供数据库下载。恢复只写新路径并撤销会话。'}
    private_json(folder/'snapshot.manifest.json',report)
    if not manual:private_json(marker,report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--manual',action='store_true');a=p.parse_args()
    try:
        r=prepare(a.source,a.manual)
        # No passwords, account hashes, sessions or business records in console output.
        print(json.dumps({k:r[k] for k in ['status','version','snapshot','schema_version','source_untouched'] if k in r},ensure_ascii=False,indent=2))
    except Exception as e:
        print('备份/升级前检查失败：'+str(e),file=sys.stderr);sys.exit(2)
