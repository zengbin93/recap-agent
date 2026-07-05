---
name: recap-data-collect
description: 采集 tushare 复盘数据集（日线行情、复权因子等），带 3 次重试降级与磁盘缓存。任何复盘 skill 运行前先调用它建立数据包。
---

# recap-data-collect

共享数据采集 skill。把 tushare 原始数据落成标准化 JSON + `manifest.json`，供 daily / weekly / monthly 复盘 skill 复用。**本 skill 不生成观点、HTML 或飞书卡片。**

## 何时使用
- 每日 08:00 定时编排的第一步
- 任何复盘 skill 运行前需要行情数据时

## 输入
- `as_of_date`：交易日，`YYYYMMDD`
- `datasets`：tushare API 名清单（默认 `daily,adj_factor`）
- `output_dir`：数据落盘目录

## 必需 secret
- `TUSHARE_TOKEN`

## 运行
```bash
python .claude/skills/recap-data-collect/scripts/collect.py \
  --as-of-date 20240102 \
  --output-dir data/daily
```

## 输出
- `<output_dir>/<dataset>.json`：每个数据集一个标准化 JSON（`list[dict]`）
- `<output_dir>/manifest.json`：本次采集清单（每数据集行数 + 跳过原因）

## 降级行为（关键）
- 单数据集限流 / 网络错误 / 空结果：重试 3 次（线性退避），仍失败则**跳过该数据集**并记入 `manifest.skipped`，不中断其它数据集。
- 空结果、截断缓存都不会被当作有效数据缓存或返回。
- 全部数据集都被跳过时退出码非零，方便 CI 察觉。
