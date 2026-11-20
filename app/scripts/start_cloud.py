"""栖云颐养·金陵国际康养社区（CCRC 完整演示版）云端容器启动入口。

适配 Zeabur / Railway / Render 等免运维 PaaS 平台：
- 自动读取平台注入的 PORT 环境变量并监听 0.0.0.0；
- 自动识别平台公网域名（CAREOPS_ORIGIN / RENDER_EXTERNAL_URL / RAILWAY_PUBLIC_DOMAIN / ZEABUR_WEB_DOMAIN）；
- 首次冷启动自动在 CAREOPS_DATA_DIR 初始化虚构脱敏演示库，启用演示账号一键切换；
- 启用 --proxy-headers，确保 HTTPS 反向代理下 Cookie 与同源校验正常。
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed_taibao_ccrc import initialize_taibao_demo


def _detect_origin(port: int) -> str:
    explicit = os.getenv("CAREOPS_ORIGIN", "").strip().rstrip("/")
    if explicit:
        return explicit
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url
    for env_key in ("RAILWAY_PUBLIC_DOMAIN", "ZEABUR_WEB_DOMAIN"):
        domain = os.getenv(env_key, "").strip().rstrip("/")
        if domain:
            if domain.startswith(("http://", "https://")):
                return domain
            return f"https://{domain}"
    return f"http://127.0.0.1:{port}"


def main() -> None:
    port = int(os.getenv("PORT", "8765"))
    host = os.getenv("HOST", "0.0.0.0")
    data_dir = Path(os.getenv("CAREOPS_DATA_DIR", "/tmp/careops-taibao-data")).expanduser().resolve()
    origin = _detect_origin(port)

    env = os.environ.copy()
    env.update(
        CAREOPS_DATA_DIR=str(data_dir),
        CAREOPS_ORIGIN=origin,
        CAREOPS_ALLOW_CLOUD_DEMO="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("CAREOPS_DATABASE_URL", None)
    os.environ.update(
        CAREOPS_DATA_DIR=str(data_dir),
        CAREOPS_ORIGIN=origin,
        CAREOPS_ALLOW_CLOUD_DEMO="1",
    )

    info = initialize_taibao_demo(data_dir)
    print(
        f"[CareOps Cloud Demo] 已就绪 | host={host} | port={port} | origin={origin} | "
        f"data_dir={data_dir} | credentials={info['credentials_file']}",
        flush=True,
    )

    os.chdir(ROOT)
    os.execvpe(
        sys.executable,
        [
            sys.executable, "-m", "uvicorn", "careops.app:create_app",
            "--factory", "--host", host, "--port", str(port),
            "--proxy-headers", "--forwarded-allow-ips=*",
            "--no-server-header",
        ],
        env,
    )


if __name__ == "__main__":
    main()
