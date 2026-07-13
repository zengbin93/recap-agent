#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "artifacts" / "cache" / "tushare"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "reports" / "tushare-recap-reports"
TUSHARE_URL = "http://api.tushare.pro"
DATE_FMT = "%Y%m%d"
SCORING_VERSION = "v2.1-quality-gated"
A_SHARE_MARKETS = {"主板", "创业板", "科创板", "北交所"}


class TushareError(RuntimeError):
    pass


@dataclass
class DailyPriceBatch:
    rows: list[dict[str, Any]]
    total_count: int
    adjusted_count: int = 0
    warning: str | None = None


class TushareClient:
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
            raise TushareError(
                "Missing TUSHARE_TOKEN. Set it in the environment or repo-root .env."
            )
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
            return self._rows_from_payload(
                json.loads(cache_path.read_text(encoding="utf-8"))
            )

        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields_text,
        }
        raw = self._post(payload)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return self._rows_from_payload(raw)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            TUSHARE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, max(1, self.retries + 1) + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                json.JSONDecodeError,
            ) as error:
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
            raise TushareError(
                f"Tushare error {payload.get('code')}: {payload.get('msg')}"
            )
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise TushareError("Malformed Tushare payload: missing data.fields/items")
        return [dict(zip(fields, item)) for item in items]


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
    price_mode: str = "qfq"
    min_trading_days: int = 80
    adjustment_coverage: float = 0.0
    data_warnings: list[str] = field(default_factory=list)


@dataclass
class ScoreBreakdown:
    stage: int
    size: int
    trend: int
    liquidity: int
    theme: int
    heat: int
    recent_momentum: int
    penalties: int


@dataclass
class WatchCandidate:
    rank: int
    tier: str
    score: int
    ts_code: str
    name: str
    industry: str
    market: str
    pct_change: float
    pullback_from_high: float
    recent_20d_pct: float
    circ_mv_yi: float
    total_mv_yi: float
    turnover_rate_f: float
    volume_ratio: float
    pe_ttm: float
    pb: float
    theme_reason: str
    thesis: str
    risk_flags: list[str]
    next_checks: list[str]
    breakdown: ScoreBreakdown
    source_rank: int
    end_trade_date: str


@dataclass
class WatchReport:
    generated_at: str
    source_report: str
    start_trade_date: str
    end_trade_date: str
    input_count: int
    watch_count: int
    core_count: int
    scoring_version: str = SCORING_VERSION
    price_mode: str = "qfq"
    data_warnings: list[str] = field(default_factory=list)
    candidates: list[WatchCandidate] = field(default_factory=list)


THEME_SCORES: dict[str, tuple[int, str]] = {
    "半导体": (10, "半导体国产替代/景气弹性"),
    "通信设备": (9, "AI 算力与通信基础设施"),
    "元器件": (8, "电子硬件周期与 AI 端侧链条"),
    "电气设备": (8, "新能源/电力设备分支机会"),
    "软件服务": (8, "AI 应用与数字化扩散"),
    "专用机械": (7, "设备更新与先进制造"),
    "机床制造": (7, "高端制造母机"),
    "IT设备": (7, "AI 硬件与国产化"),
}


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


def pick_trade_dates(
    client: TushareClient, start_date: str, end_date: str
) -> list[str]:
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


def is_risk_name(name: str) -> bool:
    normalized = name.strip().upper()
    return (
        normalized.startswith("ST")
        or normalized.startswith("*ST")
        or "退" in normalized
    )


def is_a_share(ts_code: str, basic: dict[str, Any]) -> bool:
    code, _, exchange = str(ts_code or "").upper().partition(".")
    if exchange not in {"SH", "SZ", "BJ"} or not code.isdigit():
        return False
    if code.startswith(("200", "900")):
        return False
    return str(basic.get("market") or "") in A_SHARE_MARKETS


def load_adj_factor_by_date(client: TushareClient, trade_date: str) -> dict[str, float]:
    rows = client.query(
        "adj_factor",
        params={"trade_date": trade_date},
        fields=["ts_code", "trade_date", "adj_factor"],
        cache_key=trade_date,
    )
    return {
        str(row["ts_code"]): parse_float(row.get("adj_factor"))
        for row in rows
        if row.get("ts_code") and parse_float(row.get("adj_factor")) > 0
    }


