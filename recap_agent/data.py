from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Protocol


class Gateway(Protocol):
    def query(self, table: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DatasetResult:
    rows: list[dict[str, Any]]
    source: str
    warning: str | None = None


@dataclass(frozen=True)
class MarketPeriod:
    """The explicit calendar window represented by a recap report."""

    task: str
    start_date: str
    end_date: str


BENCHMARK_INDEX_CODES = frozenset(
    {
        "000001.SH",  # 上证指数
        "000016.SH",  # 上证50
        "000300.SH",  # 沪深300
        "000688.SH",  # 科创50
        "000852.SH",  # 中证1000
        "000905.SH",  # 中证500
        "399001.SZ",  # 深证成指
        "399006.SZ",  # 创业板指
    }
)


def filter_recap_dataset(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the recap-facing dataset bounded to the entities it claims to show."""

    if name != "indices":
        return rows
    return [row for row in rows if str(row.get("ts_code")) in BENCHMARK_INDEX_CODES]


def _parse_date(value: str) -> date:
    normalized = value.replace("-", "")
    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"invalid date {value!r}; expected YYYYMMDD")
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:]))


def _format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def build_market_period(task: str, end_date: str) -> MarketPeriod:
    """Build a daily, week-to-date, or month-to-date window."""

    if task not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"unsupported recap task: {task}")
    end = _parse_date(end_date)
    if task == "daily":
        start = end
    elif task == "weekly":
        start = end - timedelta(days=end.weekday())
    else:
        start = end.replace(day=1)
    return MarketPeriod(
        task=task, start_date=_format_date(start), end_date=_format_date(end)
    )


def cache_file_name(table: str, params: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"table": table, "params": params}, sort_keys=True, ensure_ascii=True
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{table}-{digest}.json"


class TushareGateway:
    """Thin adapter around tushare; financial table knowledge lives in recap skills."""

    def __init__(self, token: str | None = None, url: str | None = None):
        self.token = token or os.environ.get("TUSHARE_TOKEN")
        self.url = url or os.environ.get("TUSHARE_URL")

    def query(self, table: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self.token:
            raise RuntimeError("TUSHARE_TOKEN is not configured")
        try:
            import tushare as ts  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("tushare package is not installed") from exc
        pro = ts.pro_api(self.token)
        if self.url:
            pro._DataApi__http_url = self.url
        frame = pro.query(table, **dict(params))
        if hasattr(frame, "to_dict"):
            return frame.to_dict(orient="records")
        if isinstance(frame, list):
            return frame
        raise RuntimeError(f"unsupported tushare response for table {table}")


class TushareDataCollector:
    def __init__(
        self,
        gateway: Gateway | None = None,
        cache_dir: pathlib.Path | str = "artifacts/cache",
        fallback_dir: pathlib.Path | str = "fallback-data",
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.gateway = gateway or TushareGateway()
        self.cache_dir = pathlib.Path(cache_dir)
        self.fallback_dir = pathlib.Path(fallback_dir)
        self.sleep = sleep

    def fetch_table(
        self,
        table: str,
        params: Mapping[str, Any],
        *,
        ttl_seconds: int = 60 * 60 * 6,
        retries: int = 2,
    ) -> DatasetResult:
        cache_path = self.cache_dir / cache_file_name(table, params)
        cached = self._read_cache(cache_path, ttl_seconds)
        if cached is not None:
            return DatasetResult(rows=cached, source="cache")

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                rows = self.gateway.query(table, params)
                self._write_json(cache_path, rows)
                return DatasetResult(rows=rows, source="tushare")
            except Exception as exc:  # noqa: BLE001 - surface upstream data failures as warnings.
                last_error = exc
                if attempt < retries:
                    self.sleep(0.2 * (attempt + 1))

        fallback_path = self.fallback_dir / cache_file_name(table, params)
        if fallback_path.exists():
            rows = json.loads(fallback_path.read_text(encoding="utf-8"))
            return DatasetResult(rows=rows, source="fallback", warning=str(last_error))
        raise RuntimeError(f"failed to fetch {table}: {last_error}") from last_error

    def _read_cache(
        self, path: pathlib.Path, ttl_seconds: int
    ) -> list[dict[str, Any]] | None:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl_seconds:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def default_market_requests(
    task: str, trade_date: str | None = None
) -> dict[str, tuple[str, dict[str, Any]]]:
    period = build_market_period(task, trade_date or _format_date(date.today()))
    params = (
        {"trade_date": period.end_date}
        if task == "daily"
        else {"start_date": period.start_date, "end_date": period.end_date}
    )
    common = {
        "indices": ("index_daily", params),
        "hot_sectors": ("moneyflow_ind_ths", params),
    }
    if task == "daily":
        return {
            **common, 
            "a_share_daily": ("daily", params),
            "fund_basics": ("fund_basic", {"market": "E"}),
            "fund_dailies": ("fund_daily", params),
            "individual_moneyflow": ("moneyflow", params)
        }
    if task == "weekly":
        return {**common, "weekly_moneyflow": ("moneyflow", params)}
    if task == "monthly":
        return {**common, "monthly_fund_flow": ("fund_flow", params)}
    return common


def resolve_latest_trade_date(
    collector: TushareDataCollector,
    requested_date: str | None = None,
) -> str:
    """Resolve the latest open SSE date at or before the requested date."""

    target = _parse_date(requested_date) if requested_date else date.today()
    result = collector.fetch_table(
        "trade_cal",
        {"exchange": "SSE", "end_date": _format_date(target), "is_open": "1"},
        ttl_seconds=60 * 60 * 24,
    )
    target_text = _format_date(target)
    open_dates = [
        str(row.get("cal_date"))
        for row in result.rows
        if str(row.get("is_open")) == "1"
        and row.get("cal_date")
        and str(row["cal_date"]) <= target_text
    ]
    if not open_dates:
        raise RuntimeError(f"no open trading date at or before {_format_date(target)}")
    return max(open_dates)
