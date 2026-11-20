"""单一管理员部门入口；复用原本地服务，只写指定独立合成演示目录。"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import webbrowser

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description='CareOps 部门级合成 Demo')
    p.add_argument('--data-dir', type=Path, default=ROOT.parent/'runtime-demo')
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--open', action='store_true')
    args = p.parse_args()
    if not 0 <= args.port <= 65535:
        p.error('端口范围须为0—65535')
    data = args.data_dir.expanduser().resolve()
    marker = data/'DEPARTMENT_DEMO_SYNTHETIC_ONLY.txt'
    if (data/'careops.sqlite3').exists() and not marker.is_file():
        raise SystemExit('此目录已有其他运行数据库。原件保留，请指定新的独立演示目录。')
    data.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    # 与实际服务绑定语义一致：允许已关闭连接的 TIME_WAIT，不放宽真实监听冲突。
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', args.port))
    port = sock.getsockname()[1]
    sock.close()
    origin = f'http://127.0.0.1:{port}'
    env = os.environ.copy()
    env.update(CAREOPS_DATA_DIR=str(data),CAREOPS_ORIGIN=origin,PYTHONDONTWRITEBYTECODE='1')
    env.pop('CAREOPS_DATABASE_URL', None)
    # 首次载入原合成夹具和原有账号；不增加各部门账号。
    subprocess.run([sys.executable,'-m','careops.cli','init','--demo'],cwd=ROOT,env=env,check=True,timeout=60)
    marker.write_text('DEPARTMENT_DEMO_SYNTHETIC_ONLY\n',encoding='utf-8')
    credentials = data/'INITIAL_CREDENTIALS_DEMO-01.txt'
    print(f'\n部门 Demo：{origin}\n账户文件：{credentials}\n用 admin 登录并在同一会话切换部门；原维修独立复核规则保留。\n按 Ctrl+C 停止，已保存演示记录继续保留。',flush=True)
    if args.open:
        webbrowser.open(origin)
    try:
        subprocess.run([sys.executable,'-m','uvicorn','careops.app:create_app','--factory','--host','127.0.0.1','--port',str(port),'--no-access-log','--no-server-header'],cwd=ROOT,env=env,check=True)
    except KeyboardInterrupt:
        print('演示服务已停止；已保存数据保留。')

if __name__ == '__main__':
    main()