def apply_adjustment(
    rows: list[dict[str, Any]],
    factors: dict[str, float],
    *,
    price_mode: str,
    reference_factors: dict[str, float] | None = None,
) -> DailyPriceBatch:
    if price_mode == "raw":
        return DailyPriceBatch(rows=rows, total_count=len(rows))

    adjusted_rows: list[dict[str, Any]] = []
    adjusted_count = 0
    unnormalized_count = 0
    for row in rows:
        adjusted = dict(row)
        close = parse_float(row.get("close"))
        factor = factors.get(str(row.get("ts_code")))
        reference_factor = (reference_factors or {}).get(str(row.get("ts_code")))
        if close > 0 and factor and factor > 0:
            if reference_factors is not None and not reference_factor:
                unnormalized_count += 1
            adjusted["close"] = (
                close * factor / reference_factor
                if reference_factor
                else close * factor
            )
            adjusted_count += 1
        adjusted_rows.append(adjusted)
    warning = None
    if adjusted_count < len(rows):
        warning = f"复权因子缺失，{len(rows) - adjusted_count}/{len(rows)} 条日线回退原始收盘价"
    elif unnormalized_count:
        warning = f"区间末日复权因子缺失，{unnormalized_count}/{len(rows)} 条日线未按最新因子归一化"
    return DailyPriceBatch(
        rows=adjusted_rows,
        total_count=len(rows),
        adjusted_count=adjusted_count,
        warning=warning,
    )


def load_daily_by_date(
    client: TushareClient,
    trade_date: str,
    *,
    price_mode: str = "qfq",
    reference_factors: dict[str, float] | None = None,
) -> DailyPriceBatch:
    rows = client.query(
        "daily",
        params={"trade_date": trade_date},
        fields=["ts_code", "trade_date", "close"],
        cache_key=trade_date,
    )
    if price_mode == "raw":
        return apply_adjustment(rows, {}, price_mode=price_mode)
    try:
        factors = load_adj_factor_by_date(client, trade_date)
    except TushareError as error:
        return DailyPriceBatch(
            rows=rows,
            total_count=len(rows),
            warning=f"{trade_date} 复权因子获取失败，回退原始收盘价：{error}",
        )
    return apply_adjustment(
        rows,
        factors,
        price_mode=price_mode,
        reference_factors=reference_factors,
    )


def load_daily_range(
    client: TushareClient,
    ts_code: str,
    start_date: str,
    end_date: str,
    *,
    price_mode: str = "qfq",
) -> DailyPriceBatch:
    rows = client.query(
        "daily",
        params={"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        fields=["ts_code", "trade_date", "close"],
        cache_key=f"{ts_code}_{start_date}_{end_date}",
    )
    if price_mode == "raw":
        return apply_adjustment(rows, {}, price_mode=price_mode)
    try:
        factors = {
            str(row["trade_date"]): parse_float(row.get("adj_factor"))
            for row in client.query(
                "adj_factor",
                params={
                    "ts_code": ts_code,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                fields=["ts_code", "trade_date", "adj_factor"],
                cache_key=f"{ts_code}_{start_date}_{end_date}",
            )
            if row.get("trade_date") and parse_float(row.get("adj_factor")) > 0
        }
    except TushareError as error:
        return DailyPriceBatch(
            rows=rows,
            total_count=len(rows),
            warning=f"{ts_code} 复权因子获取失败，回退原始收盘价：{error}",
        )
    reference_factor = factors.get(end_date)
    adjusted_rows: list[dict[str, Any]] = []
    adjusted_count = 0
    for row in rows:
        adjusted = dict(row)
        close = parse_float(row.get("close"))
        factor = factors.get(str(row.get("trade_date")))
        if close > 0 and factor and factor > 0:
            adjusted["close"] = (
                close * factor / reference_factor
                if reference_factor
                else close * factor
            )
            adjusted_count += 1
        adjusted_rows.append(adjusted)
    warning = None
    if adjusted_count < len(rows):
        warning = f"{ts_code} 复权因子缺失，{len(rows) - adjusted_count}/{len(rows)} 条日线回退原始收盘价"
    elif rows and not reference_factor:
        warning = f"{ts_code} 区间末日缺少复权因子，无法按最新因子归一化"
    return DailyPriceBatch(
        rows=adjusted_rows,
        total_count=len(rows),
        adjusted_count=adjusted_count,
        warning=warning,
    )


def build_first_double_report(
    client: TushareClient,
    *,
    end_date: date | None = None,
    lookback_days: int = 183,
    min_pct_change: float = 100.0,
    max_pct_change: float | None = None,
    price_mode: str = "qfq",
    min_trading_days: int = 80,
    include_st: bool = False,
    progress: Callable[[str], None] | None = None,
) -> FirstDoubleReport:
    if price_mode not in {"raw", "qfq"}:
        raise ValueError(f"Unsupported price mode: {price_mode}")
    if min_trading_days < 2:
        raise ValueError("min_trading_days must be at least 2")
    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=lookback_days)
    trade_dates = pick_trade_dates(client, yyyymmdd(start_date), yyyymmdd(end_date))
    all_stock_basic = load_stock_basic(client)
    stock_basic = {
        ts_code: basic
        for ts_code, basic in all_stock_basic.items()
        if is_a_share(ts_code, basic)
        and (include_st or not is_risk_name(str(basic.get("name") or "")))
    }

    by_stock: dict[str, list[tuple[str, float]]] = {}
    total_price_rows = 0
    adjusted_price_rows = 0
    data_warnings = ["股票基础信息使用当前 list_status=L，历史回测存在生存者偏差"]
    adjustment_warning_samples: list[str] = []
    adjustment_warning_count = 0
    reference_factors: dict[str, float] = {}
    if price_mode == "qfq":
        try:
            reference_factors = load_adj_factor_by_date(client, trade_dates[-1])
        except TushareError as error:
            data_warnings.append(
                f"区间末日复权因子获取失败，无法按最新因子归一化：{error}"
            )
    if price_mode == "raw":
        data_warnings.append("使用未复权收盘价，分红送转可能扭曲区间涨幅")
    for index, trade_date in enumerate(trade_dates, start=1):
        if progress:
            progress(f"拉取日线 {index}/{len(trade_dates)}：{trade_date}")
        batch = load_daily_by_date(
            client,
            trade_date,
            price_mode=price_mode,
            reference_factors=reference_factors,
        )
        total_price_rows += batch.total_count
        adjusted_price_rows += batch.adjusted_count
        if batch.warning:
            adjustment_warning_count += 1
            if len(adjustment_warning_samples) < 3:
                adjustment_warning_samples.append(batch.warning)
        for row in batch.rows:
            ts_code = str(row.get("ts_code") or "")
            if ts_code not in stock_basic:
                continue
            close = parse_float(row.get("close"))
            if close > 0:
                by_stock.setdefault(ts_code, []).append((str(row["trade_date"]), close))

    candidates: list[FirstDoubleCandidate] = []
    for ts_code, prices in by_stock.items():
        prices.sort(key=lambda item: item[0])
        if len(prices) < min_trading_days or prices[0][1] <= 0:
            continue
        start_trade_date, start_close = prices[0]
        end_trade_date, end_close = prices[-1]
        pct_change = (end_close / start_close - 1.0) * 100.0
        if pct_change < min_pct_change or (
            max_pct_change is not None and pct_change > max_pct_change
        ):
            continue
        max_trade_date, max_close = max(prices, key=lambda item: item[1])
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
                max_gain=round((max_close / start_close - 1.0) * 100.0, 2),
                pullback_from_high=round((end_close / max_close - 1.0) * 100.0, 2),
                trading_days=len(prices),
            )
        )

    candidates.sort(key=lambda item: item.pct_change, reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate.rank = index

    if adjustment_warning_samples:
        data_warnings.extend(adjustment_warning_samples)
        if adjustment_warning_count > len(adjustment_warning_samples):
            data_warnings.append(
                f"另有 {adjustment_warning_count - len(adjustment_warning_samples)} 个交易日存在复权因子提示"
            )
    if (
        price_mode == "qfq"
        and total_price_rows
        and adjusted_price_rows < total_price_rows
    ):
        data_warnings.append(
            f"复权覆盖率仅 {adjusted_price_rows / total_price_rows * 100:.1f}%，请检查 adj_factor 数据"
        )
    adjustment_coverage = (
        round(adjusted_price_rows / total_price_rows * 100.0, 2)
        if total_price_rows
        else 0.0
    )

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
        price_mode=price_mode,
        min_trading_days=min_trading_days,
        adjustment_coverage=adjustment_coverage,
        data_warnings=list(dict.fromkeys(data_warnings)),
    )


