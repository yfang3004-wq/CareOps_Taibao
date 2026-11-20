#!/usr/bin/env python3
"""Verify shipped source/evidence bytes. Does not scan .venv or user databases."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'MANIFEST.json').read_text(encoding='utf-8'))
failures=[]
for item in manifest['files']:
    path=(ROOT/item['path']).resolve()
    if not path.is_relative_to(ROOT):failures.append({'path':item['path'],'reason':'unsafe path'});continue
    if not path.is_file():failures.append({'path':item['path'],'reason':'missing'});continue
    data=path.read_bytes()
    if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
        failures.append({'path':item['path'],'reason':'changed'})
print(json.dumps({'status':'PASS' if not failures else 'FAIL','checked':len(manifest['files']),'failed':failures,'scope':'Shipped manifest entries only; extra runtime files are not uploaded or examined.'},ensure_ascii=False,indent=2))
raise SystemExit(1 if failures else 0)
