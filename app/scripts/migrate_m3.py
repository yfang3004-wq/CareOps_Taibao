#!/usr/bin/env python3
"""Explicit additive v1->v2 migration, after a verified consistent snapshot.
No implicit downgrade, no reset/reseed, no password/session output. Stop the old
service first. Old M2 code deliberately rejects a migrated database.
"""
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from prepare_upgrade import prepare, inspect_database, sha_file
from careops.database import drafts
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite


def migrate(source:Path):
    source=source.expanduser().absolute()
    prep=prepare(source)
    if not source.exists():return {**prep,'migration':'NEW_INSTALL_SCHEMA_2'}
    before=inspect_database(source)
    if before['schema_version']==2:return {**prep,'migration':'ALREADY_SCHEMA_2'}
    snap=Path(prep['snapshot'])
    if inspect_database(snap)['schema_version']!=1:raise ValueError('迁移前快照不是旧结构，停止')
    # Ensure no old service changed business/account state after the snapshot.
    snap_state=inspect_database(snap)
    for k in ('account_ids_sha256','workspaces','lineage'):
        if before[k]!=snap_state[k]:raise ValueError('数据库在快照后已变化；停止旧服务并重新人工备份核对')
    with sqlite3.connect(source) as c:
        c.execute('PRAGMA foreign_keys=ON');c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT version FROM schema_versions').fetchall()!=[(1,)]:raise ValueError('结构已变化，停止')
        # Recheck inside the lock. No alterations to any old business/account rows.
        rows=c.execute('SELECT facility_id,revision,payload FROM workspaces ORDER BY facility_id').fetchall()
        from prepare_upgrade import digest
        current=[{'facility_id':f,'revision':r,'payload_sha256':digest(json.loads(p)),
                  'rows':sum(len(v) for v in json.loads(p)['tables'].values()),'flows':len(json.loads(p)['flow']['instances'])} for f,r,p in rows]
        if current!=before['workspaces']:raise ValueError('工作区并发改变，迁移中止')
        c.execute(str(CreateTable(drafts).compile(dialect=sqlite.dialect())))
        c.execute('UPDATE schema_versions SET version=2,applied_at=? WHERE version=1',(datetime.now(timezone.utc).isoformat(),))
        if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('迁移外键核对失败')
    after=inspect_database(source)
    for k in ('account_ids_sha256','workspaces'):
        if before[k]!=after[k]:raise RuntimeError('迁移后数据核对不一致；保留备份，勿继续启动')
    return {'status':'PASS','migration':'SCHEMA_1_TO_2','snapshot':str(snap),
            'snapshot_sha256':sha_file(snap),'accounts_and_workspace_unchanged':True,
            'added_tables':['user_drafts'],'production_ready':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(migrate(a.source),ensure_ascii=False,indent=2))
    except Exception as e:print('M3升级已停止：'+str(e),file=sys.stderr);sys.exit(2)
