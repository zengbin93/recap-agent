---
name: recap-weekly
description: 每周全球市场复盘，回答"本周主线如何演化、哪些主题延续或衰退"。基于 recap-data-collect 的本周数据生成周报 HTML + 飞书卡片 JSON。
---

# recap-weekly

周复盘 skill。只做周度归纳，**不替代每日异动追踪，也不做月度结构判断**。

## 何时使用
- 每周一的 08:00 编排（在 `recap-data-collect` 之后）
- 用户问"本周市场主线 / 主题演化"

## 输入
- `date`：周标识（如 `2024-W01`）
- 本周 5 个交易日数据（`recap-data-collect` 输出）
- 周度核心观点（由 Claude 填入 `sections.json`）：
  - 本周主线
  - 延续 / 衰退的主题
  - 周度风险

## 运行
```bash
python .claude/skills/recap-weekly/scripts/run.py \
  --task weekly --date 2024-W01 \
  --sections sections.json
```

## 输出
- `reports/weekly/<date>.html`
- `artifacts/cards/weekly.json`
