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
import math
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

from recap_agent.tracker import evaluate_sector_risk


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "artifacts" / "cache" / "tushare"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "reports" / "recap-active-sectors"
DEFAULT_TUSHARE_URL = "http://api.tushare.pro"
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
    # 选股名单、持仓标签和统计指数会天然复用大成交股，但不能解释当日资金正在
    # 围绕什么产业主题交易；将它们和宽基一样排除。
    "持股", "漂亮100", "新质50", "出海50", "果指数", "中国AI 50",
    "年报", "季报", "预增", "预减", "高股息", "国企改革", "百强", "金股",
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
        url: str | None = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        use_cache: bool = True,
        retries: int = 2,
        retry_sleep: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self.token:
            raise TushareError("Missing TUSHARE_TOKEN. Set it in the environment or repo-root .env.")
        self.url = url or os.environ.get("TUSHARE_URL") or DEFAULT_TUSHARE_URL
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
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
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
    sector_size: int  # 同花顺该板块的有效成分股数
    coverage_pct: float  # 成交额榜对板块成分的覆盖率
    turnover_yi: float  # 命中个股在成交额榜内的成交额合计
    turnover_share_pct: float  # 占成交额榜 Top N 的成交额比例
    baseline_hit_count: float | None  # 过去 N-1 个交易日的平均命中数
    hit_change: float | None  # 相对上述平均命中数的变化
    today_pct: float  # 板块今日涨跌幅
    recent_pct: float  # 板块近 N 日累计涨跌幅
    amplitude: float  # 近 N 日振幅
    quote_available: bool
    related_sectors: list[str] = field(default_factory=list)  # 高重合、已折叠的标签
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
    active_sector_count: int  # 所有满足阈值的候选板块数（未截断）
    theme_cluster_count: int  # 按成分股重合度聚类后的主题数（未截断）
    displayed_sector_count: int  # 当前 artifacts 中实际输出的主题数
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
    """Return the requested trade date, or the latest open SSE date up to today with settled data."""
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
    dates = sorted([row["cal_date"] for row in rows if str(row.get("is_open")) == "1"], reverse=True)
    if not dates:
        raise RuntimeError("No open SSE trading date found in the last 15 days")
    for d in dates:
        try:
            daily_rows = client.query("daily", params={"trade_date": d}, fields=["ts_code"], cache_key=f"check_{d}")
            if daily_rows:
                return d
        except Exception:
            pass
    return dates[0]


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
        member_codes = {str(member["con_code"]) for member in members if member.get("con_code")}
        meta = {
            "index_code": index_code,
            "name": str(sector.get("name") or index_code),
            "type": str(sector.get("type") or ""),
            # THS 的 ths_index.count 并非所有行业分类都提供；直接用成员表的
            # 去重计数，保证概念和行业使用同一分母。
            "member_count": str(len(member_codes)),
        }
        for con_code in member_codes:
            membership[con_code].append(meta)
        if progress and pos % 50 == 0:
            progress(f"membership {pos}/{total}")
        if throttle:
            time.sleep(throttle)
    return membership


