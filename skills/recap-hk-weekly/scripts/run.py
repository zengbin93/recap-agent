#!/usr/bin/env python3
"""Produce a post-close Hong Kong equity weekly research recap.

The script deliberately keeps the market-data layer deterministic.  It can be
run from GitHub Actions without installing the Tushare Python SDK, and writes
every artifact under one directory so the workflow upload path is unambiguous.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from recap_agent.feishu import FeishuConfig, FeishuSender  # noqa: E402


DEFAULT_TUSHARE_URL = "http://api.tushare.pro"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "artifacts" / "cache" / "hk-weekly-recap"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "reports" / "hk-weekly-recap"
DATE_FORMAT = "%Y%m%d"
INDEX_CODES = {
    "恒生指数": "HSI",
    "恒生国企指数": "HSCEI",
    "恒生科技指数": "HSTECH",
}


class TushareError(RuntimeError):
    """A request or data-contract failure from Tushare."""


@dataclass(frozen=True)
class WeekPeriod:
    start_trade_date: str
    end_trade_date: str
    prior_trade_date: str
    trade_dates: list[str]


@dataclass
class HkWeeklyReport:
    generated_at: str
    period: WeekPeriod
    market_state: str
    market_conclusion: str
    coverage: dict[str, int]
    breadth: dict[str, float | int]
    indices: list[dict[str, Any]] = field(default_factory=list)
    industries: list[dict[str, Any]] = field(default_factory=list)
    southbound: dict[str, Any] | None = None
    connect_leaders: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    next_week_checks: list[str] = field(default_factory=list)
    data_warnings: list[str] = field(default_factory=list)
    methodology: str = "v1.0-hk-liquidity-strength"


class TushareClient:
    """Small cached REST client so the workflow has no SDK dependency."""

    def __init__(
        self,
        token: str | None = None,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        use_cache: bool = True,
        url: str | None = None,
        retries: int = 2,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self.token:
            raise TushareError("Missing TUSHARE_TOKEN. Set it in GitHub Actions or .env.")
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.url = url or os.environ.get("TUSHARE_URL") or DEFAULT_TUSHARE_URL
        self.retries = retries
        self.timeout = timeout

    def query(
        self,
        api_name: str,
        *,
        params: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        cache_key: str | None = None,
    ) -> list[dict[str, Any]]:
        cache_path = self._cache_path(api_name, cache_key)
        if self.use_cache and cache_path and cache_path.exists():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            return self._rows(raw)

        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params or {},
            "fields": ",".join(fields or []),
        }
        raw = self._post(payload)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return self._rows(raw)

    def _cache_path(self, api_name: str, cache_key: str | None) -> Path | None:
        if not cache_key:
            return None
        safe_key = cache_key.replace("/", "_").replace(":", "_")
        return self.cache_dir / api_name / f"{safe_key}.json"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(req, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(attempt + 1)
        raise TushareError(f"Tushare request failed: {last_error}")

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("code") != 0:
            raise TushareError(f"Tushare error {payload.get('code')}: {payload.get('msg')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise TushareError("Malformed Tushare response: missing data.fields/items")
        return [dict(zip(fields, item)) for item in items]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FORMAT).date()


def date_text(value: date) -> str:
    return value.strftime(DATE_FORMAT)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def latest_hk_week(client: TushareClient, end_date: date) -> WeekPeriod:
    rows = client.query(
        "hk_tradecal",
        params={
            "start_date": date_text(end_date - timedelta(days=35)),
            "end_date": date_text(end_date),
        },
        fields=["cal_date", "is_open", "pretrade_date"],
        cache_key=f"{date_text(end_date - timedelta(days=35))}_{date_text(end_date)}",
    )
    open_dates = sorted(
        {
            str(row["cal_date"])
            for row in rows
            if row.get("cal_date") and str(row.get("is_open")) == "1"
        }
    )
    if not open_dates:
        raise TushareError("hk_tradecal returned no Hong Kong trading day in the lookback window")

    final_date = parse_date(open_dates[-1])
    monday = final_date - timedelta(days=final_date.weekday())
    week_dates = [item for item in open_dates if parse_date(item) >= monday]
    prior_dates = [item for item in open_dates if parse_date(item) < monday]
    if not week_dates or not prior_dates:
        raise TushareError("Cannot determine both the Hong Kong trading week and prior close")
    return WeekPeriod(
        start_trade_date=week_dates[0],
        end_trade_date=week_dates[-1],
        prior_trade_date=prior_dates[-1],
        trade_dates=week_dates,
    )


def optional_query(
    client: TushareClient,
    api_name: str,
    *,
    params: dict[str, Any],
    fields: list[str] | None,
    cache_key: str,
    warnings: list[str],
    label: str,
) -> list[dict[str, Any]]:
    try:
        return client.query(api_name, params=params, fields=fields, cache_key=cache_key)
    except TushareError:
        warnings.append(f"{label}不可用（接口权限或数据空缺），已从结论中剔除。")
        return []


def load_hk_basic(client: TushareClient, warnings: list[str]) -> dict[str, dict[str, Any]]:
    rows = optional_query(
        client,
        "hk_basic",
        params={"list_status": "L"},
        fields=["ts_code", "name", "industry", "market", "list_date"],
        cache_key="listed",
        warnings=warnings,
        label="港股基础资料",
    )
    return {str(row["ts_code"]): row for row in rows if row.get("ts_code")}


def load_daily_prices(
    client: TushareClient, period: WeekPeriod, warnings: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    fields = [
        "ts_code",
        "trade_date",
        "close",
        "pct_change",
        "amount",
        "turnover_ratio",
        "total_mv",
    ]
    for trade_date in [period.prior_trade_date, *period.trade_dates]:
        rows = client.query(
            "hk_daily_adj",
            params={"trade_date": trade_date},
            fields=fields,
            cache_key=trade_date,
        )
        if not rows:
            raise TushareError(f"hk_daily_adj returned no rows for {trade_date}")
        by_date[trade_date] = {
            str(row["ts_code"]): row for row in rows if row.get("ts_code")
        }
    if len(by_date) != len(period.trade_dates) + 1:
        warnings.append("部分交易日行情缺失，周内持续性指标已降级。")
    return by_date


def load_indices(
    client: TushareClient, period: WeekPeriod, warnings: list[str]
) -> list[dict[str, Any]]:
    indices: list[dict[str, Any]] = []
    for name, ts_code in INDEX_CODES.items():
        rows = optional_query(
            client,
            "index_global",
            params={
                "ts_code": ts_code,
                "start_date": period.prior_trade_date,
                "end_date": period.end_trade_date,
            },
            fields=["ts_code", "trade_date", "close"],
            cache_key=f"{ts_code}_{period.prior_trade_date}_{period.end_trade_date}",
            warnings=warnings,
            label=name,
        )
        rows = sorted(rows, key=lambda row: str(row.get("trade_date", "")))
        if len(rows) < 2:
            continue
        start_close, end_close = as_float(rows[0].get("close")), as_float(rows[-1].get("close"))
        if start_close <= 0 or end_close <= 0:
            continue
        indices.append(
            {
                "name": name,
                "ts_code": ts_code,
                "start_trade_date": rows[0].get("trade_date"),
                "end_trade_date": rows[-1].get("trade_date"),
                "week_return_pct": round((end_close / start_close - 1) * 100, 2),
            }
        )
    return indices


def load_southbound(
    client: TushareClient, period: WeekPeriod, warnings: list[str]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    totals = optional_query(
        client,
        "ggt_daily",
        params={"start_date": period.start_trade_date, "end_date": period.end_trade_date},
        fields=["trade_date", "buy_amount", "buy_volume", "sell_amount", "sell_volume"],
        cache_key=f"{period.start_trade_date}_{period.end_trade_date}",
        warnings=warnings,
        label="港股通成交统计",
    )
    southbound: dict[str, Any] | None = None
    if totals:
        buy_amount = sum(as_float(row.get("buy_amount")) for row in totals)
        sell_amount = sum(as_float(row.get("sell_amount")) for row in totals)
        southbound = {
            "days": len(totals),
            "buy_amount_yi": round(buy_amount, 2),
            "sell_amount_yi": round(sell_amount, 2),
            "net_buy_yi": round(buy_amount - sell_amount, 2),
            "source": "ggt_daily",
        }

    leaders = optional_query(
        client,
        "ggt_top10",
        params={"trade_date": period.end_trade_date},
        fields=[
            "trade_date",
            "ts_code",
            "name",
            "close",
            "p_change",
            "rank",
            "market_type",
            "amount",
            "net_amount",
            "sh_net_amount",
            "sz_net_amount",
        ],
        cache_key=period.end_trade_date,
        warnings=warnings,
        label="港股通活跃标的",
    )
    leaders = sorted(leaders, key=lambda row: as_float(row.get("net_amount")), reverse=True)
    return southbound, [
        {
            "ts_code": str(row.get("ts_code", "")),
            "name": str(row.get("name") or row.get("ts_code") or "未知"),
            "net_amount": round(as_float(row.get("net_amount")), 2),
            "amount": round(as_float(row.get("amount")), 2),
            "market_type": row.get("market_type"),
        }
        for row in leaders[:5]
    ]


def build_industry_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        industry = str(row.get("industry") or "未分类")
        grouped.setdefault(industry, []).append(as_float(row["week_return_pct"]))
    stats = []
    for name, returns in grouped.items():
        if name == "未分类" or len(returns) < 3:
            continue
        stats.append(
            {
                "name": name,
                "sample_count": len(returns),
                "median_return_pct": round(median(returns), 2),
                "up_ratio_pct": round(sum(item > 0 for item in returns) / len(returns) * 100, 1),
            }
        )
    return sorted(stats, key=lambda item: item["median_return_pct"], reverse=True)


def build_candidates(
    rows: list[dict[str, Any]], industries: list[dict[str, Any]], period: WeekPeriod
) -> list[dict[str, Any]]:
    liquid_values = [as_float(row["week_amount"]) for row in rows if as_float(row["week_amount"]) > 0]
    if not liquid_values:
        return []
    liquidity_floor = percentile(liquid_values, 0.5)
    industry_map = {item["name"]: item for item in industries}
    result = []
    for row in rows:
        week_return = as_float(row["week_return_pct"])
        week_amount = as_float(row["week_amount"])
        active_days = int(row["active_days"])
        positive_days = int(row["positive_days"])
        if week_return <= 0 or week_amount < liquidity_floor or active_days == 0:
            continue
        persistence = positive_days / active_days
        industry = str(row.get("industry") or "未分类")
        industry_stat = industry_map.get(industry)
        industry_support = 15 if industry_stat and industry_stat["median_return_pct"] > 0 and industry_stat["up_ratio_pct"] >= 55 else 0
        liquidity_ratio = week_amount / median(liquid_values)
        score = (
            min(45.0, max(0.0, week_return * 3))
            + min(25.0, math.log10(1 + liquidity_ratio) * 35)
            + persistence * 15
            + industry_support
        )
        list_date = str(row.get("list_date") or "")
        new_listing = False
        try:
            new_listing = (parse_date(period.end_trade_date) - parse_date(list_date)).days < 180
        except ValueError:
            pass
        if new_listing:
            score -= 10
        reason_parts = [f"周涨幅 {week_return:+.1f}%", f"{positive_days}/{active_days} 个交易日上涨"]
        if liquidity_ratio >= 1:
            reason_parts.append(f"成交活跃度为样本中位数的 {liquidity_ratio:.1f} 倍")
        if industry_support:
            reason_parts.append("行业广度同步改善")
        risk = "驱动待核验：行情与港股通数据不能替代公告、业绩或新闻证据。"
        if new_listing:
            risk = "上市未满 180 日，价格发现与流动性稳定性待验证。"
        result.append(
            {
                "ts_code": row["ts_code"],
                "name": row["name"],
                "industry": industry,
                "market": row.get("market") or "未分类",
                "score": round(score, 1),
                "week_return_pct": round(week_return, 2),
                "positive_days": positive_days,
                "active_days": active_days,
                "liquidity_ratio": round(liquidity_ratio, 2),
                "why_now": "；".join(reason_parts) + "。",
                "first_rejection": "若下周跌破本周低点且成交活跃度回落至样本中位数以下，移出研究池。",
                "risk": risk,
                "next_check": "核验业绩披露、公司公告和真实催化；确认相对强度与成交活跃度是否延续。",
            }
        )
    ranked = sorted(result, key=lambda item: (item["score"], item["week_return_pct"]), reverse=True)
    for rank, item in enumerate(ranked[:10], start=1):
        item["rank"] = rank
    return ranked[:10]


def market_state(breadth: dict[str, float | int]) -> tuple[str, str]:
    median_return = as_float(breadth["median_return_pct"])
    up_ratio = as_float(breadth["up_ratio_pct"])
    if median_return >= 2 and up_ratio >= 60:
        return "偏强扩散", "收益中位数与上涨家数同步改善，风险偏好偏强；仍需观察成交活跃度能否延续。"
    if median_return <= -2 and up_ratio <= 40:
        return "偏弱收缩", "收益中位数和上涨家数同时走弱，优先控制流动性与下行风险。"
    return "结构分化", "市场并非单边：强势标的需要同时通过流动性和持续性筛选，不能只按涨幅追踪。"


def build_hk_weekly_report(client: TushareClient, end_date: date) -> HkWeeklyReport:
    warnings: list[str] = []
    period = latest_hk_week(client, end_date)
    basics = load_hk_basic(client, warnings)
    daily = load_daily_prices(client, period, warnings)
    prior_rows = daily[period.prior_trade_date]
    final_rows = daily[period.end_trade_date]
    prices: list[dict[str, Any]] = []
    for ts_code, final in final_rows.items():
        prior = prior_rows.get(ts_code)
        prior_close = as_float(prior.get("close")) if prior else 0.0
        final_close = as_float(final.get("close"))
        if prior_close <= 0 or final_close <= 0:
            continue
        metadata = basics.get(ts_code, {})
        active_rows = [daily[item].get(ts_code) for item in period.trade_dates]
        active_rows = [row for row in active_rows if row and as_float(row.get("close")) > 0]
        prices.append(
            {
                "ts_code": ts_code,
                "name": str(metadata.get("name") or ts_code),
                "industry": metadata.get("industry"),
                "market": metadata.get("market"),
                "list_date": metadata.get("list_date"),
                "week_return_pct": (final_close / prior_close - 1) * 100,
                "week_amount": sum(as_float(row.get("amount")) for row in active_rows),
                "positive_days": sum(as_float(row.get("pct_change")) > 0 for row in active_rows),
                "active_days": len(active_rows),
            }
        )
    if not prices:
        raise TushareError("No Hong Kong stocks have valid prior and week-end adjusted closes")

    up = sum(as_float(row["week_return_pct"]) > 0 for row in prices)
    down = sum(as_float(row["week_return_pct"]) < 0 for row in prices)
    flat = len(prices) - up - down
    returns = [as_float(row["week_return_pct"]) for row in prices]
    breadth: dict[str, float | int] = {
        "sample_count": len(prices),
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "up_ratio_pct": round(up / len(prices) * 100, 1),
        "median_return_pct": round(median(returns), 2),
    }
    state, conclusion = market_state(breadth)
    industries = build_industry_stats(prices)
    candidates = build_candidates(prices, industries, period)
    indices = load_indices(client, period, warnings)
    southbound, connect_leaders = load_southbound(client, period, warnings)
    if not basics:
        warnings.append("行业与名称资料缺失，强势研究池以代码和流动性指标展示。")
    if not candidates:
        warnings.append("没有标的同时满足周度上涨与样本中位数以上的成交活跃度，未生成强势研究池。")

    checks = [
        "观察上涨家数是否继续高于 55%，避免只由少数权重股带动。",
        "确认强势研究池能否守住本周低点，并维持不低于样本中位数的成交活跃度。",
    ]
    if southbound:
        checks.append("比较下周港股通买卖差额与本周，避免把单周成交变化当作长期资金趋势。")
    if indices:
        checks.append("对照恒生、国企与科技指数的相对表现，判断风险偏好是否从局部扩散。")
    return HkWeeklyReport(
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        period=period,
        market_state=state,
        market_conclusion=conclusion,
        coverage={
            "week_end_rows": len(final_rows),
            "prior_close_rows": len(prior_rows),
            "computable_sample": len(prices),
            "basic_coverage": len(basics),
        },
        breadth=breadth,
        indices=indices,
        industries=industries,
        southbound=southbound,
        connect_leaders=connect_leaders,
        candidates=candidates,
        next_week_checks=checks,
        data_warnings=list(dict.fromkeys(warnings)),
    )


def pct(value: Any) -> str:
    return f"{as_float(value):+.1f}%"


def table_rows(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return '<p class="empty">暂无可复核数据</p>'
    header = "".join(f"<th>{escape(label)}</th>" for _key, label in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{escape(str(row.get(key, '—')))}</td>" for key, _label in columns)
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: HkWeeklyReport) -> str:
    period = report.period
    breadth = report.breadth
    index_rows = [
        {"name": item["name"], "return": pct(item["week_return_pct"]), "date": item["end_trade_date"]}
        for item in report.indices
    ]
    industry_rows = [
        {
            "name": item["name"],
            "median": pct(item["median_return_pct"]),
            "breadth": f"{item['up_ratio_pct']:.1f}%",
            "count": item["sample_count"],
        }
        for item in report.industries[:6]
    ]
    candidates = "".join(
        "<article class=\"candidate\">"
        f"<h3>#{item['rank']} {escape(item['name'])} <code>{escape(item['ts_code'])}</code></h3>"
        f"<p class=\"score\">研究分 {item['score']:.1f} · 本周 <strong>{pct(item['week_return_pct'])}</strong> · {escape(item['industry'])}</p>"
        f"<p><strong>为什么进入研究池：</strong>{escape(item['why_now'])}</p>"
        f"<p><strong>首先剔除条件：</strong>{escape(item['first_rejection'])}</p>"
        f"<p><strong>证据边界：</strong>{escape(item['risk'])}</p>"
        f"<p><strong>下周验证：</strong>{escape(item['next_check'])}</p>"
        "</article>"
        for item in report.candidates[:5]
    ) or '<p class="empty">本周没有满足流动性与持续性门槛的研究对象。</p>'
    southbound = "数据不可用"
    if report.southbound:
        southbound = (
            f"港股通买入 <strong>{report.southbound['buy_amount_yi']:.1f}</strong> 亿，"
            f"卖出 <strong>{report.southbound['sell_amount_yi']:.1f}</strong> 亿，"
            f"买卖差额 <strong>{report.southbound['net_buy_yi']:+.1f}</strong> 亿"
        )
    warning_html = "".join(f"<li>{escape(item)}</li>" for item in report.data_warnings) or "<li>无</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>港股周复盘</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#172033;background:#f8fafc;line-height:1.6}}
header,section{{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;margin:18px 0}} h1,h2,h3{{margin-top:0}}h1{{font-size:30px}}h2{{font-size:21px;border-left:4px solid #1677ff;padding-left:10px}}.subtle{{color:#64748b}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.metric{{background:#eff6ff;border-radius:10px;padding:14px}}.metric b{{font-size:22px;display:block}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px;border-bottom:1px solid #e5e7eb}}th{{color:#64748b}}.candidate{{border-top:1px solid #e5e7eb;padding:16px 0}}.candidate:first-of-type{{border-top:0}}.candidate p{{margin:7px 0}}.score{{color:#475569}}code{{font-size:.9em}}.empty{{color:#64748b}}.warning{{background:#fff7ed;border-color:#fed7aa}}@media(max-width:640px){{.grid{{grid-template-columns:1fr}}}}</style></head>
<body><header><h1>港股周复盘</h1><p class="subtle">交易周：{period.start_trade_date} — {period.end_trade_date} · 上周末：{period.prior_trade_date} · 生成：{escape(report.generated_at)}</p>
<h2>本周结论：{escape(report.market_state)}</h2><p>{escape(report.market_conclusion)}</p></header>
<section><div class="grid"><div class="metric"><span>可计算样本</span><b>{breadth['sample_count']}</b><small>周末与上周末都有有效收盘价</small></div><div class="metric"><span>上涨家数</span><b>{breadth['up_ratio_pct']:.1f}%</b><small>{breadth['up_count']} 涨 / {breadth['down_count']} 跌 / {breadth['flat_count']} 平</small></div><div class="metric"><span>样本收益中位数</span><b>{pct(breadth['median_return_pct'])}</b><small>不等同于指数涨跌</small></div></div></section>
<section><h2>市场温度</h2><h3>主要指数</h3>{table_rows(index_rows, [("name", "指数"), ("return", "本周变动"), ("date", "可用截止日")])}<h3>行业广度（有行业资料且样本不少于 3 只）</h3>{table_rows(industry_rows, [("name", "行业"), ("median", "收益中位数"), ("breadth", "上涨占比"), ("count", "样本")])}</section>
<section><h2>港股通活动</h2><p>{southbound} <span class="subtle">（Tushare ggt_daily 口径，非港股全市场资金流。）</span></p>{table_rows(report.connect_leaders, [("name", "活跃标的"), ("ts_code", "代码"), ("net_amount", "净额"), ("amount", "成交额")])}</section>
<section><h2>强势研究池</h2><p class="subtle">先过成交活跃度、周内持续性和行业广度筛选；以下不是买入建议，也不把涨幅当作上涨原因。</p>{candidates}</section>
<section><h2>下周验证条件</h2><ol>{''.join(f'<li>{escape(item)}</li>' for item in report.next_week_checks)}</ol></section>
<section class="warning"><h2>数据边界与状态</h2><ul>{warning_html}</ul><p>AH 折溢价、公司公告、业绩与新闻催化尚未接入经过校验的统一来源，因此不在本周结论中推断因果。</p></section>
</body></html>"""


