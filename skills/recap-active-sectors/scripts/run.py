#!/usr/bin/env python3
"""Deep-dive report for the day's most actively-traded THS sectors.

Pipeline (see SKZ-149):
  1. Take the top-N A-shares by turnover (Tushare ``daily.amount``).
  2. Map each of those stocks to its 同花顺 concept/industry sectors and count
     how often each sector shows up. Sectors hit ``--min-count`` times or more
     are "active sectors".
  3. For every active sector pull the THS sector index (``ths_daily``) and score
     today's plus the recent 5-day move.
  4. For every active sector pick a few representative constituents (the ones in
     the turnover leaderboard first) and describe their recent move.

The script is self-contained: it ships its own tiny Tushare client (urllib +
cache + retry), mirroring ``skills/tushare-recap-reports``. It expects
``TUSHARE_TOKEN`` in the environment or a repo-root ``.env``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "artifacts" / "cache" / "tushare"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "reports" / "recap-active-sectors"
TUSHARE_URL = "http://api.tushare.pro"
DATE_FMT = "%Y%m%d"

# 同花顺板块类型：N 概念指数、I 行业指数（其余如 R 地域、S 特色默认不纳入）。
SECTOR_TYPE_LABELS = {"N": "概念", "I": "行业", "R": "地域", "S": "特色", "T": "同花顺主题"}

# 宽基指数成分 / 互联互通 / 交易属性类板块——几乎覆盖所有大盘股，命中数天然很高，
# 但不代表当日热点，属噪音，默认从活跃板块中剔除（名称子串匹配）。
BROAD_SECTOR_KEYWORDS = (
    "融资融券", "两融", "转融券", "标的证券",
    "深股通", "沪股通", "陆股通", "港股通", "互联互通",
    "样本股", "成份股", "成分股",
    "沪深300", "中证500", "中证1000", "中证100", "中证800", "上证50", "上证180",
    "上证380", "创业板指", "创业板50", "科创50", "科创100", "深证100", "MSCI",
    "富时", "标普", "QFII", "AH股", "AB股", "预盈预增", "预亏预减",
)


def is_broad_sector(name: str) -> bool:
    text = name or ""
    return any(kw in text for kw in BROAD_SECTOR_KEYWORDS)


class TushareError(RuntimeError):
    pass


class TushareClient:
    """Minimal Tushare Pro HTTP client with on-disk cache and retry."""

    def __init__(
        self,
        token: str | None = None,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        use_cache: bool = True,
        retries: int = 2,
        retry_sleep: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self.token:
            raise TushareError("Missing TUSHARE_TOKEN. Set it in the environment or repo-root .env.")
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.retries = retries
        self.retry_sleep = retry_sleep
        self.timeout = timeout

    def query(
        self,
        api_name: str,
        *,
        params: dict[str, Any] | None = None,
        fields: list[str] | str | None = None,
        cache_key: str | None = None,
    ) -> list[dict[str, Any]]:
        params = params or {}
        fields_text = ",".join(fields) if isinstance(fields, list) else fields or ""
        cache_path = self._cache_path(api_name, cache_key)
        if self.use_cache and cache_path and cache_path.exists():
            return self._rows_from_payload(json.loads(cache_path.read_text(encoding="utf-8")))

        payload = {"api_name": api_name, "token": self.token, "params": params, "fields": fields_text}
        raw = self._post(payload)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self._rows_from_payload(raw)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(TUSHARE_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        last_error: Exception | None = None
        for attempt in range(1, max(1, self.retries + 1) + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                last_error = error
                if attempt <= self.retries:
                    time.sleep(self.retry_sleep * attempt)
        raise TushareError(f"Tushare request failed: {last_error}")

    def _cache_path(self, api_name: str, cache_key: str | None) -> Path | None:
        if not cache_key:
            return None
        safe_key = cache_key.replace("/", "_").replace(":", "_")
        return self.cache_dir / api_name / f"{safe_key}.json"

    def _rows_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("code") != 0:
            raise TushareError(f"Tushare error {payload.get('code')}: {payload.get('msg')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise TushareError("Malformed Tushare payload: missing data.fields/items")
        return [dict(zip(fields, item)) for item in items]


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
@dataclass
class TopStock:
    rank: int
    ts_code: str
    name: str
    amount_yi: float  # 成交额（亿元）
    pct_chg: float
    close: float


@dataclass
class RepStock:
    ts_code: str
    name: str
    in_top: bool  # 是否在成交额榜内
    pct_chg: float  # 今日涨跌幅
    recent_pct: float  # 近 N 日累计涨跌幅
    amount_yi: float


@dataclass
class ActiveSector:
    rank: int
    index_code: str
    name: str
    sector_type: str  # N / I ...
    type_label: str  # 概念 / 行业
    hit_count: int
    hit_stocks: list[str]  # 命中的成交额榜个股名称
    today_pct: float  # 板块今日涨跌幅
    recent_pct: float  # 板块近 N 日累计涨跌幅
    amplitude: float  # 近 N 日振幅
    quote_available: bool
    representatives: list[RepStock] = field(default_factory=list)


@dataclass
class ActiveSectorsReport:
    generated_at: str
    trade_date: str
    top_n: int
    min_count: int
    recent_days: int
    sector_types: list[str]
    top_stock_count: int
    active_sector_count: int
    sectors: list[ActiveSector] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def yyyymmdd(value: date) -> str:
    return value.strftime(DATE_FMT)


def parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, DATE_FMT).date()


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def is_bj(ts_code: str) -> bool:
    return ts_code.endswith(".BJ")


def resolve_trade_date(client: TushareClient, trade_date: str | None) -> str:
    """Return the requested trade date, or the latest open SSE date up to today."""
    if trade_date:
        return trade_date
    today = datetime.now().date()
    start = yyyymmdd(today - timedelta(days=15))
    rows = client.query(
        "trade_cal",
        params={"exchange": "SSE", "start_date": start, "end_date": yyyymmdd(today), "is_open": "1"},
        fields=["cal_date", "is_open"],
        cache_key=f"SSE_{start}_{yyyymmdd(today)}_open",
    )
    dates = sorted(row["cal_date"] for row in rows if str(row.get("is_open")) == "1")
    if not dates:
        raise RuntimeError("No open SSE trading date found in the last 15 days")
    return dates[-1]


def recent_open_dates(client: TushareClient, trade_date: str, days: int) -> list[str]:
    """Return the last ``days`` open trading dates ending at ``trade_date`` (inclusive)."""
    end = parse_yyyymmdd(trade_date)
    start = yyyymmdd(end - timedelta(days=days * 2 + 20))
    rows = client.query(
        "trade_cal",
        params={"exchange": "SSE", "start_date": start, "end_date": trade_date, "is_open": "1"},
        fields=["cal_date", "is_open"],
        cache_key=f"SSE_{start}_{trade_date}_open",
    )
    dates = sorted(row["cal_date"] for row in rows if str(row.get("is_open")) == "1")
    return dates[-days:]


# --------------------------------------------------------------------------- #
# Step 1 — turnover leaderboard
# --------------------------------------------------------------------------- #
def load_stock_basic(client: TushareClient) -> dict[str, dict[str, Any]]:
    rows = client.query(
        "stock_basic",
        params={"exchange": "", "list_status": "L"},
        fields=["ts_code", "symbol", "name", "market", "list_date"],
        cache_key="listed",
    )
    return {row["ts_code"]: row for row in rows}


def load_top_amount_stocks(
    client: TushareClient,
    trade_date: str,
    *,
    top_n: int,
    exclude_st: bool,
    exclude_bj: bool,
    basic: dict[str, dict[str, Any]],
) -> list[TopStock]:
    rows = client.query(
        "daily",
        params={"trade_date": trade_date},
        fields=["ts_code", "trade_date", "close", "pct_chg", "amount"],
        cache_key=trade_date,
    )
    if not rows:
        raise RuntimeError(f"No daily rows for trade_date {trade_date}")
    # amount 单位千元，转亿元；降序排序。
    rows.sort(key=lambda r: parse_float(r.get("amount")), reverse=True)
    result: list[TopStock] = []
    for row in rows:
        ts_code = row["ts_code"]
        info = basic.get(ts_code, {})
        name = str(info.get("name") or ts_code)
        if exclude_bj and is_bj(ts_code):
            continue
        if exclude_st and is_st(name):
            continue
        result.append(
            TopStock(
                rank=len(result) + 1,
                ts_code=ts_code,
                name=name,
                amount_yi=round(parse_float(row.get("amount")) / 1e5, 2),
                pct_chg=round(parse_float(row.get("pct_chg")), 2),
                close=round(parse_float(row.get("close")), 2),
            )
        )
        if len(result) >= top_n:
            break
    return result


# --------------------------------------------------------------------------- #
# Step 2 — stock -> THS sector inverted index
# --------------------------------------------------------------------------- #
def load_sector_index(
    client: TushareClient, sector_types: list[str], *, exclude_broad: bool = True
) -> list[dict[str, Any]]:
    sectors: list[dict[str, Any]] = []
    for stype in sector_types:
        rows = client.query(
            "ths_index",
            params={"type": stype, "exchange": "A"},
            fields=["ts_code", "name", "count", "type"],
            cache_key=f"type_{stype}",
        )
        for row in rows:
            row["type"] = row.get("type") or stype
            if exclude_broad and is_broad_sector(str(row.get("name") or "")):
                continue
            sectors.append(row)
    return sectors


def build_membership(
    client: TushareClient,
    sector_index: list[dict[str, Any]],
    *,
    trade_date: str,
    throttle: float,
    progress: Callable[[str], None] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Build a ``con_code -> [{index_code, name, type}]`` inverted index.

    The full sweep over ``ths_member`` is heavy, so results are cached per sector
    (independent of ``trade_date``; membership changes slowly) and rate-limited.
    """
    membership: dict[str, list[dict[str, str]]] = defaultdict(list)
    total = len(sector_index)
    for pos, sector in enumerate(sector_index, start=1):
        index_code = sector["ts_code"]
        try:
            members = client.query(
                "ths_member",
                params={"ts_code": index_code},
                fields=["ts_code", "con_code", "con_name"],
                cache_key=index_code,
            )
        except TushareError as error:
            if progress:
                progress(f"skip {index_code}: {error}")
            continue
        meta = {"index_code": index_code, "name": str(sector.get("name") or index_code), "type": str(sector.get("type") or "")}
        for member in members:
            con_code = member.get("con_code")
            if con_code:
                membership[con_code].append(meta)
        if progress and pos % 50 == 0:
            progress(f"membership {pos}/{total}")
        if throttle:
            time.sleep(throttle)
    return membership


