---
name: recap-monthly
description: 每月全球市场复盘，回答"本月市场结构、宏观和资金风格如何变化"。基于 recap-data-collect 的月度数据生成月报 HTML + 飞书卡片 JSON。
---

# recap-monthly

月复盘 skill。只做月度结构判断，**不承担日内或单周热点解释**。

## 何时使用
- 每月首个交易日的 08:00 编排（在 `recap-data-collect` 之后）
- 用户问"本月市场结构 / 风格切换"

## 输入
- `date`：月份（如 `2024-01`）
- 本月指数 / 行业 / 月度宏观数据（`recap-data-collect` 输出）
- 月度核心观点（由 Claude 填入 `sections.json`）：
  - 结构性变化
  - 资金 / 风格
  - 月度风险

## 运行
```bash
python .claude/skills/recap-monthly/scripts/run.py \
  --task monthly --date 2024-01 \
  --sections sections.json
```

## 输出
- `reports/monthly/<date>.html`
- `artifacts/cards/monthly.json`
