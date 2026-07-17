---
name: recap-hk-weekly
description: Generate a post-close Hong Kong equity weekly research recap using Hong Kong trading days, market breadth, liquidity, Stock Connect activity, and a liquid-strength research queue. Use for weekly Hong Kong market reviews or a scheduled Hong Kong recap; do not use for A-share reports or trade execution.
---

# 港股周复盘

运行收盘后的确定性脚本；它会同时生成 HTML、证据快照和飞书卡片。

```bash
python3 skills/recap-hk-weekly/scripts/run.py \
  --output-dir artifacts/reports/hk-weekly-recap
```

手工复盘历史周时，传入周末或该周最后一个港股交易日：

```bash
python3 skills/recap-hk-weekly/scripts/run.py \
  --end-date 20260717 \
  --dry-run \
  --output-dir artifacts/reports/hk-weekly-recap
```

## 工作准则

1. 以 `hk_tradecal` 决定周度截止日；不能用“周五”或 A 股交易日替代。若本周无港股交易日，停止并报错，不发送空报告。
2. 全量候选样本以 `hk_daily_adj` 在周末和上周末均有有效收盘价的标的为准。报告必须写出“可计算样本”，不得称为完整 HKEX 全量。
3. “强势研究池”同时要求周涨幅、周内持续性和相对成交活跃度。它是待研究队列，**不是买入名单**。
4. `ggt_daily` 是港股通成交统计；仅将买卖差额表述为“港股通净买入（Tushare 口径）”。`ggt_top10` 仅用于补充本周末活跃标的。
5. 价格和资金数据不能证明上涨的新闻或基本面原因。没有公告/业绩/新闻证据时，明确写“驱动待核验”，不得编造催化剂。
6. 报告要区分：**本周结论**、**市场温度**、**值得跟踪的强势股**、**下周验证条件**、**数据边界**。飞书卡片使用 Markdown 的标题、粗体和分隔线来突出这些层次。

## 数据降级

`hk_basic`、`index_global`、`ggt_daily` 和 `ggt_top10` 为增强数据。无权限、缺数据或接口异常时，行情主报告仍可生成，但卡片必须展示相应的数据状态；不要把缺失值填成 0，也不要据此给出资金或指数结论。

AH 折溢价、公司业绩和公告催化需要经过验证的独立数据源。本技能当前不把未经校验的接口结果写入结论，避免混淆币种、复权和披露时点。

## 产物与交付检查

输出目录必须包含：

- `latest.html`：可读的完整周报；
- `latest.json`：用于复核计算范围和数据状态的快照；
- `latest-card.json`：飞书 interactive card；
- `latest.csv`：强势研究池明细。

计划任务在每周五 18:30（Asia/Shanghai）触发。工作流完成后，确认 artifact 中存在以上四个文件，并以 `FEISHU_HK_WEEKLY_WEBHOOK_URL` 优先投递；未设置时才回退到通用周报 webhook。