# --------------------------------------------------------------------------- #
# Step 3 — aggregate active sectors
# --------------------------------------------------------------------------- #
def aggregate_active_sectors(
    top_stocks: list[TopStock],
    membership: dict[str, list[dict[str, str]]],
    *,
    min_count: int,
) -> list[dict[str, Any]]:
    hits: dict[str, dict[str, Any]] = {}
    for stock in top_stocks:
        for meta in membership.get(stock.ts_code, []):
            index_code = meta["index_code"]
            bucket = hits.setdefault(
                index_code,
                {"index_code": index_code, "name": meta["name"], "type": meta["type"], "stocks": []},
            )
            bucket["stocks"].append(stock.name)
    active = [h for h in hits.values() if len(h["stocks"]) >= min_count]
    active.sort(key=lambda h: len(h["stocks"]), reverse=True)
    return active


# --------------------------------------------------------------------------- #
# Step 4 — sector index quote
# --------------------------------------------------------------------------- #
def analyze_sector_quote(
    client: TushareClient,
    index_code: str,
    open_dates: list[str],
) -> dict[str, Any]:
    if not open_dates:
        return {"available": False, "today_pct": 0.0, "recent_pct": 0.0, "amplitude": 0.0}
    start, end = open_dates[0], open_dates[-1]
    try:
        rows = client.query(
            "ths_daily",
            params={"ts_code": index_code, "start_date": start, "end_date": end},
            fields=["ts_code", "trade_date", "close", "pre_close", "high", "low", "pct_change"],
            cache_key=f"{index_code}_{start}_{end}",
        )
    except TushareError:
        return {"available": False, "today_pct": 0.0, "recent_pct": 0.0, "amplitude": 0.0}
    rows = [r for r in rows if r.get("trade_date") in set(open_dates)]
    rows.sort(key=lambda r: r["trade_date"])
    if not rows:
        return {"available": False, "today_pct": 0.0, "recent_pct": 0.0, "amplitude": 0.0}
    today = rows[-1]
    first_close = parse_float(rows[0].get("pre_close")) or parse_float(rows[0].get("close"))
    last_close = parse_float(today.get("close"))
    recent_pct = ((last_close - first_close) / first_close * 100.0) if first_close else 0.0
    highs = [parse_float(r.get("high")) for r in rows if parse_float(r.get("high"))]
    lows = [parse_float(r.get("low")) for r in rows if parse_float(r.get("low"))]
    amplitude = ((max(highs) - min(lows)) / min(lows) * 100.0) if highs and lows and min(lows) else 0.0
    return {
        "available": True,
        "today_pct": round(parse_float(today.get("pct_change")), 2),
        "recent_pct": round(recent_pct, 2),
        "amplitude": round(amplitude, 2),
    }