def load_daily_basic(
    client: TushareClient, trade_date: str
) -> dict[str, dict[str, Any]]:
    rows = client.query(
        "daily_basic",
        params={"trade_date": trade_date},
        fields=[
            "ts_code",
            "trade_date",
            "turnover_rate",
            "turnover_rate_f",
            "volume_ratio",
            "pe_ttm",
            "pb",
            "total_mv",
            "circ_mv",
        ],
        cache_key=trade_date,
    )
    return {row["ts_code"]: row for row in rows}


def load_cached_adj_factors(cache_dir: Path, trade_date: str) -> dict[str, float]:
    path = cache_dir / "adj_factor" / f"{trade_date}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    if "ts_code" not in fields or "adj_factor" not in fields:
        return {}
    ts_index = fields.index("ts_code")
    factor_index = fields.index("adj_factor")
    return {
        str(item[ts_index]): parse_float(item[factor_index])
        for item in items
        if parse_float(item[factor_index]) > 0
    }


def load_price_series_from_cache(
    cache_dir: Path,
    start_date: str,
    end_date: str,
    *,
    price_mode: str = "raw",
) -> dict[str, list[tuple[str, float]]]:
    daily_dir = cache_dir / "daily"
    series: dict[str, list[tuple[str, float]]] = {}
    if not daily_dir.exists():
        return series
    reference_factors = (
        load_cached_adj_factors(cache_dir, end_date) if price_mode == "qfq" else {}
    )
    for path in sorted(daily_dir.glob("*.json")):
        trade_date = path.stem
        if trade_date < start_date or trade_date > end_date:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if "ts_code" not in fields or "close" not in fields:
            continue
        ts_index = fields.index("ts_code")
        close_index = fields.index("close")
        date_index = fields.index("trade_date") if "trade_date" in fields else None
        factors = (
            load_cached_adj_factors(cache_dir, trade_date)
            if price_mode == "qfq"
            else {}
        )
        for item in items:
            close = parse_float(item[close_index])
            if price_mode == "qfq":
                factor = factors.get(str(item[ts_index]))
                if factor and factor > 0:
                    reference_factor = reference_factors.get(str(item[ts_index]))
                    close = (
                        close * factor / reference_factor
                        if reference_factor
                        else close * factor
                    )
            if close > 0:
                series.setdefault(item[ts_index], []).append(
                    (item[date_index] if date_index is not None else trade_date, close)
                )
    for prices in series.values():
        prices.sort(key=lambda item: item[0])
    return series


