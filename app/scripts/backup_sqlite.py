#!/usr/bin/env python3
"""Consistent SQLite backup using the online backup API; does not overwrite files."""
import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

def backup(source: Path, destination: Path):
    source=source.resolve();destination=destination.resolve()
    if not source.is_file():raise ValueError('源数据库不存在')
    if source==destination or destination.exists():raise ValueError('目标已存在或与源相同；拒绝覆盖')
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(destination) as dst:
            src.backup(dst,pages=100)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份完整性检查失败')
            version=dst.execute('SELECT version FROM schema_versions').fetchall()
            if version not in ([(1,)],[(2,)]):raise ValueError('并非兼容的CareOps数据库')
            if version==[(2,)] and not dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_drafts'").fetchone():raise ValueError("M3草稿表缺失")
        report={'status':'PASS','sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'bytes':destination.stat().st_size,'at':datetime.now(timezone.utc).isoformat(),'encrypted':False,'contains_password_hashes_and_sessions':True,'overwrite':False}
        return report
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--dest',required=True,type=Path);a=p.parse_args()
    report=backup(a.source,a.dest)
    a.dest.with_suffix(a.dest.suffix+'.manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
