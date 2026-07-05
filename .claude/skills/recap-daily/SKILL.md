---
name: recap-daily
description: 每日全球市场复盘，回答"最近一个交易日发生了什么、热点和风险是什么"。基于 recap-data-collect 的数据生成日报 HTML + 飞书卡片 JSON。
---

# recap-daily

每日复盘 skill。只回答日频视角的异动与热点，**不承担周/月趋势归因**。

## 何时使用
- 每日 08:00 编排，在 `recap-data-collect` 之后
- 用户问"昨天 / 最近一个交易日市场怎么样"

## 输入
- `as_of_date`：交易日
- `data-dir`：`recap-data-collect` 的输出
- 当日核心观点（由 Claude 基于数据填入 `sections.json`）：
  - 核心结论 3–5 条
  - 热点主题
  - 风险提示

## 运行
```bash
# 1) Claude 基于采集数据生成 sections.json，例如：
#    [
#      {"heading": "核心结论", "bullets": ["大盘放量上涨", "..."]},
#      {"heading": "热点主题", "bullets": ["AI 算力领涨", "..."]},
#      {"heading": "风险提示", "bullets": ["北向资金净流出"], "risk": true}
#    ]
# 2) 渲染 HTML + 卡片：
python .claude/skills/recap-daily/scripts/run.py \
  --task daily --date 2024-01-02 \
  --sections sections.json
```

## 输出
- `reports/daily/<date>.html`
- `artifacts/cards/daily.json`（供 `feishu-card-push` 推送）