def build_feishu_card(report: HkWeeklyReport) -> dict[str, Any]:
    period = report.period
    breadth = report.breadth
    lines = [
        f"**交易周** `{period.start_trade_date} - {period.end_trade_date}`  ·  截止 `{period.end_trade_date}`",
        f"**本周结论：{report.market_state}**  {report.market_conclusion}",
        "",
        "### 市场温度",
        f"- **可计算样本** {breadth['sample_count']} · **上涨 / 下跌 / 平盘** {breadth['up_count']} / {breadth['down_count']} / {breadth['flat_count']}",
        f"- **上涨占比** {breadth['up_ratio_pct']:.1f}% · **收益中位数** {pct(breadth['median_return_pct'])}",
    ]
    if report.indices:
        lines.append("- **指数** " + " · ".join(f"{item['name']} {pct(item['week_return_pct'])}" for item in report.indices))
    if report.southbound:
        lines.append(
            f"- **港股通活动** 买入 {report.southbound['buy_amount_yi']:.1f} 亿 / 卖出 {report.southbound['sell_amount_yi']:.1f} 亿 / 差额 **{report.southbound['net_buy_yi']:+.1f} 亿**"
        )
    else:
        lines.append("- **港股通活动** 数据不可用，未作资金方向判断")

    candidate_lines = ["### 强势研究池（流动性筛选后，非买入建议）"]
    if report.candidates:
        for item in report.candidates[:3]:
            candidate_lines.extend(
                [
                    f"**#{item['rank']} {item['name']}** `{item['ts_code']}`  ·  **{pct(item['week_return_pct'])}** · 研究分 `{item['score']:.1f}`",
                    f"{item['why_now']}",
                    f"_风险：{item['risk']}_",
                ]
            )
    else:
        candidate_lines.append("本周无同时满足上涨、持续性和成交活跃度门槛的标的。")
    check_lines = ["### 下周先看什么", *[f"- {item}" for item in report.next_week_checks[:3]]]
    warning_lines = []
    if report.data_warnings:
        warning_lines = ["### 数据状态", *[f"- ⚠️ {item}" for item in report.data_warnings[:3]]]
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": "blue" if report.market_state == "偏强扩散" else "orange",
                "title": {"tag": "plain_text", "content": "港股周复盘"},
            },
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {"tag": "hr"},
                {"tag": "markdown", "content": "\n".join(candidate_lines)},
                {"tag": "hr"},
                {"tag": "markdown", "content": "\n".join(check_lines)},
                *([{"tag": "hr"}, {"tag": "markdown", "content": "\n".join(warning_lines)}] if warning_lines else []),
            ],
        },
    }


