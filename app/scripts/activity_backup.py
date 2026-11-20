"""活动候选本地完整备份；仅恢复到新目录，不覆盖已有运行数据。

备份包含数据库（含账号密码哈希）与附件，按敏感文件保管。
建议先正常停止服务，避免正在上传的文件不属于同一次快照。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zipfile

FORMAT = 'careops-local-activity-backup-v1'
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or any(x in {'', '.', '..'} for x in name.split('/')):
        raise ValueError('备份含不安全路径')
    if p.as_posix() not in {'careops.sqlite3', 'DEPARTMENT_DEMO_SYNTHETIC_ONLY.txt'} and not p.as_posix().startswith('attachments/'):
        raise ValueError('备份含未允许的文件')
    return p


def backup(data_dir, output):
    data_dir = Path(data_dir).resolve()
    output = Path(output).absolute()
    source = data_dir / 'careops.sqlite3'
    if not source.is_file() or source.is_symlink():
        raise ValueError('请选择包含真实 careops.sqlite3 的运行目录')
    if output.exists():
        raise ValueError('目标备份已存在；请使用新文件名，不覆盖历史备份')
    entries = []
    total = 0
    with tempfile.TemporaryDirectory(prefix='careops-backup-') as tmp:
        snap = Path(tmp) / 'careops.sqlite3'
        # SQLite backup API 产生一致数据库快照，不复制运行中的 WAL 文件。
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(snap) as dest:
            src.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('数据库完整性检查失败')
        inputs = [('careops.sqlite3', snap)]
        marker = data_dir / 'DEPARTMENT_DEMO_SYNTHETIC_ONLY.txt'
        if marker.is_file() and not marker.is_symlink():
            inputs.append((marker.name, marker))
        attachments = data_dir / 'attachments'
        if attachments.is_symlink():
            raise ValueError('附件目录为符号链接，保留现场并停止备份')
        if attachments.exists():
            for p in sorted(attachments.rglob('*')):
                if p.is_symlink():
                    raise ValueError('附件中存在符号链接，保留现场并停止备份')
                if p.is_file():
                    inputs.append((p.relative_to(data_dir).as_posix(), p))
        if len(inputs) > MAX_FILES:
            raise ValueError('文件数量超过本地候选备份上限')
        # 先写临时 ZIP，再以独占创建方式落盘，失败不覆盖已有文件。
        temporary = Path(tmp) / 'backup.zip'
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, p in inputs:
                safe_relative(name)
                size = p.stat().st_size
                total += size
                if total > MAX_BYTES:
                    raise ValueError('备份原始数据超过512 MiB上限')
                content = p.read_bytes()
                if len(content) != size:
                    raise ValueError('备份期间文件变化，请停止服务后重试')
                archive.writestr(name, content)
                entries.append({'path': name, 'size': size, 'sha256': sha(content)})
            archive.writestr('manifest.json', json.dumps({'format': FORMAT, 'files': entries,
                'sensitive': True, 'note': '包含数据库和附件；不含初始密码明文文件。恢复保留原密码，撤销原会话。'}, ensure_ascii=False))
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as dst, temporary.open('rb') as src:
            while block := src.read(1024 * 1024):
                dst.write(block)
    return {'backup': str(output), 'files': len(entries), 'attachment_files': sum(x['path'].startswith('attachments/') for x in entries)}


def restore(archive_path, target_dir):
    target = Path(target_dir).absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('恢复目录必须尚不存在；不会覆盖已有数据')
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > MAX_FILES + 1 or len(names) != len(set(names)):
            raise ValueError('备份文件数量异常或路径重复')
        if sum(i.file_size for i in infos) > MAX_BYTES + 4 * 1024 * 1024:
            raise ValueError('备份展开大小超过上限')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
            raise ValueError('备份清单缺失或过大')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != FORMAT or not isinstance(manifest.get('files'), list):
            raise ValueError('备份格式不兼容')
        rows = manifest['files']
        if any(not isinstance(x, dict) for x in rows):
            raise ValueError('备份清单格式无效')
        if len(rows) != len({x.get('path') for x in rows}) or set(names) != {'manifest.json', *(x.get('path') for x in rows)}:
            raise ValueError('备份清单与文件不一致')
        if 'careops.sqlite3' not in names:
            raise ValueError('备份缺少数据库')
        # 在临时目录完整验证后才创建恢复目录。禁止任意路径和符号链接。
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='careops-restore-', dir=target.parent) as tmp:
            staging = Path(tmp)
            for row in rows:
                name = row['path']
                rel = safe_relative(name)
                info = archive.getinfo(name)
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('备份含符号链接')
                data = archive.read(name)
                if len(data) != row.get('size') or sha(data) != row.get('sha256'):
                    raise ValueError('文件校验失败：' + name)
                p = staging.joinpath(*rel.parts)
                p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                p.write_bytes(data)
                p.chmod(0o600)
            with sqlite3.connect(staging / 'careops.sqlite3') as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('恢复数据库完整性检查失败')
                tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'schema_versions', 'sessions', 'users', 'workspaces'}.issubset(tables):
                    raise ValueError('不是完整 CareOps 数据库')
                db.execute('DELETE FROM sessions')
                db.commit()
            target.mkdir(mode=0o700)  # 不使用 exist_ok，防止覆盖竞态。
            for p in staging.iterdir():
                p.rename(target / p.name)
    return {'restored_to': str(target), 'files': len(rows), 'sessions_revoked': True}


def main():
    p = argparse.ArgumentParser(description='CareOps活动数据与照片完整备份 / 新目录恢复')
    commands = p.add_subparsers(dest='action', required=True)
    b = commands.add_parser('backup'); b.add_argument('--data-dir', required=True); b.add_argument('--output', required=True)
    r = commands.add_parser('restore'); r.add_argument('--archive', required=True); r.add_argument('--new-data-dir', required=True)
    args = p.parse_args()
    try:
        result = backup(args.data_dir, args.output) if args.action == 'backup' else restore(args.archive, args.new_data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError) as e:
        raise SystemExit('未完成；原数据保留。' + str(e))


if __name__ == '__main__':
    main()