def price_cache_needs_backfill(
    cache_dir: Path, start_date: str, end_date: str, *, price_mode: str
) -> bool:
    if price_mode != "qfq":
        return False
    daily_dir = cache_dir / "daily"
    if not daily_dir.exists():
        return False
    for path in daily_dir.glob("*.json"):
        if (
            start_date <= path.stem <= end_date
            and not (cache_dir / "adj_factor" / path.name).exists()
        ):
            return True
    return False


def recent_pct(prices: list[tuple[str, float]], days: int = 20) -> float:
    if len(prices) < 2:
        return 0.0
    window = prices[-days:] if len(prices) >= days else prices
    return (
        round((window[-1][1] / window[0][1] - 1.0) * 100.0, 2)
        if window[0][1] > 0
        else 0.0
    )


def stage_score(pct_change: float) -> int:
    if 120 <= pct_change <= 350:
        return 25
    if 100 <= pct_change < 120:
        return 15
    if 350 < pct_change <= 500:
        return 12
    return 5


def size_score(circ_mv_yi: float) -> int:
    if 20 <= circ_mv_yi <= 150:
        return 25
    if 150 < circ_mv_yi <= 300:
        return 16
    if 300 < circ_mv_yi <= 600:
        return 8
    if 0 < circ_mv_yi < 20:
        return 10
    return 0


def trend_score(pullback_from_high: float) -> int:
    if pullback_from_high >= -8:
        return 20
    if pullback_from_high >= -18:
        return 12
    if pullback_from_high >= -30:
        return 5
    return 0


def liquidity_score(turnover_rate_f: float) -> int:
    if 3 <= turnover_rate_f <= 18:
        return 15
    if 1 <= turnover_rate_f < 3 or 18 < turnover_rate_f <= 30:
        return 8
    if turnover_rate_f > 30:
        return 2
    return 0


def heat_score(volume_ratio: float) -> int:
    if 0.8 <= volume_ratio <= 2.5:
        return 7
    if 2.5 < volume_ratio <= 5:
        return 3
    return 0


def recent_momentum_score(value: float) -> int:
    if 5 <= value <= 60:
        return 8
    if -8 <= value < 5:
        return 4
    if value > 60:
        return 2
    return 0


def risk_penalties(
    name: str, market: str, pe_ttm: float, pb: float
) -> tuple[int, list[str]]:
    penalties = 0
    flags: list[str] = []
    if name.startswith("ST") or name.startswith("*ST") or "退" in name:
        penalties -= 25
        flags.append("ST/退市风险")
    if market == "北交所":
        penalties -= 5
        flags.append("北交所流动性差异")
    if pe_ttm > 180:
        penalties -= 5
        flags.append("PE_TTM 偏高")
    if pb > 20:
        penalties -= 4
        flags.append("PB 偏高")
    return penalties, flags


def tier_for_score(score: int) -> str:
    if score >= 100:
        return "A 核心跟踪"
    if score >= 90:
        return "B 重点观察"
    if score >= 78:
        return "C 观察名单"
    return "D 暂缓"