# --------------------------------------------------------------------------- #
# Step 5 — representative constituents
# --------------------------------------------------------------------------- #
def pick_representatives(
    client: TushareClient,
    index_code: str,
    top_lookup: dict[str, TopStock],
    *,
    open_dates: list[str],
    limit: int,
    basic: dict[str, dict[str, Any]],
) -> list[RepStock]:
    try:
        members = client.query(
            "ths_member",
            params={"ts_code": index_code},
            fields=["ts_code", "con_code", "con_name"],
            cache_key=index_code,
        )
    except TushareError:
        return []
    con_codes = [m.get("con_code") for m in members if m.get("con_code")]
    # 优先取落在成交额榜内的成分股（贡献活跃度的龙头），再按榜内排名补齐。
    in_top = [c for c in con_codes if c in top_lookup]
    in_top.sort(key=lambda c: top_lookup[c].rank)
    chosen = in_top[:limit]
    reps: list[RepStock] = []
    for con_code in chosen:
        top = top_lookup[con_code]
        recent_pct = _recent_stock_pct(client, con_code, open_dates)
        reps.append(
            RepStock(
                ts_code=con_code,
                name=top.name,
                in_top=True,
                pct_chg=top.pct_chg,
                recent_pct=recent_pct,
                amount_yi=top.amount_yi,
            )
        )
    return reps


