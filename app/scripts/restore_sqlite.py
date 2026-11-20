#!/usr/bin/env python3
"""Restore into a NEW path only, verify hash, revoke copied sessions, preserve source."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from backup_sqlite import backup
from careops.database import Database,facilities,sessions
from careops.security import log
from sqlalchemy import select,delete

def restore(source:Path,dest:Path,expected_sha256:str):
    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('源文件SHA256与预期不符')
    result=backup(source,dest)
    from migrate_m3 import migrate
    migrate(dest)  # Only the NEW destination may receive a supported additive migration.
    db=Database('sqlite:///'+str(dest.resolve()));db.initialize()
    with db.write() as c:
        c.execute(delete(sessions))
        for fid in c.execute(select(facilities.c.id)).scalars().all():
            log(c,{'id':'system:local-restore','facility_id':fid},'database_restored_to_new_path',summary={'backup_sha256':expected_sha256,'all_sessions_revoked':True})
    db.engine.dispose()
    result.update(restored_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),sessions_revoked=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--dest',required=True,type=Path);p.add_argument('--sha256',required=True);a=p.parse_args()
    print(json.dumps(restore(a.source,a.dest,a.sha256),ensure_ascii=False,indent=2))