def build_watch_report(
    first_double_report: dict[str, Any],
    *,
    client: TushareClient,
    source_report: Path,
    cache_dir: Path,
    limit: int = 80,
) -> WatchReport:
    end_trade_date = first_double_report["end_trade_date"]
    price_mode = str(first_double_report.get("price_mode") or "raw")
    daily_basic = load_daily_basic(client, end_trade_date)
    price_series = load_price_series_from_cache(
        cache_dir,
        first_double_report["start_trade_date"],
        first_double_report["end_trade_date"],
        price_mode=price_mode,
    )
    data_warnings: list[str] = list(first_double_report.get("data_warnings") or [])
    cache_needs_backfill = price_cache_needs_backfill(
        cache_dir,
        first_double_report["start_trade_date"],
        first_double_report["end_trade_date"],
        price_mode=price_mode,
    )
    if cache_needs_backfill:
        data_warnings.append("日线缓存缺少复权因子，已对候选股按区间自动补拉")

    scored: list[WatchCandidate] = []
    for source in first_double_report.get("candidates", []):
        ts_code = source["ts_code"]
        if len(price_series.get(ts_code, [])) < 2 or cache_needs_backfill:
            batch = load_daily_range(
                client,
                ts_code,
                first_double_report["start_trade_date"],
                first_double_report["end_trade_date"],
                price_mode=price_mode,
            )
            if batch.rows:
                price_series[ts_code] = [
                    (str(row["trade_date"]), parse_float(row.get("close")))
                    for row in batch.rows
                    if parse_float(row.get("close")) > 0
                ]
                price_series[ts_code].sort(key=lambda item: item[0])
            if batch.warning:
                data_warnings.append(batch.warning)
        basic = daily_basic.get(ts_code, {})
        industry = str(source.get("industry") or "")
        pct_change = parse_float(source.get("pct_change"))
        pullback = parse_float(source.get("pullback_from_high"))
        circ_mv_yi = round(parse_float(basic.get("circ_mv")) / 10000.0, 2)
        total_mv_yi = round(parse_float(basic.get("total_mv")) / 10000.0, 2)
        turnover_rate_f = parse_float(
            basic.get("turnover_rate_f") or basic.get("turnover_rate")
        )
        volume_ratio = parse_float(basic.get("volume_ratio"))
        pe_ttm = parse_float(basic.get("pe_ttm"))
        pb = parse_float(basic.get("pb"))
        prices = price_series.get(ts_code, [])
        recent_20d = recent_pct(prices, 20)
        theme_points, theme_reason = THEME_SCORES.get(
            industry, (3, "行业弹性需要单独验证")
        )
        penalties, flags = risk_penalties(
            str(source.get("name") or ""), str(source.get("market") or ""), pe_ttm, pb
        )
        if len(prices) < 2:
            flags.append("近半年价格数据缺失")
            data_warnings.append(f"{ts_code} 缺少可计算近 20 日动量的价格数据")
        breakdown = ScoreBreakdown(
            stage=stage_score(pct_change),
            size=size_score(circ_mv_yi),
            trend=trend_score(pullback),
            liquidity=liquidity_score(turnover_rate_f),
            theme=theme_points,
            heat=heat_score(volume_ratio),
            recent_momentum=recent_momentum_score(recent_20d),
            penalties=penalties,
        )
        score = sum(asdict(breakdown).values())
        theme = theme_reason
        thesis = (
            f"{theme}；半年涨幅 {pct_change:.2f}%，已被市场初步验证；"
            f"流通市值约 {circ_mv_yi:.2f} 亿，回撤 {pullback:.2f}%，"
            f"自由流通换手 {turnover_rate_f:.2f}%，近 20 日涨幅 {recent_20d:.2f}%"
            f"{'（数据缺失）' if len(prices) < 2 else ''}。"
        )
        next_checks = [
            "核对最近两期营收/利润是否出现加速",
            "追踪公告、互动易和机构调研中是否有订单/产能/客户变化",
            "复盘涨停与放量日，确认是板块共振还是孤立炒作",
        ]
        if pe_ttm <= 0:
            next_checks.append("当前 PE_TTM 无效或亏损，必须优先验证盈利拐点")
        if flags:
            next_checks.append("先处理风险标签，再决定是否进入核心跟踪")
        scored.append(
            WatchCandidate(
                rank=0,
                tier=tier_for_score(score),
                score=score,
                ts_code=ts_code,
                name=str(source.get("name") or ""),
                industry=industry,
                market=str(source.get("market") or ""),
                pct_change=round(pct_change, 2),
                pullback_from_high=round(pullback, 2),
                recent_20d_pct=round(recent_20d, 2),
                circ_mv_yi=circ_mv_yi,
                total_mv_yi=total_mv_yi,
                turnover_rate_f=round(turnover_rate_f, 2),
                volume_ratio=round(volume_ratio, 2),
                pe_ttm=round(pe_ttm, 2),
                pb=round(pb, 2),
                theme_reason=theme_reason,
                thesis=thesis,
                risk_flags=flags or ["无明显规则风险"],
                next_checks=next_checks,
                breakdown=breakdown,
                source_rank=int(source.get("rank") or 0),
                end_trade_date=end_trade_date,
            )
        )

    scored.sort(key=lambda item: (item.score, item.pct_change), reverse=True)
    for index, candidate in enumerate(scored, start=1):
        candidate.rank = index
    limited = scored[:limit] if limit > 0 else scored
    return WatchReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        source_report=str(source_report),
        start_trade_date=first_double_report["start_trade_date"],
        end_trade_date=end_trade_date,
        input_count=len(first_double_report.get("candidates", [])),
        watch_count=len(limited),
        core_count=sum(1 for item in limited if item.tier.startswith("A")),
        scoring_version=SCORING_VERSION,
        price_mode=price_mode,
        data_warnings=list(dict.fromkeys(data_warnings)),
        candidates=limited,
    )