def _recent_stock_pct(client: TushareClient, ts_code: str, open_dates: list[str]) -> float:
    if not open_dates:
        return 0.0
    start, end = open_dates[0], open_dates[-1]
    try:
        rows = client.query(
            "daily",
            params={"ts_code": ts_code, "start_date": start, "end_date": end},
            fields=["ts_code", "trade_date", "close", "pre_close"],
            cache_key=f"{ts_code}_{start}_{end}",
        )
    except TushareError:
        return 0.0
    rows = [r for r in rows if r.get("trade_date") in set(open_dates)]
    rows.sort(key=lambda r: r["trade_date"])
    if not rows:
        return 0.0
    first_close = parse_float(rows[0].get("pre_close")) or parse_float(rows[0].get("close"))
    last_close = parse_float(rows[-1].get("close"))
    return round(((last_close - first_close) / first_close * 100.0) if first_close else 0.0, 2)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_report(
    client: TushareClient,
    *,
    trade_date: str | None,
    top_n: int,
    min_count: int,
    recent_days: int,
    sector_types: list[str],
    rep_stocks: int,
    throttle: float,
    exclude_broad: bool = True,
    max_sectors: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> ActiveSectorsReport:
    resolved_date = resolve_trade_date(client, trade_date)
    if progress:
        progress(f"trade_date={resolved_date}")
    basic = load_stock_basic(client)
    top_stocks = load_top_amount_stocks(
        client, resolved_date, top_n=top_n, exclude_st=True, exclude_bj=False, basic=basic
    )
    top_lookup = {s.ts_code: s for s in top_stocks}
    if progress:
        progress(f"top stocks={len(top_stocks)}")

    sector_index = load_sector_index(client, sector_types, exclude_broad=exclude_broad)
    if progress:
        progress(f"sector index={len(sector_index)}")
    membership = build_membership(
        client, sector_index, trade_date=resolved_date, throttle=throttle, progress=progress
    )
    active = aggregate_active_sectors(top_stocks, membership, min_count=min_count)
    if max_sectors is not None and max_sectors > 0:
        active = active[:max_sectors]
    if progress:
        progress(f"active sectors={len(active)}")

    open_dates = recent_open_dates(client, resolved_date, recent_days)
    sectors: list[ActiveSector] = []
    for rank, item in enumerate(active, start=1):
        quote = analyze_sector_quote(client, item["index_code"], open_dates)
        reps = pick_representatives(
            client, item["index_code"], top_lookup, open_dates=open_dates, limit=rep_stocks, basic=basic
        )
        stype = item["type"]
        sectors.append(
            ActiveSector(
                rank=rank,
                index_code=item["index_code"],
                name=item["name"],
                sector_type=stype,
                type_label=SECTOR_TYPE_LABELS.get(stype, stype or "-"),
                hit_count=len(item["stocks"]),
                hit_stocks=item["stocks"],
                today_pct=quote["today_pct"],
                recent_pct=quote["recent_pct"],
                amplitude=quote["amplitude"],
                quote_available=quote["available"],
                representatives=reps,
            )
        )

    return ActiveSectorsReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        trade_date=resolved_date,
        top_n=top_n,
        min_count=min_count,
        recent_days=recent_days,
        sector_types=sector_types,
        top_stock_count=len(top_stocks),
        active_sector_count=len(sectors),
        sectors=sectors,
    )


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def write_json_report(report: ActiveSectorsReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv_report(report: ActiveSectorsReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "rank", "index_code", "name", "type_label", "hit_count",
        "today_pct", "recent_pct", "amplitude", "hit_stocks", "representatives",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for s in report.sectors:
            writer.writerow(
                {
                    "rank": s.rank,
                    "index_code": s.index_code,
                    "name": s.name,
                    "type_label": s.type_label,
                    "hit_count": s.hit_count,
                    "today_pct": s.today_pct,
                    "recent_pct": s.recent_pct,
                    "amplitude": s.amplitude,
                    "hit_stocks": "、".join(s.hit_stocks),
                    "representatives": "、".join(f"{r.name}({fmt_pct(r.recent_pct)})" for r in s.representatives),
                }
            )


def render_html(report: ActiveSectorsReport) -> str:
    rows = []
    for s in report.sectors:
        reps = "".join(
            f"<li><strong>{escape(r.name)}</strong> 今日 {fmt_pct(r.pct_chg)} / {report.recent_days}日 {fmt_pct(r.recent_pct)}"
            f"<span>{escape(r.ts_code)} · 成交 {r.amount_yi:g} 亿</span></li>"
            for r in s.representatives
        )
        quote = (
            f"{fmt_pct(s.today_pct)}<span>{report.recent_days}日 {fmt_pct(s.recent_pct)} / 振幅 {fmt_pct(s.amplitude)}</span>"
            if s.quote_available
            else "<span>板块行情不可用</span>"
        )
        rows.append(
            "<tr>"
            f"<td>{s.rank}</td>"
            f"<td><strong>{escape(s.name)}</strong><span>{escape(s.index_code)} · {escape(s.type_label)}</span></td>"
            f"<td class='gain'>{s.hit_count}</td>"
            f"<td>{quote}</td>"
            f"<td>{escape('、'.join(s.hit_stocks))}</td>"
            f"<td><ul>{reps or '<li>无榜内成分股</li>'}</ul></td>"
            "</tr>"
        )
    table_body = "\n".join(rows) or "<tr><td colspan='6' class='empty'>今日无满足阈值的活跃板块。</td></tr>"
    table = (
        "<table><thead><tr><th>排名</th><th>板块</th><th>命中数</th>"
        f"<th>今日/{report.recent_days}日</th><th>命中个股</th><th>代表成分股</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>"
    )
    cards = [
        ("交易日", report.trade_date),
        ("成交额榜", f"前 {report.top_n}"),
        ("活跃阈值", f"≥ {report.min_count} 次"),
        ("活跃板块数", str(report.active_sector_count)),
        ("板块口径", "/".join(SECTOR_TYPE_LABELS.get(t, t) for t in report.sector_types)),
    ]
    cards_html = "".join(
        f"<div class='card'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in cards
    )
    note = (
        "活跃板块 = 成交额榜前 N 只个股在同花顺概念/行业板块上的命中次数达阈值。"
        "板块与个股行情来自 Tushare（未做复权），用于复盘研究，不构成投资建议。"
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>成交活跃板块复盘 {escape(report.trade_date)}</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#20242a; --muted:#667085; --line:#dde2e8; --gain:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    header {{ padding:28px 36px 18px; background:var(--panel); border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:28px; }} .subtitle {{ margin:0; color:var(--muted); line-height:1.7; }}
    main {{ padding:22px 36px 42px; }} .cards {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; margin-bottom:18px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
    .card span, td span {{ display:block; color:var(--muted); font-size:12px; margin-top:4px; }} .card strong {{ font-size:22px; }}
    .table-wrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; min-width:1080px; border-collapse:collapse; }}
    th,td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#f9fafb; color:#475467; font-size:13px; }} tr:last-child td {{ border-bottom:0; }}
    .gain {{ color:var(--gain); font-weight:750; font-size:18px; }} ul {{ margin:0; padding-left:18px; }} li {{ margin-bottom:6px; }}
    .empty {{ text-align:center; color:var(--muted); padding:32px; }} .note {{ margin-top:14px; color:var(--muted); font-size:13px; line-height:1.7; }}
    @media (max-width:900px) {{ header,main {{ padding-left:18px; padding-right:18px; }} .cards {{ grid-template-columns:repeat(2,minmax(140px,1fr)); }} }}
  </style>
</head>
<body><header><h1>成交活跃板块复盘 · {escape(report.trade_date)}</h1>
<p class="subtitle">成交额榜前 {report.top_n} 只个股聚合出的同花顺活跃概念/行业板块，及其行情与代表成分股。</p></header>
<main><section class="cards">{cards_html}</section><section class="table-wrap">{table}</section><p class="note">{escape(note)}</p></main></body></html>
"""


def build_feishu_card(report: ActiveSectorsReport, *, top: int = 10) -> dict[str, Any]:
    """Render the report into a Feishu interactive card payload.

    Shape matches ``recap_agent.reports`` / ``feishu-card-push`` (``msg_type``
    ``interactive`` with a ``card`` body), so it can be pushed as-is.
    """
    def arrow(v: float) -> str:
        return "🔺" if v > 0 else ("🔻" if v < 0 else "▪️")

    lines: list[str] = []
    for s in report.sectors[:top]:
        reps = "、".join(r.name for r in s.representatives[:3]) or "—"
        quote = (
            f"今日 {arrow(s.today_pct)}{fmt_pct(s.today_pct)} / {report.recent_days}日 {fmt_pct(s.recent_pct)}"
            if s.quote_available
            else "行情不可用"
        )
        lines.append(
            f"**{s.rank}. {s.name}**（{s.type_label}·命中 {s.hit_count}）\n{quote}｜代表：{reps}"
        )
    body = "\n\n".join(lines) or "今日无满足阈值的活跃板块。"
    overview = (
        f"**交易日**：{report.trade_date}　**成交额榜**：前 {report.top_n}\n"
        f"**活跃阈值**：≥ {report.min_count} 次　**活跃板块**：{report.active_sector_count} 个"
        f"（展示前 {min(top, report.active_sector_count)}）"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": f"成交活跃板块复盘 · {report.trade_date}"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": overview}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "lark_md",
                            "content": "活跃板块 = 成交额榜前 N 个股在同花顺概念/行业上的命中次数达阈值（已剔宽基）。数据来自 Tushare，仅供复盘研究，不构成投资建议。",
                        }
                    ],
                },
            ],
        },
    }


def output_paths(base_dir: Path) -> tuple[Path, Path, Path]:
    return base_dir / "latest.html", base_dir / "latest.csv", base_dir / "latest.json"


def run(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(args.token, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    sector_types = [t.strip().upper() for t in str(args.sector_types).split(",") if t.strip()]
    report = build_report(
        client,
        trade_date=args.trade_date,
        top_n=args.top_n,
        min_count=args.min_count,
        recent_days=args.recent_days,
        sector_types=sector_types,
        rep_stocks=args.rep_stocks,
        throttle=args.throttle,
        exclude_broad=not args.include_broad,
        max_sectors=args.max_sectors,
        progress=print if args.progress else None,
    )
    html_path, csv_path, json_path = output_paths(args.output_dir)
    card_path = args.output_dir / "latest-card.json"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(report), encoding="utf-8")
    write_csv_report(report, csv_path)
    write_json_report(report, json_path)
    card = build_feishu_card(report, top=args.card_top)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "trade_date": report.trade_date,
        "active_sectors": str(report.active_sector_count),
        "html": str(html_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "card": str(card_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="深度分析成交活跃板块（同花顺概念/行业）。")
    parser.add_argument("--token", default=None, help="Tushare token；默认取 TUSHARE_TOKEN")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Tushare 缓存目录")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="报告输出目录")
    parser.add_argument("--no-cache", action="store_true", help="禁用本地缓存")
    parser.add_argument("--trade-date", default=None, help="交易日 YYYYMMDD；默认最近开市日")
    parser.add_argument("--top-n", type=int, default=100, help="成交额榜取前 N 只")
    parser.add_argument("--min-count", type=int, default=5, help="板块被判定为活跃的最少命中次数")
    parser.add_argument("--recent-days", type=int, default=5, help="近 N 个交易日区间")
    parser.add_argument("--sector-types", default="N,I", help="板块类型，逗号分隔：N 概念 / I 行业")
    parser.add_argument("--rep-stocks", type=int, default=5, help="每个板块代表成分股数量上限")
    parser.add_argument("--max-sectors", type=int, default=40, help="最多输出的活跃板块数（按命中数取前 N，0 为不限）")
    parser.add_argument("--include-broad", action="store_true", help="保留宽基指数/互联互通/交易属性类板块（默认剔除）")
    parser.add_argument("--card-top", type=int, default=10, help="飞书卡片展示的活跃板块数量")
    parser.add_argument("--throttle", type=float, default=0.0, help="ths_member 调用间隔秒数（限频用）")
    parser.add_argument("--progress", action="store_true", help="打印进度")
    parser.set_defaults(func=run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except (TushareError, FileNotFoundError, KeyError, RuntimeError) as error:
        print(f"错误：{error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
