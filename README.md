# recap-agent
全球市场复盘系统，一手数据源

## 课题 01：一个股票要想涨 10 倍，先涨 1 倍

目标：先关注市场最近半年涨幅超过 1 倍的股票池，并生成 HTML 复盘报告。

数据源：Tushare Pro。

## 配置

本地开发使用 `.env` 保存密钥：

```bash
cp .env.example .env
```

然后把 `.env` 里的 `TUSHARE_TOKEN` 改成自己的 Tushare token。`.env`、缓存和报告产物都不会进入 git。

GitHub Actions 使用仓库 Secret：

1. 打开 GitHub 仓库的 `Settings -> Secrets and variables -> Actions`。
2. 新建 repository secret，名称填 `TUSHARE_TOKEN`。
3. 值填 Tushare token。

工作流在 `.github/workflows/recap.yml`，支持手动触发，也会在交易日 16:30（北京时间）自动运行。运行结束后，报告会作为 `stock-recap-reports` artifact 上传。

## 本地运行

### 使用

先设置 Tushare token：

```bash
export TUSHARE_TOKEN="你的 tushare token"
```

也可以复制 `.env.example` 为 `.env`，把 token 写进去。

运行：

```bash
python3 scripts/run_first_double.py
```

默认输出：

- `reports/first_double/latest.html`
- `reports/first_double/latest.csv`
- `reports/first_double/latest.json`

常用参数：

```bash
# 指定统计截止日
python3 scripts/run_first_double.py --end-date 20260529

# 调整回看天数和涨幅阈值
python3 scripts/run_first_double.py --lookback-days 183 --min-pct-change 100

# 只保留涨幅 100% 到 150% 的股票
python3 scripts/run_first_double.py --min-pct-change 100 --max-pct-change 150

# 不使用本地缓存，重新拉取 Tushare
python3 scripts/run_first_double.py --no-cache
```

当前口径：使用 Tushare `daily` 收盘价，按最近 183 个自然日覆盖的交易区间计算涨幅；停牌股票使用区间内第一条和最后一条可用日线。后续可以继续扩展为前复权涨幅、成交额过滤、涨停路径、龙虎榜和公告事件归因。

## 课题 02：十倍潜力跟踪池

目标：在“最近半年已翻倍”的股票里，继续筛选更值得深挖的二阶段候选。

运行：

```bash
python3 scripts/run_tenbagger_watch.py
```

默认读取 `reports/first_double/latest.json`，并输出：

- `reports/tenbagger_watch/latest.html`
- `reports/tenbagger_watch/latest.csv`
- `reports/tenbagger_watch/latest.json`

当前评分模型已经代码化：

- 阶段位置：涨幅 120%-350% 优先，过热降权。
- 空间：流通市值 20-150 亿优先，市值越大十倍难度越高。
- 趋势质量：离阶段高点越近越好，深回撤降权。
- 资金参与：自由流通换手适中、量比温和优先。
- 产业想象力：半导体、通信设备、元器件、电气设备、软件服务、专用机械等优先。
- 风险扣分：ST/退市风险、北交所流动性差异、过高估值等。

说明：这是复盘研究队列，不构成投资建议。下一步可以把财报增速、前复权涨幅、公告催化、资金流和龙虎榜继续接入模型。

## GitHub Actions

CI 默认执行完整链路：

```bash
python scripts/run_first_double.py --min-pct-change 100 --max-pct-change 150
python scripts/run_tenbagger_watch.py
```

也就是先生成“近半年涨幅 100%-150% 股票池”，再从这个池子里生成“十倍潜力跟踪池”。产物路径仍然是：

- `reports/first_double/latest.html`
- `reports/first_double/latest.csv`
- `reports/first_double/latest.json`
- `reports/tenbagger_watch/latest.html`
- `reports/tenbagger_watch/latest.csv`
- `reports/tenbagger_watch/latest.json`
