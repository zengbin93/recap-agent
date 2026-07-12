# recap-agent

RecapAgent 是一套由大模型驱动的全球市场复盘工程体系。仓库把每个复盘任务拆成独立 skill，并把跨任务的数据采集、飞书卡片推送抽成共享能力。

## Skills

- `skills/recap-data-collect`: 共享数据采集 skill，封装 Tushare 调用、重试、缓存、降级和 manifest 输出。金融数据口径复用已有 `tushare` skill / Tushare 数据能力，不在本仓库重造金融知识库。
- `skills/recap-daily`: 只负责日复盘 HTML 报告和飞书卡片生成。
- `skills/recap-weekly`: 只负责周复盘 HTML 报告和飞书卡片生成。
- `skills/recap-monthly`: 只负责月复盘 HTML 报告和飞书卡片生成。
- `skills/tushare-recap-reports`: PR #1 的 Tushare 课题复盘 skill，封装“半年翻倍股票池”和“十倍潜力跟踪池”报告链路。
- `skills/feishu-card-push`: 统一处理飞书 webhook、签名、dry run 和发送结果。

## GitHub Actions

`.github/workflows/daily-recap.yml` 每天北京时间 08:00 触发，也支持 `workflow_dispatch` 手动触发。手动触发时可以选择 `daily` / `weekly` / `monthly`，并通过 `dry_run=true` 只生成 artifact、不发送飞书卡片。

workflow 由 Claude Code action 编排，固定调用仓库内 deterministic runner：

```bash
python3 scripts/run_recap.py --task daily --output-dir artifacts/reports --dry-run
```

无论成功或失败，workflow 都会通过 `actions/upload-artifact` 上传 `artifacts/reports/**` 和 `artifacts/cache/**`，便于排查失败现场。

每次日报、周报或月报会同时生成三类产物：

- `*-recap.html`：带规则化市场摘要和原始数据表的可读报告。
- `*-feishu-card.json`：可直接发送的飞书 interactive card payload。
- `*-snapshot.json`：结构化证据快照，包含明确的起止日期、数据源、警告、涨跌条目和摘要统计，供后续大模型总结或历史对比使用。

报告日期会先通过 Tushare `trade_cal` 解析为最近有效交易日。日报只查询该交易日；周报使用当周周一至报告日；月报使用当月月初至报告日，避免在未指定日期时查询无边界的历史数据。日报的市场状态只使用 Tushare `daily` 的 A 股行情计算涨跌家数；指数只展示主要基准指数，板块数量也单独标注，不再把不同类型的原始行数混成“股票数量”。

## Required Secrets

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中配置：

| Secret | 必需 | 用途 |
| --- | --- | --- |
| `TUSHARE_TOKEN` | 是 | Tushare 数据接口 token。 |
| `ANTHROPIC_API_KEY` | 是 | Claude Code / Anthropic API 调用。 |
| `FEISHU_WEBHOOK_URL` | 单群时是 | 默认飞书机器人 webhook。 |
| `FEISHU_WEBHOOK_SECRET` | 否 | 默认飞书机器人签名密钥。 |
| `FEISHU_WEBHOOKS_JSON` | 多群时推荐 | 多任务 webhook 映射。 |
| `FEISHU_DAILY_WEBHOOK_URL` | 否 | 日复盘专用 webhook，优先级最高。 |
| `FEISHU_DAILY_WEBHOOK_SECRET` | 否 | 日复盘专用签名密钥。 |
| `FEISHU_WEEKLY_WEBHOOK_URL` | 否 | 周复盘专用 webhook，优先级最高。 |
| `FEISHU_WEEKLY_WEBHOOK_SECRET` | 否 | 周复盘专用签名密钥。 |
| `FEISHU_MONTHLY_WEBHOOK_URL` | 否 | 月复盘专用 webhook，优先级最高。 |
| `FEISHU_MONTHLY_WEBHOOK_SECRET` | 否 | 月复盘专用签名密钥。 |

多群配置示例：

```json
{
  "daily": {
    "url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "secret": "daily-sign-secret"
  },
  "weekly": {
    "url": "https://open.feishu.cn/open-apis/bot/v2/hook/yyy"
  },
  "monthly": "https://open.feishu.cn/open-apis/bot/v2/hook/zzz"
}
```

飞书目标解析顺序：

1. 任务专用变量，例如 `FEISHU_DAILY_WEBHOOK_URL`。
2. `FEISHU_WEBHOOKS_JSON` 中同名任务配置。
3. 默认 `FEISHU_WEBHOOK_URL`。

如果没有任何飞书 webhook，报告仍可生成，推送步骤会被标记为 skipped。

## Local Dry Run

本地不发送飞书卡片：

```bash
python3 scripts/run_recap.py --task daily --output-dir artifacts/reports --dry-run
```

运行 PR #1 迁移后的 Tushare 课题 skill：

```bash
python3 skills/tushare-recap-reports/scripts/run.py full-chain
```

默认输出到 `artifacts/reports/tushare-recap-reports`，Tushare API 缓存放在 `artifacts/cache/tushare`。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

如果本机没有安装 `tushare` 或没有配置 `TUSHARE_TOKEN`，请在 `fallback-data/` 放置与 cache key 同名的 JSON fixture，或者只运行单元测试。

当前 Claude Code action 负责工作流编排，核心数据计算和报告渲染仍由确定性的 Python 代码完成；`*-snapshot.json` 是后续接入受约束的自然语言总结层的输入边界。

## Development Docs

- `docs/design/skz-78-recap-engineering.html`
- `docs/development/skz-78-development-record.html`