# --------------------------------------------------------------------------- #
# Step 3 — aggregate active sectors
# --------------------------------------------------------------------------- #
def aggregate_sector_hits(
    top_stocks: list[TopStock],
    membership: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Return one unfiltered bucket per THS label for a turnover leaderboard."""
    hits: dict[str, dict[str, Any]] = {}
    top_turnover_yi = sum(stock.amount_yi for stock in top_stocks)
    for stock in top_stocks:
        for meta in membership.get(stock.ts_code, []):
            index_code = meta["index_code"]
            bucket = hits.setdefault(
                index_code,
                {
                    "index_code": index_code,
                    "name": meta["name"],
                    "type": meta["type"],
                    "sector_size": int(meta.get("member_count") or 0),
                    "stocks": [],
                },
            )
            if all(existing.ts_code != stock.ts_code for existing in bucket["stocks"]):
                bucket["stocks"].append(stock)

    result: list[dict[str, Any]] = []
    for bucket in hits.values():
        stocks: list[TopStock] = bucket["stocks"]
        hit_count = len(stocks)
        turnover_yi = round(sum(stock.amount_yi for stock in stocks), 2)
        sector_size = int(bucket["sector_size"])
        coverage_pct = round(hit_count / sector_size * 100, 2) if sector_size else 0.0
        turnover_share_pct = round(turnover_yi / top_turnover_yi * 100, 2) if top_turnover_yi else 0.0
        # 覆盖率将天然很大的父行业降权；sqrt(hit_count) 保留成交集中度，避免
        # 仅由少数小盘股组成的极小概念获得不成比例的高分。
        activity_score = round(
            coverage_pct * math.sqrt(hit_count) * math.sqrt(max(turnover_share_pct, 0.1)),
            4,
        )
        result.append(
            {
                **bucket,
                "hit_count": hit_count,
                "hit_codes": [stock.ts_code for stock in stocks],
                "hit_stocks": [stock.name for stock in stocks],
                "turnover_yi": turnover_yi,
                "turnover_share_pct": turnover_share_pct,
                "coverage_pct": coverage_pct,
                "activity_score": activity_score,
            }
        )
    return result


def aggregate_active_sectors(
    top_stocks: list[TopStock],
    membership: dict[str, list[dict[str, str]]],
    *,
    min_count: int,
) -> list[dict[str, Any]]:
    """Keep sufficiently represented labels and rank them by normalized activity."""
    hits = aggregate_sector_hits(top_stocks, membership)
    active = [h for h in hits if h["hit_count"] >= min_count]
    active.sort(key=lambda h: (h["activity_score"], h["hit_count"], h["turnover_yi"]), reverse=True)
    return active


def annotate_historical_activity(
    client: TushareClient,
    active: list[dict[str, Any]],
    membership: dict[str, list[dict[str, str]]],
    *,
    open_dates: list[str],
    top_n: int,
    basic: dict[str, dict[str, Any]],
) -> None:
    """Attach prior-day average leaderboard hits without failing the daily report."""
    prior_dates = open_dates[:-1]
    if not prior_dates:
        for item in active:
            item["baseline_hit_count"] = None
            item["hit_change"] = None
        return
    history: dict[str, list[int]] = {item["index_code"]: [] for item in active}
    for trade_date in prior_dates:
        try:
            historical_top = load_top_amount_stocks(
                client,
                trade_date,
                top_n=top_n,
                exclude_st=True,
                exclude_bj=False,
                basic=basic,
            )
        except (RuntimeError, TushareError):
            continue
        counts = {
            item["index_code"]: item["hit_count"]
            for item in aggregate_sector_hits(historical_top, membership)
        }
        for index_code, values in history.items():
            values.append(counts.get(index_code, 0))
    for item in active:
        values = history[item["index_code"]]
        if not values:
            item["baseline_hit_count"] = None
            item["hit_change"] = None
            continue
        baseline = round(sum(values) / len(values), 2)
        item["baseline_hit_count"] = baseline
        item["hit_change"] = round(item["hit_count"] - baseline, 2)


def jaccard(left: list[str], right: list[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def cluster_overlapping_sectors(
    active: list[dict[str, Any]], *, overlap_threshold: float = 0.5
) -> list[dict[str, Any]]:
    """Greedily keep one representative for strongly overlapping THS labels."""
    clusters: list[dict[str, Any]] = []
    for candidate in active:
        best_cluster: dict[str, Any] | None = None
        best_overlap = 0.0
        for cluster in clusters:
            overlap = max(
                jaccard(candidate["hit_codes"], label["hit_codes"])
                for label in cluster["labels"]
            )
            if overlap > best_overlap:
                best_overlap, best_cluster = overlap, cluster
        if best_cluster and best_overlap >= overlap_threshold:
            best_cluster["labels"].append(candidate)
            best_cluster["related"].append(candidate)
            continue
        clusters.append(
            {
                "representative": candidate,
                "labels": [candidate],
                "related": [],
            }
        )
    collapsed: list[dict[str, Any]] = []
    for cluster in clusters:
        representative = dict(cluster["representative"])
        representative["related_sectors"] = [item["name"] for item in cluster["related"]]
        collapsed.append(representative)
    return collapsed


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
    open_dates = recent_open_dates(client, resolved_date, recent_days)
    active = aggregate_active_sectors(top_stocks, membership, min_count=min_count)
    annotate_historical_activity(
        client,
        active,
        membership,
        open_dates=open_dates,
        top_n=top_n,
        basic=basic,
    )
    clusters = cluster_overlapping_sectors(active)
    if max_sectors is not None and max_sectors > 0:
        visible = clusters[:max_sectors]
    else:
        visible = clusters
    if progress:
        progress(
            f"active candidates={len(active)} theme clusters={len(clusters)} displayed={len(visible)}"
        )

    sectors: list[ActiveSector] = []
    for rank, item in enumerate(visible, start=1):
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
                hit_count=item["hit_count"],
                hit_stocks=item["hit_stocks"],
                sector_size=item["sector_size"],
                coverage_pct=item["coverage_pct"],
                turnover_yi=item["turnover_yi"],
                turnover_share_pct=item["turnover_share_pct"],
                baseline_hit_count=item["baseline_hit_count"],
                hit_change=item["hit_change"],
                today_pct=quote["today_pct"],
                recent_pct=quote["recent_pct"],
                amplitude=quote["amplitude"],
                quote_available=quote["available"],
                related_sectors=item["related_sectors"],
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
        active_sector_count=len(active),
        theme_cluster_count=len(clusters),
        displayed_sector_count=len(sectors),
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
        "rank", "index_code", "name", "type_label", "hit_count", "sector_size",
        "coverage_pct", "turnover_yi", "turnover_share_pct", "baseline_hit_count",
        "hit_change", "today_pct", "recent_pct", "amplitude", "related_sectors",
        "hit_stocks", "representatives",
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
                    "sector_size": s.sector_size,
                    "coverage_pct": s.coverage_pct,
                    "turnover_yi": s.turnover_yi,
                    "turnover_share_pct": s.turnover_share_pct,
                    "baseline_hit_count": s.baseline_hit_count,
                    "hit_change": s.hit_change,
                    "today_pct": s.today_pct,
                    "recent_pct": s.recent_pct,
                    "amplitude": s.amplitude,
                    "related_sectors": "、".join(s.related_sectors),
                    "hit_stocks": "、".join(s.hit_stocks),
                    "representatives": "、".join(f"{r.name}({fmt_pct(r.recent_pct)})" for r in s.representatives),
                }
            )


def render_html(report: ActiveSectorsReport) -> str:
    rows = []
    for s in report.sectors:
        sector_dict = asdict(s)
        sig = evaluate_sector_risk(sector_dict)
        risk_tag_html = f"<span class='{sig.tag_class}' title='{escape(sig.reason)}'>{escape(sig.tag_label)}</span>" if sig.risk_level != "normal" else ""

        if not s.quote_available:
            flow_tag_html = f"<span class='tag-flat'>方向未知</span>{risk_tag_html}"
        elif s.today_pct > 0:
            flow_tag_html = f"<span class='tag-up'>主力流入</span>{risk_tag_html}"
        elif s.today_pct < 0:
            flow_tag_html = f"<span class='tag-down'>主力流出</span>{risk_tag_html}"
        else:
            flow_tag_html = f"<span class='tag-flat'>多空平衡</span>{risk_tag_html}"

        reps_items = []
        for r in s.representatives:
            r_today_class = "text-up" if r.pct_chg > 0 else "text-down" if r.pct_chg < 0 else ""
            r_recent_class = "text-up" if r.recent_pct > 0 else "text-down" if r.recent_pct < 0 else ""
            reps_items.append(
                f"<li><strong>{escape(r.name)}</strong> 今日 <span class='{r_today_class}'>{fmt_pct(r.pct_chg)}</span> / {report.recent_days}日 <span class='{r_recent_class}'>{fmt_pct(r.recent_pct)}</span>"
                f"<span>{escape(r.ts_code)} · 成交 {r.amount_yi:g} 亿</span></li>"
            )
        reps = "".join(reps_items)

        if s.quote_available:
            today_class = "text-up" if s.today_pct > 0 else "text-down" if s.today_pct < 0 else ""
            recent_class = "text-up" if s.recent_pct > 0 else "text-down" if s.recent_pct < 0 else ""
            quote = (
                f"<strong class='{today_class}' style='font-size:16px;'>{fmt_pct(s.today_pct)}</strong>"
                f"<span>{report.recent_days}日 <strong class='{recent_class}'>{fmt_pct(s.recent_pct)}</strong> / 振幅 {fmt_pct(s.amplitude)}</span>"
            )
        else:
            quote = "<span>板块行情不可用</span>"

        activity = (
            f"<span class='gain'>{s.hit_count}</span> <span style='display:inline; color:var(--text-muted); font-size:14px;'>/ {s.sector_size or '—'}</span>"
            f"<span>覆盖 {fmt_pct(s.coverage_pct)} · 成交 {s.turnover_yi:g} 亿（榜内 {fmt_pct(s.turnover_share_pct)}）</span>"
        )
        change = (
            f"较前{max(report.recent_days - 1, 1)}日均值 {s.hit_change:+.1f}"
            if s.hit_change is not None
            else "历史命中数据不可用"
        )
        merged = (
            f"<div style='margin-top:8px; font-size:12px; color:var(--text-muted);'>已合并：{escape('、'.join(s.related_sectors[:3]))}</div>"
            if s.related_sectors
            else ""
        )
        
        hit_badges = "".join(f"<span class='badge'>{escape(stk)}</span>" for stk in s.hit_stocks)
        
        rows.append(
            "<tr>"
            f"<td style='font-weight:bold; font-size:16px; color:var(--text-muted);'>{s.rank}</td>"
            f"<td><strong style='font-size:16px; color:#ffffff;'>{escape(s.name)}</strong>{flow_tag_html}<span style='font-size:12px; margin-top:6px;'>{escape(s.index_code)} · {escape(s.type_label)} · {change}</span>{merged}</td>"
            f"<td>{activity}</td>"
            f"<td>{quote}</td>"
            f"<td><div style='max-width:320px;'>{hit_badges}</div></td>"
            f"<td><ul>{reps or '<li>无榜内成分股</li>'}</ul></td>"
            "</tr>"
        )
    table_body = "\n".join(rows) or "<tr><td colspan='6' class='empty'>今日无满足阈值的活跃板块。</td></tr>"
    table = (
        "<table><thead><tr><th style='width:50px;'>排名</th><th>主题簇代表</th><th>命中 / 覆盖</th>"
        f"<th>今日/{report.recent_days}日</th><th style='width:320px;'>命中个股</th><th>代表成分股</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>"
    )
    cards = [
        ("交易日", report.trade_date),
        ("成交额榜", f"前 {report.top_n}"),
        ("活跃阈值", f"≥ {report.min_count} 次"),
        ("候选板块", str(report.active_sector_count)),
        ("去重主题", str(report.theme_cluster_count)),
        ("实际输出", str(report.displayed_sector_count)),
        ("板块口径", "/".join(SECTOR_TYPE_LABELS.get(t, t) for t in report.sector_types)),
    ]
    cards_html = "".join(
        f"<div class='card'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in cards
    )
    note = (
        "候选板块 = 成交额榜前 N 只个股在同花顺概念/行业板块中的命中次数达阈值；"
        "输出按板块覆盖率与榜内成交额综合排序，并将成分股高度重合的标签折叠为主题簇。"
        "板块与个股行情来自 Tushare（未做复权），用于复盘研究，不构成投资建议。"
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>成交活跃板块复盘 {escape(report.trade_date)}</title>
  <style>
    :root {{
      --primary: #151c2c;
      --primary-light: #1e293b;
      --accent: #3b82f6;
      --bg: #0b0f19;
      --card-bg: #111827;
      --border: rgba(56, 189, 248, 0.08);
      --text-main: #e2e8f0;
      --text-muted: #94a3b8;
      
      --up-color: #ff4a6b;
      --up-bg: rgba(255, 74, 107, 0.1);
      --down-color: #00e676;
      --down-bg: rgba(0, 230, 118, 0.1);
      --flat-color: #94a3b8;
      --flat-bg: rgba(148, 163, 184, 0.1);
    }}
    
    * {{ box-sizing: border-box; }}
    
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text-main);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.6;
    }}
    
    header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      padding: 32px 40px;
      border-bottom: 1px solid rgba(56, 189, 248, 0.15);
      box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
    }}
    
    header h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      font-weight: 800;
      letter-spacing: -0.025em;
      background: linear-gradient(to right, #ffffff, #94a3b8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    
    .subtitle {{
      margin: 0;
      color: var(--text-muted);
      font-size: 14px;
    }}
    
    main {{
      padding: 32px 40px;
      max-width: 1500px;
      margin: 0 auto;
    }}
    
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 32px;
    }}
    
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 20px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
      transition: transform 0.2s, border-color 0.2s;
    }}
    
    .card:hover {{
      transform: translateY(-2px);
      border-color: rgba(56, 189, 248, 0.25);
    }}
    
    .card span, td span {{
      display: block;
      color: var(--text-muted);
      font-size: 12px;
      margin-top: 4px;
    }}
    
    .card span {{
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }}
    
    .card strong {{
      font-size: 24px;
      font-weight: 700;
      color: #ffffff;
    }}
    
    .table-wrap {{
      overflow-x: auto;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
    }}
    
    table {{
      width: 100%;
      min-width: 1100px;
      border-collapse: collapse;
      font-size: 14px;
      text-align: left;
    }}
    
    th, td {{
      padding: 16px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    
    th {{
      background-color: #1e293b;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    
    tr:last-child td {{
      border-bottom: none;
    }}
    
    tr {{
      transition: background-color 0.15s;
    }}
    
    tr:hover td {{
      background-color: rgba(255, 255, 255, 0.02);
    }}
    
    .text-up {{ color: var(--up-color) !important; font-weight: bold; }}
    .text-down {{ color: var(--down-color) !important; font-weight: bold; }}
    
    .gain {{
      font-size: 18px;
      font-weight: 700;
      color: var(--up-color);
      font-variant-numeric: tabular-nums;
    }}
    
    .tag-up {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: bold;
      border-radius: 4px;
      background-color: var(--up-bg);
      color: var(--up-color);
      margin-left: 8px;
    }}
    .tag-down {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: bold;
      border-radius: 4px;
      background-color: var(--down-bg);
      color: var(--down-color);
      margin-left: 8px;
    }}
    .tag-flat {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: bold;
      border-radius: 4px;
      background-color: var(--flat-bg);
      color: var(--flat-color);
      margin-left: 8px;
    }}
    .tag-warning {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: bold;
      border-radius: 4px;
      background-color: rgba(255, 74, 107, 0.15);
      color: #ff4a6b;
      border: 1px solid rgba(255, 74, 107, 0.3);
      margin-left: 8px;
    }}
    .tag-safe {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: bold;
      border-radius: 4px;
      background-color: rgba(0, 230, 118, 0.15);
      color: #00e676;
      border: 1px solid rgba(0, 230, 118, 0.3);
      margin-left: 8px;
    }}
    
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 500;
      border-radius: 6px;
      background-color: #1e293b;
      color: #cbd5e1;
      border: 1px solid rgba(255, 255, 255, 0.08);
      margin-right: 4px;
      margin-bottom: 6px;
      transition: background-color 0.15s;
    }}
    
    .badge:hover {{
      background-color: #334155;
    }}
    
    ul {{
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    
    li {{
      margin-bottom: 12px;
      border-bottom: 1px dashed rgba(255,255,255,0.04);
      padding-bottom: 8px;
    }}
    li:last-child {{
      border-bottom: none;
      padding-bottom: 0;
      margin-bottom: 0;
    }}
    
    li strong {{
      color: #ffffff;
      font-size: 14px;
    }}
    
    .empty {{
      text-align: center;
      color: var(--text-muted);
      padding: 60px;
      font-size: 15px;
    }}
    
    .note {{
      margin-top: 24px;
      color: var(--text-muted);
      font-size: 13px;
      line-height: 1.8;
      border-top: 1px solid var(--border);
      padding-top: 20px;
    }}
    
    @media (max-width: 900px) {{
      header, main {{ padding-left: 20px; padding-right: 20px; }}
      .cards {{ grid-template-columns: repeat(2, minmax(140px, 1fr)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>成交活跃板块复盘 · {escape(report.trade_date)}</h1>
    <p class="subtitle">成交额榜前 {report.top_n} 只个股聚合出的同花顺活跃概念/行业板块，及其行情与代表成分股。</p>
  </header>
  <main>
    <section class="cards">{cards_html}</section>
    <section class="table-wrap">{table}</section>
    <p class="note">{escape(note)}</p>
  </main>
</body>
</html>
"""


def _color_pct(value: float) -> str:
    """涨红跌绿（A股习惯），带方向箭头，用飞书 lark_md font color。"""
    if value > 0:
        return f"<font color='red'>▲ {value:.2f}%</font>"
    if value < 0:
        return f"<font color='green'>▼ {abs(value):.2f}%</font>"
    return f"<font color='grey'>— {value:.2f}%</font>"


def _sector_row(s: "ActiveSector", recent_days: int, shaded: bool) -> dict[str, Any]:
    """一个板块 = 一行两列 column_set：左侧板块/代表股，右侧今日/近N日涨跌。"""
    if not s.quote_available:
        flow_tag = "<font color='grey'>【方向未知】</font>"
    elif s.today_pct > 0:
        flow_tag = "<font color='red'>【主力流入】</font>"
    elif s.today_pct < 0:
        flow_tag = "<font color='green'>【主力流出】</font>"
    else:
        flow_tag = "<font color='grey'>【多空平衡】</font>"

    reps = " · ".join(r.name for r in s.representatives[:3]) or "—"
    history = (
        f"较前{max(recent_days - 1, 1)}日均值 {s.hit_change:+.1f}"
        if s.hit_change is not None
        else "历史命中不可用"
    )
    merged = (
        f"\n<font color='grey'>已合并</font> {' · '.join(s.related_sectors[:2])}"
        if s.related_sectors
        else ""
    )
    left = (
        f"**{s.rank}. {s.name}** {flow_tag}　<font color='grey'>{s.type_label} · 命中 {s.hit_count}/{s.sector_size or '—'} · 覆盖 {s.coverage_pct:.1f}%</font>\n"
        f"<font color='grey'>榜内成交</font> {s.turnover_yi:g} 亿（{s.turnover_share_pct:.1f}%） · {history}\n"
        f"<font color='grey'>代表</font> {reps}{merged}"
    )
    right = (
        f"今日 {_color_pct(s.today_pct)}\n{recent_days}日 {_color_pct(s.recent_pct)}"
        if s.quote_available
        else "<font color='grey'>行情不可用</font>"
    )
    def column(weight: int, content: str) -> dict[str, Any]:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": weight,
            "vertical_align": "top",
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        }
    row: dict[str, Any] = {
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [column(62, left), column(38, right)],
    }
    if shaded:
        row["background_style"] = "grey"
    return row


