"""栖云颐养·金陵国际康养社区（CCRC 完整演示版）统一本地启动入口。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed_taibao_ccrc import initialize_taibao_demo


def main():
    p = argparse.ArgumentParser(description="启动 CareOps × 栖云颐养·金陵国际康养社区 本地演示系统")
    p.add_argument("--data-dir", type=Path, default=ROOT.parent / "runtime-taibao")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    args = p.parse_args()
    if not 0 <= args.port <= 65535:
        p.error("端口范围须为 0—65535")

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    origin = f"http://127.0.0.1:{port}"
    data_dir = args.data_dir.expanduser().resolve()
    env = os.environ.copy()
    env.update(
        CAREOPS_DATA_DIR=str(data_dir),
        CAREOPS_ORIGIN=origin,
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("CAREOPS_DATABASE_URL", None)
    os.environ.update(CAREOPS_DATA_DIR=str(data_dir), CAREOPS_ORIGIN=origin)

    info = initialize_taibao_demo(data_dir)
    print(
        f"\n============================================================\n"
        f"CareOps × 栖云颐养·金陵国际康养社区（CCRC 全链路演示版）\n"
        f"访问地址：{origin}\n"
        f"数据目录：{data_dir}\n"
        f"账户凭据：{info['credentials_file']}\n"
        f"说明：登录页支持演示身份一键填入/切换；按 Ctrl+C 停止服务，演示记录自动保留。\n"
        f"============================================================\n",
        flush=True,
    )
    if args.open:
        def _open_when_ready():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(60):
                try:
                    with opener.open(origin + "/", timeout=1):
                        webbrowser.open(origin)
                        return
                except Exception:
                    time.sleep(0.25)
        threading.Thread(target=_open_when_ready, daemon=True).start()
    try:
        subprocess.run(
            [
                sys.executable, "-m", "uvicorn", "careops.app:create_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port),
                "--no-access-log", "--no-server-header",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
    except KeyboardInterrupt:
        print("\n演示服务已安全停止；本机演示数据已保留。")


if __name__ == "__main__":
    main()