def write_json_report(report: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv_report(report: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in report.candidates:
        row = asdict(item)
        if "risk_flags" in row:
            row["risk_flags"] = "；".join(item.risk_flags)
            row["next_checks"] = "；".join(item.next_checks)
            row["breakdown"] = json.dumps(
                asdict(item.breakdown), ensure_ascii=False, sort_keys=True
            )
        rows.append(row)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def render_first_double_html(report: FirstDoubleReport) -> str:
    rows = []
    for item in report.candidates:
        rows.append(
            "<tr>"
            f"<td>{item.rank}</td><td><strong>{escape(item.name)}</strong><span>{escape(item.ts_code)}</span></td>"
            f"<td>{escape(item.industry)}</td><td>{escape(item.market)}</td>"
            f"<td>{escape(item.start_trade_date)}<span>{item.start_close:g}</span></td>"
            f"<td>{escape(item.end_trade_date)}<span>{item.end_close:g}</span></td>"
            f"<td class='gain'>{fmt_pct(item.pct_change)}</td>"
            f"<td>{escape(item.max_trade_date)}<span>{fmt_pct(item.max_gain)}</span></td>"
            f"<td>{fmt_pct(item.pullback_from_high)}</td><td>{item.trading_days}</td>"
            "</tr>"
        )
    table_body = (
        "\n".join(rows) or "<tr><td colspan='10' class='empty'>暂无候选。</td></tr>"
    )
    price_label = "前复权" if report.price_mode == "qfq" else "未复权"
    warning_note = "；".join(report.data_warnings[:3])
    if len(report.data_warnings) > 3:
        warning_note += f"；另有 {len(report.data_warnings) - 3} 条数据提示请查看 JSON"
    return render_page(
        "半年强势股初筛",
        "先筛出过去半年表现强势的 A 股，再进入财报、公告和后验收益验证。该模块是研究池，不是买入建议。",
        [
            ("统计区间", f"{report.start_trade_date} - {report.end_trade_date}"),
            ("自然日回看", str(report.lookback_days)),
            ("价格口径", price_label),
            ("最低交易日", str(report.min_trading_days)),
            ("复权覆盖率", fmt_pct(report.adjustment_coverage)),
            ("A 股股票数", str(report.stock_count)),
            ("有行情股票数", str(report.stocks_with_prices)),
            ("入选股票数", str(report.candidate_count)),
        ],
        "<table><thead><tr><th>排名</th><th>股票</th><th>行业</th><th>市场</th><th>区间起点</th><th>区间终点</th><th>区间涨幅</th><th>期间高点</th><th>高点回撤</th><th>交易天数</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>",
        f"默认使用 Tushare daily × adj_factor 的前复权口径；最低交易日为 {report.min_trading_days}，避免新股短历史误入。{warning_note}",
    )


def render_watch_html(report: WatchReport) -> str:
    rows = []
    for item in report.candidates:
        checks = "".join(f"<li>{escape(check)}</li>" for check in item.next_checks)
        rows.append(
            "<tr>"
            f"<td>{item.rank}</td><td><strong>{escape(item.name)}</strong><span>{escape(item.ts_code)}</span></td>"
            f"<td><strong>{escape(item.tier)}</strong><span>{item.score} 分</span></td>"
            f"<td>{escape(item.industry)}<span>{escape(item.theme_reason)}</span></td>"
            f"<td class='gain'>{fmt_pct(item.pct_change)}<span>20日 {fmt_pct(item.recent_20d_pct)}</span></td>"
            f"<td>{item.circ_mv_yi:g} 亿<span>总市值 {item.total_mv_yi:g} 亿</span></td>"
            f"<td>{fmt_pct(item.pullback_from_high)}<span>换手 {fmt_pct(item.turnover_rate_f)} / 量比 {item.volume_ratio:g}</span></td>"
            f"<td>{escape(item.thesis)}</td><td>{escape('；'.join(item.risk_flags))}<ul>{checks}</ul></td>"
            "</tr>"
        )
    table_body = (
        "\n".join(rows) or "<tr><td colspan='9' class='empty'>暂无候选。</td></tr>"
    )
    warning_note = "；".join(report.data_warnings[:3])
    if len(report.data_warnings) > 3:
        warning_note += f"；另有 {len(report.data_warnings) - 3} 条数据提示请查看 JSON"
    return render_page(
        "二阶段研究跟踪池",
        "从最近半年已翻倍的股票中，继续筛选更值得深挖的二阶段候选。评分只负责排序，基本面与催化仍需人工核验。",
        [
            ("源区间", f"{report.start_trade_date} - {report.end_trade_date}"),
            ("输入翻倍股", str(report.input_count)),
            ("跟踪池数量", str(report.watch_count)),
            ("A 级核心", str(report.core_count)),
            ("评分版本", report.scoring_version),
            ("价格口径", "前复权" if report.price_mode == "qfq" else "未复权"),
            ("生成日期", report.generated_at[:10]),
        ],
        "<table><thead><tr><th>排名</th><th>股票</th><th>分层/分数</th><th>行业</th><th>涨幅</th><th>流通市值</th><th>趋势/资金</th><th>候选逻辑</th><th>风险与下一步</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>",
        f"评分模型 {report.scoring_version} 仅用于复盘研究，不构成投资建议。{warning_note}",
    )


def render_page(
    title: str, subtitle: str, cards: list[tuple[str, str]], table: str, note: str
) -> str:
    cards_html = "".join(
        f"<div class='card'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in cards
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#20242a; --muted:#667085; --line:#dde2e8; --gain:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    header {{ padding:28px 36px 18px; background:var(--panel); border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }} .subtitle {{ margin:0; color:var(--muted); line-height:1.7; }}
    main {{ padding:22px 36px 42px; }} .cards {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; margin-bottom:18px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }} .card span, td span {{ display:block; color:var(--muted); font-size:12px; margin-top:4px; }}
    .card strong {{ font-size:22px; }} .table-wrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; min-width:1120px; border-collapse:collapse; }} th,td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#f9fafb; color:#475467; font-size:13px; }} tr:last-child td {{ border-bottom:0; }} .gain {{ color:var(--gain); font-weight:750; }}
    .empty {{ text-align:center; color:var(--muted); padding:32px; }} .note {{ margin-top:14px; color:var(--muted); font-size:13px; line-height:1.7; }}
    @media (max-width:900px) {{ header,main {{ padding-left:18px; padding-right:18px; }} .cards {{ grid-template-columns:repeat(2,minmax(140px,1fr)); }} }}
  </style>
