---
name: verify
summary: 通过 CLI 和本地 Tushare 兼容 HTTP 服务验证数据采集路径。
---

# Verify RecapAgent

1. 启动仅监听 `127.0.0.1` 随机端口的 `ThreadingHTTPServer`，记录 POST JSON 并返回 Tushare `{code,data}` 响应。
2. 以 `TUSHARE_TOKEN`、`TUSHARE_URL` 环境变量运行真实 CLI：
   `python3 skills/recap-active-sectors/scripts/run.py --trade-date YYYYMMDD --no-cache --cache-dir <tmp> --output-dir <tmp>`。
3. 确认 CLI 退出码为 0、代理收到预期 `api_name`/token，并生成 HTML、CSV、JSON、飞书卡片。
4. 再运行 `skills/tushare-recap-reports/scripts/run.py first-double`，确认首个 `trade_cal` 请求也到达代理。

使用 `tempfile.TemporaryDirectory()` 隔离缓存和产物；验证时不调用真实 Tushare 或飞书接口。
