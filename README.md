# recap-agent

全球市场复盘工程：复盘任务 **skill 化** + GitHub Actions **每日 08:00（北京）定时编排** + **飞书卡片推送**，由 Claude Code 驱动。

## 架构

- **5 个 skill**（`.claude/skills/`，由 Claude Code 调用）：
  - `recap-data-collect` — 共享数据采集（tushare 封装，重试 3 次降级 + 磁盘缓存）
  - `recap-daily` / `recap-weekly` / `recap-monthly` — 日 / 周 / 月复盘，各产出 HTML 报告 + 飞书卡片 JSON
  - `feishu-card-push` — 按 `FEISHU_WEBHOOKS` 把卡片推到对应群
- **可测试工具层**（`recap_agent/`，零运行时依赖）：
  - `feishu/`（webhook 解析 + 签名 + 发送）、`data/`（tushare 客户端 + 缓存）、`reports/`（渲染 + pipeline）、`schedule.py`
- **GitHub Actions**（`.github/workflows/daily-recap.yml`）：每日北京 08:00 编排，单任务失败不拖垮整批。

## 目录结构

```
.claude/skills/        # 5 个复盘 skill（SKILL.md + 脚本 + 模板）
recap_agent/           # 可测试 Python 工具层（feishu / data / reports / schedule）
tests/                 # 单元测试（unittest，零依赖）
.github/workflows/     # 每日定时编排
configs/recap.yml      # 复盘配置
docs/                  # 设计与开发记录
```

## 快速开始（本地 dry run）

```bash
# 1. 装包（纯渲染/推送零依赖；采集需要 tushare/pandas）
pip install -e .
pip install tushare pandas            # 可选，仅采集需要

# 2. 配置 secrets
cp .env.example .env                 # 编辑 .env 填入 TUSHARE_TOKEN / FEISHU_WEBHOOKS

# 3. 采集
export $(grep -v '^#' .env | xargs)
python .claude/skills/recap-data-collect/scripts/collect.py \
  --as-of-date 20240102 --output-dir data/daily

# 4. 渲染日报（sections.json 由你或 Claude 基于数据填写）
echo '[{"heading":"核心结论","bullets":["示例结论"]}]' > sections.json
python .claude/skills/recap-daily/scripts/run.py \
  --task daily --date 2024-01-02 --sections sections.json

# 5. 推送飞书（dry_run 时跳过）
python .claude/skills/feishu-card-push/scripts/push.py \
  --task daily --card artifacts/cards/daily.json
```

## Configuration（必需 secrets）

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 添加下面三个：

| Secret | 必需 | 用途 | 获取方式 |
|---|---|---|---|
| `TUSHARE_TOKEN` | ✅ | tushare 数据采集认证 | https://tushare.pro 注册 → 个人主页 → API Token |
| `FEISHU_WEBHOOKS` | ✅ | 飞书推送目标（JSON） | 飞书群「设置 → 群机器人 → 添加自定义机器人」，key 取 webhook URL 末段 |
| `ANTHROPIC_API_KEY` | ✅（CI） | GitHub Actions 跑 Claude Code | https://console.anthropic.com/ |

> 本地 dry run 只需要 `TUSHARE_TOKEN` 与 `FEISHU_WEBHOOKS`；`ANTHROPIC_API_KEY` 仅 CI 编排用。

### FEISHU_WEBHOOKS 格式

单个 JSON 字符串，**daily 必填**；weekly / monthly 缺省时**回退到 daily 群**：

```json
{
  "daily":   {"key": "<webhook-key>"},
  "weekly":  {"key": "<可选，缺省回退 daily>"},
  "monthly": {"key": "<可选，缺省回退 daily>"}
}
```

机器人启用签名校验时，在对应任务对象里加 `sign_secret`：

```json
{"daily": {"key": "d-key", "sign_secret": "your-sign-secret"}}
```

`key` 可以是 webhook URL 末段，也可以是完整 URL（自动识别）。

## GitHub Actions 编排

- **定时**：`cron: "0 0 * * *"`（UTC 00:00 = 北京 08:00）。daily 每天跑；weekly 周一跑；monthly 月初 1–3 号跑（由 `recap_agent.schedule` 解释）。
- **手动**：仓库 Actions → `daily-recap` → Run workflow，可指定 `task`（all / daily / weekly / monthly）、`as_of_date`、`dry_run`。
- **失败隔离**：daily / weekly / monthly 各自 `continue-on-error`，单个失败不拖垮整批；产物 `if: always()` 上传，结果写入 Step Summary。

## 开发

```bash
pip install -e .
python -m unittest discover -s tests    # 零依赖，覆盖降级/回退/签名/缓存/调度/workflow 静态校验
```

测试覆盖的关键路径：飞书 webhook 解析与 daily→weekly/monthly 回退、JSON 解析失败处理、飞书签名与 URL 拼接、空/脏/截断缓存丢弃、tushare 重试 3 次后跳过降级、不可重试错误立即抛、调度逻辑、workflow cron/secrets/失败隔离静态校验。

详见 `docs/design/SKZ-77-recap-agent-engineering-design.html`（设计）与 `docs/development/SKZ-77-implementation.html`（实现记录）。
