# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS node_runtime

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CAREOPS_ALLOW_CLOUD_DEMO=1 \
    CAREOPS_DATA_DIR=/tmp/careops-taibao-data \
    PORT=8765

# tzdata 用于 Asia/Shanghai 时区，libstdc++6 用于内嵌的 Node.js 规则引擎运行时
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# 引入 Node.js 22 二进制（服务端工作流规则校验 bridge.cjs 依赖 node）
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node

WORKDIR /workspace

COPY app/requirements.txt /workspace/app/requirements.txt
RUN pip install --no-cache-dir -r /workspace/app/requirements.txt

COPY app /workspace/app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8765\")}/healthz', timeout=3)"

CMD ["python", "app/scripts/start_cloud.py"]