def write_report_files(report: HkWeeklyReport, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = asdict(report)
    html_path = output_dir / "latest.html"
    json_path = output_dir / "latest.json"
    card_path = output_dir / "latest-card.json"
    csv_path = output_dir / "latest.csv"
    html_path.write_text(render_html(report), encoding="utf-8")
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    card_path.write_text(json.dumps(build_feishu_card(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank", "name", "ts_code", "industry", "market", "score", "week_return_pct", "positive_days", "active_days", "liquidity_ratio", "why_now", "first_rejection", "risk", "next_check",
            ],
        )
        writer.writeheader()
        writer.writerows(report.candidates)
    return {"html": str(html_path), "snapshot": str(json_path), "card": str(card_path), "csv": str(csv_path)}


def current_hk_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", help="YYYYMMDD; defaults to the current Hong Kong date")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Render the Feishu payload without posting it")
    parser.add_argument("--skip-feishu", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    as_of = parse_date(args.end_date) if args.end_date else current_hk_date()
    client = TushareClient(cache_dir=args.cache_dir, use_cache=not args.no_cache)
    report = build_hk_weekly_report(client, as_of)
    files = write_report_files(report, args.output_dir)
    feishu: dict[str, Any] = {"skipped": True}
    if not args.skip_feishu:
        try:
            target = FeishuConfig.from_env().resolve("hk-weekly")
        except ValueError:
            feishu = {"skipped": True, "reason": "no hk-weekly, weekly, or default webhook configured"}
        else:
            result = FeishuSender(dry_run=args.dry_run).send(target, build_feishu_card(report))
            feishu = result.__dict__
    print(
        json.dumps(
            {
                "period": asdict(report.period),
                "market_state": report.market_state,
                "sample_count": report.breadth["sample_count"],
                "candidate_count": len(report.candidates),
                "warnings": report.data_warnings,
                "files": files,
                "feishu": feishu,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