</head>
<body><header><h1>{escape(title)}</h1><p class="subtitle">{escape(subtitle)}</p></header>
<main><section class="cards">{cards_html}</section><section class="table-wrap">{table}</section><p class="note">{escape(note)}</p></main></body></html>
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def output_paths(base_dir: Path, topic: str) -> tuple[Path, Path, Path]:
    report_dir = base_dir / topic
    return (
        report_dir / "latest.html",
        report_dir / "latest.csv",
        report_dir / "latest.json",
    )


def build_feishu_card(
    first_report: dict[str, Any], watch_report: dict[str, Any]
) -> dict[str, Any]:
    first_candidates = first_report.get("candidates", [])[:5]
    watch_candidates = watch_report.get("candidates", [])[:5]
    first_lines = [
        f"{item.get('rank', 0)}. {item.get('name') or item.get('ts_code')}（{item.get('ts_code')}）"
        f" · 半年 {parse_float(item.get('pct_change')):.2f}% · {item.get('industry') or '行业未标注'}"
        for item in first_candidates
    ] or ["暂无符合条件的半年强势股"]
    watch_lines = [
        f"{item.get('rank', 0)}. {item.get('name') or item.get('ts_code')}（{item.get('ts_code')}）"
        f" · {item.get('tier')} · {item.get('score')} 分 · {item.get('industry') or '行业未标注'}"
        for item in watch_candidates
    ] or ["暂无二阶段跟踪候选"]
    warnings = list(
        dict.fromkeys(
            first_report.get("data_warnings", [])
            + watch_report.get("data_warnings", [])
        )
    )
    warning_text = (
        "\n".join(f"- {warning}" for warning in warnings[:3]) or "- 无数据质量提示"
    )
    summary = (
        f"**区间**: {first_report.get('start_trade_date')} - {first_report.get('end_trade_date')}\n"
        f"**口径**: {'前复权' if first_report.get('price_mode') == 'qfq' else '未复权'}，"
        f"最低交易日 {first_report.get('min_trading_days', 0)}\n"
        f"**A 股样本**: {first_report.get('stock_count', 0)} · "
        f"**半年强势股**: {first_report.get('candidate_count', 0)} · "
        f"**二阶段跟踪池**: {watch_report.get('watch_count', 0)} · "
        f"**A 级核心**: {watch_report.get('core_count', 0)}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "过去半年潜力股复盘"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**半年强势股初筛 Top 5**\n"
                        + "\n".join(first_lines),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**二阶段研究跟踪池 Top 5**（评分 {watch_report.get('scoring_version', 'unknown')}）\n"
                            + "\n".join(watch_lines)
                        ),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**数据质量提示**\n" + warning_text,
                    },
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "研究复盘队列，不构成投资建议；基本面、催化和后验收益仍需验证。",
                        }
                    ],
                },
            ],
        },
    }