def build_feishu_card(report: ActiveSectorsReport, *, top: int = 10) -> dict[str, Any]:
    """Render the report into a Feishu interactive card payload.

    Shape matches ``recap_agent.reports`` / ``feishu-card-push`` (``msg_type``
    ``interactive`` with a ``card`` body), so it can be pushed as-is.
    """
    shown = min(top, report.displayed_sector_count)
    overview = {
        "tag": "div",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"📅 **交易日**\n{report.trade_date}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"💰 **成交额榜**\n前 {report.top_n}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"🎯 **活跃阈值**\n≥ {report.min_count} 次"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"🧩 **候选板块**\n{report.active_sector_count} 个"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"🔥 **去重主题**\n{report.theme_cluster_count} 个（展示前 {shown}）"}},
        ],
    }
    rows = [_sector_row(s, report.recent_days, shaded=(i % 2 == 1)) for i, s in enumerate(report.sectors[:top])]
    if not rows:
        rows = [{"tag": "div", "text": {"tag": "lark_md", "content": "今日无满足阈值的活跃板块。"}}]

    elements: list[dict[str, Any]] = [overview, {"tag": "hr"}, *rows, {"tag": "hr"}]
    pages_url = os.environ.get("RECAP_PAGES_URL")
    if pages_url:
        base_url = pages_url.rstrip("/")
        report_url = f"{base_url}/recap-active-sectors/latest.html"
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🌐 查看网页版详细报告"},
                        "type": "primary",
                        "multi_url": {
                            "url": report_url,
                            "android_url": report_url,
                            "ios_url": report_url,
                            "pc_url": report_url,
                        }
                    }
                ]
            }
        )
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "lark_md",
                    "content": "候选板块按命中阈值生成；展示按覆盖率与榜内成交额排序，并折叠成分股高度重合的同花顺标签。数据来自 Tushare，仅供复盘研究，不构成投资建议。",
                }
            ],
        }
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": f"📊 成交活跃板块复盘 · {report.trade_date}"},
            },
            "elements": elements,
        },
    }


def output_paths(base_dir: Path) -> tuple[Path, Path, Path]:
    return base_dir / "latest.html", base_dir / "latest.csv", base_dir / "latest.json"


def run(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(args.token, url=args.url, cache_dir=args.cache_dir, use_cache=not args.no_cache)
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
    parser.add_argument("--url", default=None, help="Tushare API 地址；默认取 TUSHARE_URL")
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
