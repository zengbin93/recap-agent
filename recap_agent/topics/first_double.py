"""Topic: stocks that doubled before becoming potential tenbaggers."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Callable

from recap_agent.tushare_client import TushareClient


DATE_FMT = "%Y%m%d"


@dataclass
class FirstDoubleCandidate:
    rank: int
    ts_code: str
    name: str
    industry: str
    market: str
    list_date: str
    start_trade_date: str
    end_trade_date: str
    start_close: float
    end_close: float
    pct_change: float
    max_close: float
    max_trade_date: str
    max_gain: float
    pullback_from_high: float
    trading_days: int


@dataclass
class FirstDoubleReport:
    generated_at: str
    lookback_days: int
    min_pct_change: float
    max_pct_change: float | None
    start_date: str
    end_date: str
    start_trade_date: str
    end_trade_date: str
    stock_count: int
    stocks_with_prices: int
    candidate_count: int
    candidates: list[FirstDoubleCandidate]


def yyyymmdd(value: date) -> str:
    return value.strftime(DATE_FMT)


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FMT).date()


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pick_trade_dates(client: TushareClient, start_date: str, end_date: str) -> list[str]:
    rows = client.query(
        "trade_cal",
        params={
            "exchange": "SSE",
            "start_date": start_date,
            "end_date": end_date,
            "is_open": "1",
        },
        fields=["cal_date", "is_open"],
        cache_key=f"SSE_{start_date}_{end_date}_open",
    )
    dates = sorted(row["cal_date"] for row in rows if str(row.get("is_open")) == "1")
    if not dates:
        raise RuntimeError(f"No open trading dates between {start_date} and {end_date}")
    return dates


def load_stock_basic(client: TushareClient) -> dict[str, dict[str, Any]]:
    rows = client.query(
        "stock_basic",
        params={"exchange": "", "list_status": "L"},
        fields=["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
        cache_key="listed",
    )
    return {row["ts_code"]: row for row in rows}


def load_daily_by_date(client: TushareClient, trade_date: str) -> list[dict[str, Any]]:
    return client.query(
        "daily",
        params={"trade_date": trade_date},
        fields=["ts_code", "trade_date", "close"],
        cache_key=trade_date,
    )


def build_first_double_report(
    client: TushareClient,
    *,
    end_date: date | None = None,
    lookback_days: int = 183,
    min_pct_change: float = 100.0,
    max_pct_change: float | None = None,
    progress: Callable[[str], None] | None = None,
) -> FirstDoubleReport:
    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=lookback_days)
    trade_dates = pick_trade_dates(client, yyyymmdd(start_date), yyyymmdd(end_date))
    stock_basic = load_stock_basic(client)

    by_stock: dict[str, list[tuple[str, float]]] = {}
    for index, trade_date in enumerate(trade_dates, start=1):
        if progress:
            progress(f"拉取日线 {index}/{len(trade_dates)}：{trade_date}")
        for row in load_daily_by_date(client, trade_date):
            close = parse_float(row.get("close"))
            if close <= 0:
                continue
            by_stock.setdefault(row["ts_code"], []).append((row["trade_date"], close))

    candidates: list[FirstDoubleCandidate] = []
    for ts_code, prices in by_stock.items():
        prices.sort(key=lambda item: item[0])
        if len(prices) < 2:
            continue
        start_trade_date, start_close = prices[0]
        end_trade_date, end_close = prices[-1]
        if start_close <= 0:
            continue
        pct_change = (end_close / start_close - 1.0) * 100.0
        if pct_change < min_pct_change:
            continue
        if max_pct_change is not None and pct_change > max_pct_change:
            continue
        max_trade_date, max_close = max(prices, key=lambda item: item[1])
        max_gain = (max_close / start_close - 1.0) * 100.0
        pullback_from_high = (end_close / max_close - 1.0) * 100.0 if max_close else 0.0
        basic = stock_basic.get(ts_code, {})
        candidates.append(
            FirstDoubleCandidate(
                rank=0,
                ts_code=ts_code,
                name=str(basic.get("name") or ""),
                industry=str(basic.get("industry") or ""),
                market=str(basic.get("market") or ""),
                list_date=str(basic.get("list_date") or ""),
                start_trade_date=start_trade_date,
                end_trade_date=end_trade_date,
                start_close=round(start_close, 3),
                end_close=round(end_close, 3),
                pct_change=round(pct_change, 2),
                max_close=round(max_close, 3),
                max_trade_date=max_trade_date,
                max_gain=round(max_gain, 2),
                pullback_from_high=round(pullback_from_high, 2),
                trading_days=len(prices),
            )
        )

    candidates.sort(key=lambda item: item.pct_change, reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate.rank = index

    return FirstDoubleReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lookback_days=lookback_days,
        min_pct_change=min_pct_change,
        max_pct_change=max_pct_change,
        start_date=yyyymmdd(start_date),
        end_date=yyyymmdd(end_date),
        start_trade_date=trade_dates[0],
        end_trade_date=trade_dates[-1],
        stock_count=len(stock_basic),
        stocks_with_prices=len(by_stock),
        candidate_count=len(candidates),
        candidates=candidates,
    )


def write_json(report: FirstDoubleReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(report: FirstDoubleReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(candidate) for candidate in report.candidates]
    columns = list(rows[0].keys()) if rows else [field.name for field in FirstDoubleCandidate.__dataclass_fields__.values()]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def format_pct(value: float) -> str:
    return f"{value:.2f}%"


def format_price(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def render_html(report: FirstDoubleReport) -> str:
    rows = []
    for candidate in report.candidates:
        rows.append(
            "<tr>"
            f"<td>{candidate.rank}</td>"
            f"<td><strong>{escape(candidate.name)}</strong><span>{escape(candidate.ts_code)}</span></td>"
            f"<td>{escape(candidate.industry)}</td>"
            f"<td>{escape(candidate.market)}</td>"
            f"<td>{escape(candidate.start_trade_date)}<span>{format_price(candidate.start_close)}</span></td>"
            f"<td>{escape(candidate.end_trade_date)}<span>{format_price(candidate.end_close)}</span></td>"
            f"<td class='gain'>{format_pct(candidate.pct_change)}</td>"
            f"<td>{escape(candidate.max_trade_date)}<span>{format_pct(candidate.max_gain)}</span></td>"
            f"<td class='pullback'>{format_pct(candidate.pullback_from_high)}</td>"
            f"<td>{candidate.trading_days}</td>"
            "</tr>"
        )

    table_body = "\n".join(rows) or (
        "<tr><td colspan='10' class='empty'>没有找到最近半年涨幅超过 100% 的股票。</td></tr>"
    )

    pct_scope = f"{report.min_pct_change:.2f}% 以上"
    if report.max_pct_change is not None:
        pct_scope = f"{report.min_pct_change:.2f}% - {report.max_pct_change:.2f}%"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>半年翻倍股票池</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #68717d;
      --line: #dde2e8;
      --accent: #0f766e;
      --gain: #b42318;
      --pullback: #475467;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      padding: 28px 36px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      font-weight: 750;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    main {{ padding: 22px 36px 40px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(5, minmax(140px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .card strong {{ font-size: 24px; }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1100px;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f9fafb;
      color: #475467;
      font-size: 13px;
      font-weight: 650;
    }}
    td span {{
      display: block;
      color: var(--muted);
      margin-top: 4px;
      font-size: 12px;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .gain {{ color: var(--gain); font-weight: 750; }}
    .pullback {{ color: var(--pullback); }}
    .empty {{
      text-align: center;
      color: var(--muted);
      padding: 32px;
    }}
    .note {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 900px) {{
      header, main {{ padding-left: 18px; padding-right: 18px; }}
      .cards {{ grid-template-columns: repeat(2, minmax(140px, 1fr)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>一个股票要想涨 10 倍，先涨 1 倍</h1>
    <p class="subtitle">
      课题 01：筛选最近半年区间涨幅在 {escape(pct_scope)} 的 A 股股票池。数据来自 Tushare Pro 日线行情。
    </p>
  </header>
  <main>
    <section class="cards">
      <div class="card"><span>统计区间</span><strong>{report.start_trade_date} - {report.end_trade_date}</strong></div>
      <div class="card"><span>自然日回看</span><strong>{report.lookback_days}</strong></div>
      <div class="card"><span>上市股票数</span><strong>{report.stock_count}</strong></div>
      <div class="card"><span>有行情股票数</span><strong>{report.stocks_with_prices}</strong></div>
      <div class="card"><span>涨幅区间</span><strong>{escape(pct_scope)}</strong></div>
      <div class="card"><span>入选股票数</span><strong>{report.candidate_count}</strong></div>
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>股票</th>
            <th>行业</th>
            <th>市场</th>
            <th>区间起点</th>
            <th>区间终点</th>
            <th>区间涨幅</th>
            <th>期间高点</th>
            <th>高点回撤</th>
            <th>交易天数</th>
          </tr>
        </thead>
        <tbody>
          {table_body}
        </tbody>
      </table>
    </section>
    <p class="note">
      说明：当前版本使用 Tushare <code>daily</code> 收盘价计算，未做复权处理；停牌股票使用区间内第一条和最后一条可用日线。
      后续可以继续扩展为前复权涨幅、成交额过滤、行业聚类、涨停板路径、龙虎榜和公告事件归因。
      报告生成时间：{escape(report.generated_at)}。
    </p>
  </main>
</body>
</html>
"""


def write_html(report: FirstDoubleReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