def run_first_double(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(
        args.token, cache_dir=args.cache_dir, use_cache=not args.no_cache
    )
    report = build_first_double_report(
        client,
        end_date=parse_yyyymmdd(args.end_date) if args.end_date else None,
        lookback_days=args.lookback_days,
        min_pct_change=args.min_pct_change,
        max_pct_change=args.max_pct_change,
        price_mode=args.price_mode,
        min_trading_days=args.min_trading_days,
        include_st=args.include_st,
        progress=print if args.progress else None,
    )
    html_path = args.html or output_paths(args.output_dir, "first_double")[0]
    csv_path = args.csv or output_paths(args.output_dir, "first_double")[1]
    json_path = args.json or output_paths(args.output_dir, "first_double")[2]
    write_text(html_path, render_first_double_html(report))
    write_csv_report(report, csv_path)
    write_json_report(report, json_path)
    return {
        "html": str(html_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "count": str(report.candidate_count),
    }


def run_tenbagger_watch(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(
        args.token, cache_dir=args.cache_dir, use_cache=not args.no_cache
    )
    source_report = (
        args.source_report or output_paths(args.output_dir, "first_double")[2]
    )
    source = json.loads(source_report.read_text(encoding="utf-8"))
    report = build_watch_report(
        source,
        client=client,
        source_report=source_report,
        cache_dir=args.cache_dir,
        limit=args.limit,
    )
    html_path = args.html or output_paths(args.output_dir, "tenbagger_watch")[0]
    csv_path = args.csv or output_paths(args.output_dir, "tenbagger_watch")[1]
    json_path = args.json or output_paths(args.output_dir, "tenbagger_watch")[2]
    write_text(html_path, render_watch_html(report))
    write_csv_report(report, csv_path)
    write_json_report(report, json_path)
    return {
        "html": str(html_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "count": str(report.watch_count),
    }


def run_full_chain(args: argparse.Namespace) -> dict[str, Any]:
    first = run_first_double(args)
    args.source_report = Path(first["json"])
    watch = run_tenbagger_watch(args)
    card_path = args.output_dir / "tushare-recap-reports" / "latest-card.json"
    first_report = json.loads(Path(first["json"]).read_text(encoding="utf-8"))
    watch_report = json.loads(Path(watch["json"]).read_text(encoding="utf-8"))
    write_text(
        card_path,
        json.dumps(
            build_feishu_card(first_report, watch_report), ensure_ascii=False, indent=2
        )
        + "\n",
    )
    return {"first_double": first, "tenbagger_watch": watch, "card": str(card_path)}


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token", default=None, help="Tushare token; defaults to TUSHARE_TOKEN"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Tushare API cache directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Report output directory",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Disable local Tushare cache"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Tushare recap report skills.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    first = subparsers.add_parser(
        "first-double", help="Generate the first-double stock pool report"
    )
    add_common_arguments(first)
    first.add_argument(
        "--end-date", default=None, help="End date in YYYYMMDD; defaults to today"
    )
    first.add_argument(
        "--lookback-days", type=int, default=183, help="Natural days to look back"
    )
    first.add_argument(
        "--min-pct-change",
        type=float,
        default=100.0,
        help="Minimum interval gain percent",
    )
    first.add_argument(
        "--max-pct-change",
        type=float,
        default=None,
        help="Maximum interval gain percent",
    )
    first.add_argument(
        "--price-mode",
        choices=["qfq", "raw"],
        default="qfq",
        help="Price basis; qfq uses adj_factor",
    )
    first.add_argument(
        "--min-trading-days",
        type=int,
        default=80,
        help="Minimum available trading days per stock",
    )
    first.add_argument(
        "--include-st",
        action="store_true",
        help="Include ST/delisting-risk names in first-double",
    )
    first.add_argument("--html", type=Path, default=None, help="HTML output path")
    first.add_argument("--csv", type=Path, default=None, help="CSV output path")
    first.add_argument("--json", type=Path, default=None, help="JSON output path")
    first.add_argument(
        "--progress", action="store_true", help="Print each fetched trade date"
    )
    first.set_defaults(func=run_first_double)

    watch = subparsers.add_parser(
        "tenbagger-watch", help="Generate the second-stage watchlist report"
    )
    add_common_arguments(watch)
    watch.add_argument(
        "--source-report", type=Path, default=None, help="first-double JSON report"
    )
    watch.add_argument(
        "--limit", type=int, default=80, help="Maximum candidates to output"
    )
    watch.add_argument("--html", type=Path, default=None, help="HTML output path")
    watch.add_argument("--csv", type=Path, default=None, help="CSV output path")
    watch.add_argument("--json", type=Path, default=None, help="JSON output path")
    watch.set_defaults(func=run_tenbagger_watch)

    full = subparsers.add_parser(
        "full-chain", help="Run first-double and tenbagger-watch in sequence"
    )
    add_common_arguments(full)
    full.add_argument(
        "--end-date", default=None, help="End date in YYYYMMDD; defaults to today"
    )
    full.add_argument(
        "--lookback-days", type=int, default=183, help="Natural days to look back"
    )
    full.add_argument(
        "--min-pct-change",
        type=float,
        default=100.0,
        help="Minimum interval gain percent",
    )
    full.add_argument(
        "--max-pct-change",
        type=float,
        default=None,
        help="Maximum interval gain percent",
    )
    full.add_argument(
        "--price-mode",
        choices=["qfq", "raw"],
        default="qfq",
        help="Price basis; qfq uses adj_factor",
    )
    full.add_argument(
        "--min-trading-days",
        type=int,
        default=80,
        help="Minimum available trading days per stock",
    )
    full.add_argument(
        "--include-st",
        action="store_true",
        help="Include ST/delisting-risk names in first-double",
    )
    full.add_argument(
        "--limit", type=int, default=80, help="Maximum watch candidates to output"
    )
    full.add_argument("--html", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument("--csv", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument("--json", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument(
        "--progress", action="store_true", help="Print each fetched trade date"
    )
    full.set_defaults(func=run_full_chain)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except (
        TushareError,
        FileNotFoundError,
        KeyError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"错误：{error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
