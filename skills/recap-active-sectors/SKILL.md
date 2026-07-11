---
name: recap-active-sectors
description: Deep-dive the day's most actively-traded 同花顺 (THS) concept/industry sectors — identify active sectors from the turnover leaderboard, then analyze sector index moves and representative constituents.
---

# Recap Active Sectors

Use this skill to build the daily "成交活跃板块" deep-dive report (SKZ-149).

活跃板块定义与分析口径：

1. 用 Tushare `daily.amount` 取当日 A 股成交额排序前 100 的股票。
2. 把这 100 只股票映射到其所属的同花顺概念/行业板块，统计每个板块被命中的次数，
   保留命中 **≥ 3 次** 的板块作为「活跃板块」。
3. 对每个活跃板块，用 `ths_daily` 分析板块指数今日与最近 5 日的表现。
4. 对每个活跃板块，挑选落在成交额榜内的代表成分股，分析其近 5 日表现。

脚本自包含，内置一个带缓存/重试的 Tushare 客户端，产出 HTML/CSV/JSON。
需要环境变量 `TUSHARE_TOKEN`（或 repo 根 `.env`）。

> ⚠️ 依赖 `ths_index / ths_member / ths_daily` 接口，需相应 Tushare 积分权限。

## Run

```bash
python3 skills/recap-active-sectors/scripts/run.py
```

常用参数：

```bash
python3 skills/recap-active-sectors/scripts/run.py \
  --trade-date 20260710 \
  --top-n 100 --min-count 3 --recent-days 5 \
  --sector-types N,I --rep-stocks 5 \
  --throttle 0.3 --progress
```

- `--trade-date`：默认取最近开市日。
- `--top-n`：成交额榜取前 N（默认 100）。
- `--min-count`：板块判活跃的最少命中次数（默认 5）。
- `--max-sectors`：最多输出的活跃板块数，按命中数取前 N（默认 40，`0` 为不限）。
- `--sector-types`：`N` 概念、`I` 行业，逗号分隔（默认 `N,I`）。
- `--include-broad`：保留宽基指数/互联互通/交易属性类板块（默认剔除，见下）。
- `--throttle`：`ths_member` 全量扫描的调用间隔秒数，用于规避分钟级限频。

> 默认剔除「融资融券、深股通/沪股通、沪深300/中证500 样本股」等宽基指数成分与
> 交易属性类板块——它们几乎覆盖所有大盘股、命中数天然最高，但不代表当日热点。

默认输出目录：`artifacts/reports/recap-active-sectors`（`latest.{html,csv,json}`）。
默认缓存目录：`artifacts/cache/tushare`。个股→板块倒排索引按板块缓存，成分变化慢，
当日多次运行可直接命中缓存。
