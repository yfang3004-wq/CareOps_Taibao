# CareOps × 栖云颐养·金陵国际康养社区（CCRC 完整演示版）

本仓库基于 `CareOps_Complete_Demo_Maintenance_4Status_V1` 基线演进，严格对齐《栖云颐养·金陵国际康养社区养老信息系统产品与需求设计报告》的 **8 大一级模块、22 个二级子模块与 65 项三级功能点**。

## 快速启动

双击根目录 **`启动太保家园Demo.command`**，或在终端执行：

```bash
./app/.venv/bin/python app/scripts/start_taibao_demo.py --port 8765
```

- 首次运行会在 `runtime-taibao/` 自动初始化 **栖云颐养·金陵国际康养社区（演示环境）** 四业态合成数据库（`Asia/Shanghai` 时区），包含康养活力公寓、颐养协助生活区、静心认知症专属照护单元、乐养旅居体验房的完整房床、夫妻分住不分区、仙林三甲绿通外院保留床位、临期预留锁床、双时间护理打卡、九要素显式交接班及实拍活动附件。
- 初始随机账户保存在 `runtime-taibao/INITIAL_CREDENTIALS_DEMO-01.txt`；本地合成演示模式下支持顶部演示角色快速切换与第 8 模块（活力长者适老微端 / 家属安心透明端）内外实时联动核验。
- 数据备份可双击 `备份数据与照片.command`。

## 云端一键部署（课堂演示免买域名）

仓库根目录已内置 [`Dockerfile`](Dockerfile)、[`.dockerignore`](.dockerignore)、[`render.yaml`](render.yaml) 与云端启动脚本 [`app/scripts/start_cloud.py`](app/scripts/start_cloud.py)，内置 Python 3.13 与 Node.js 22 规则引擎，零环境变量即可在 **Render / Zeabur / Railway** 上生成免费 HTTPS 子域名供课堂电脑直接访问：

1. 将本仓库推送到 GitHub（私有或公开均可）。
2. 在 **Render**（推荐免费长期可用，15 分钟无访问休眠、打开后约 30–50 秒冷启动唤醒）、**Zeabur** 或 **Railway** 新建 Web Service 并绑定该 GitHub 仓库，平台会自动识别根目录 `Dockerfile` 构建。
3. 构建完成后点击平台分配的 `https://*.onrender.com` / `*.zeabur.app` / `*.up.railway.app` 链接即可使用；容器冷启动时会自动初始化「栖云颐养·金陵国际康养社区」合成演示库并开启一键切换演示身份。
4. 课堂演示建议：如使用 Render 免费版，上台前 1 分钟先打开一次网址唤醒容器。

## 架构与口径边界

1. **唯一共享底座**：全院八大模块共用同一份 `tables.residents` 长者主档、`stays` 在院状态与 `beds` 空间目录，全部写事务经服务端修订号（`revision`）乐观锁与操作审计校验。
2. **防造假与隐私最小披露**：保留「提交时间 ≠ 实际执行时间」「老人拒绝不作漏做」「写交班 ≠ 接班」「护理入院评估正文对销售隔离」「维修前台四状态投影 + 后台跨人独立复核」核心机制。
3. **长者与家属轻协同端（第 8 模块）**：严格执行「院内岗位核验后脱敏触达」，长者微端遵循米白底 `#FAF6F0`、深棕字 `#30251F`、每屏单一朱红 `#C92B27` 主按钮适老化规范；家属端仅展示已核对（`confirmed`）费用、实际签到活动影像及已闭环异常回执。
4. **合成数据声明**：本仓库预置数据全部为虚构脱敏样例，非真实医疗病历或生产上线部署包。
