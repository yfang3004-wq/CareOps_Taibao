"""Run the preserved v0.3 rules on the server, never execute caller-supplied code."""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def engine(op: str, **values):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("缺少 Node.js 22+；不会自动安装或修改系统环境。")
    data = json.dumps({"op": op, **values}, ensure_ascii=False, allow_nan=False)
    if len(data.encode("utf-8")) > 16_000_000:
        raise ValueError("工作区超过本候选版容量限制")
    try:
        run = subprocess.run([node, str(ROOT / "engine/bridge.cjs")], input=data,
                             text=True, capture_output=True, timeout=10,
                             cwd=ROOT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("规则检查超时；事务未提交，请缩小试用数据") from exc
    if run.returncode or not run.stdout or len(run.stdout)>20_000_000:
        raise RuntimeError("规则引擎未正常返回；事务未提交")
    answer = json.loads(run.stdout)
    if not answer.get("ok"):
        raise ValueError(answer.get("error", "规则检查失败"))
    return answer["result"]
