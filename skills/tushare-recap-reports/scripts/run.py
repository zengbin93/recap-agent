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


class TushareError(RuntimeError):
    pass


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


def pick_trade_dates(client: TushareClient, start_date: str, end_date: str) -> list[str]:
    rows = client.query(
        "trade_cal",
        params={"exchange": "SSE", "start_date": start_date, "end_date": end_date, "is_open": "1"},
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
            if close > 0:
                by_stock.setdefault(row["ts_code"], []).append((row["trade_date"], close))

    candidates: list[FirstDoubleCandidate] = []
    for ts_code, prices in by_stock.items():
        prices.sort(key=lambda item: item[0])
        if len(prices) < 2 or prices[0][1] <= 0:
            continue
        start_trade_date, start_close = prices[0]
        end_trade_date, end_close = prices[-1]
        pct_change = (end_close / start_close - 1.0) * 100.0
        if pct_change < min_pct_change or (max_pct_change is not None and pct_change > max_pct_change):
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


def load_daily_basic(client: TushareClient, trade_date: str) -> dict[str, dict[str, Any]]:
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


def load_price_series_from_cache(cache_dir: Path, start_date: str, end_date: str) -> dict[str, list[tuple[str, float]]]:
    daily_dir = cache_dir / "daily"
    series: dict[str, list[tuple[str, float]]] = {}
    if not daily_dir.exists():
        return series
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
        for item in items:
            close = parse_float(item[close_index])
            if close > 0:
                series.setdefault(item[ts_index], []).append((item[date_index] if date_index is not None else trade_date, close))
    for prices in series.values():
        prices.sort(key=lambda item: item[0])
    return series


def recent_pct(prices: list[tuple[str, float]], days: int = 20) -> float:
    if len(prices) < 2:
        return 0.0
    window = prices[-days:] if len(prices) >= days else prices
    return round((window[-1][1] / window[0][1] - 1.0) * 100.0, 2) if window[0][1] > 0 else 0.0


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


def risk_penalties(name: str, market: str, pe_ttm: float, pb: float) -> tuple[int, list[str]]:
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
    daily_basic = load_daily_basic(client, end_trade_date)
    price_series = load_price_series_from_cache(
        cache_dir,
        first_double_report["start_trade_date"],
        first_double_report["end_trade_date"],
    )

    scored: list[WatchCandidate] = []
    for source in first_double_report.get("candidates", []):
        ts_code = source["ts_code"]
        basic = daily_basic.get(ts_code, {})
        industry = str(source.get("industry") or "")
        pct_change = parse_float(source.get("pct_change"))
        pullback = parse_float(source.get("pullback_from_high"))
        circ_mv_yi = round(parse_float(basic.get("circ_mv")) / 10000.0, 2)
        total_mv_yi = round(parse_float(basic.get("total_mv")) / 10000.0, 2)
        turnover_rate_f = parse_float(basic.get("turnover_rate_f") or basic.get("turnover_rate"))
        volume_ratio = parse_float(basic.get("volume_ratio"))
        pe_ttm = parse_float(basic.get("pe_ttm"))
        pb = parse_float(basic.get("pb"))
        recent_20d = recent_pct(price_series.get(ts_code, []), 20)
        theme_points, theme_reason = THEME_SCORES.get(industry, (3, "行业弹性需要单独验证"))
        penalties, flags = risk_penalties(str(source.get("name") or ""), str(source.get("market") or ""), pe_ttm, pb)
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
            f"自由流通换手 {turnover_rate_f:.2f}%，近 20 日涨幅 {recent_20d:.2f}%。"
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
        candidates=limited,
    )


def write_json_report(report: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv_report(report: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in report.candidates:
        row = asdict(item)
        if "risk_flags" in row:
            row["risk_flags"] = "；".join(item.risk_flags)
            row["next_checks"] = "；".join(item.next_checks)
            row["breakdown"] = json.dumps(asdict(item.breakdown), ensure_ascii=False, sort_keys=True)
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
    table_body = "\n".join(rows) or "<tr><td colspan='10' class='empty'>暂无候选。</td></tr>"
    return render_page(
        "一个股票要想涨 10 倍，先涨 1 倍",
        "筛选最近半年区间涨幅超过阈值的 A 股股票池。数据来自 Tushare Pro 日线行情。",
        [
            ("统计区间", f"{report.start_trade_date} - {report.end_trade_date}"),
            ("自然日回看", str(report.lookback_days)),
            ("上市股票数", str(report.stock_count)),
            ("有行情股票数", str(report.stocks_with_prices)),
            ("入选股票数", str(report.candidate_count)),
        ],
        "<table><thead><tr><th>排名</th><th>股票</th><th>行业</th><th>市场</th><th>区间起点</th><th>区间终点</th><th>区间涨幅</th><th>期间高点</th><th>高点回撤</th><th>交易天数</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>",
        "当前版本使用 Tushare daily 收盘价计算，未做复权处理；停牌股票使用区间内第一条和最后一条可用日线。",
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
    table_body = "\n".join(rows) or "<tr><td colspan='9' class='empty'>暂无候选。</td></tr>"
    return render_page(
        "十倍潜力跟踪池",
        "从最近半年已翻倍的股票中，继续筛选更值得深挖的二阶段候选。不是买入建议，是复盘研究队列。",
        [
            ("源区间", f"{report.start_trade_date} - {report.end_trade_date}"),
            ("输入翻倍股", str(report.input_count)),
            ("跟踪池数量", str(report.watch_count)),
            ("A 级核心", str(report.core_count)),
            ("生成日期", report.generated_at[:10]),
        ],
        "<table><thead><tr><th>排名</th><th>股票</th><th>分层/分数</th><th>行业</th><th>涨幅</th><th>流通市值</th><th>趋势/资金</th><th>候选逻辑</th><th>风险与下一步</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table>",
        "评分模型用于复盘研究，不构成投资建议。下一步可接入前复权、财报增速、公告催化和资金流。",
    )


def render_page(title: str, subtitle: str, cards: list[tuple[str, str]], table: str, note: str) -> str:
    cards_html = "".join(f"<div class='card'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in cards)
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
    return report_dir / "latest.html", report_dir / "latest.csv", report_dir / "latest.json"


def run_first_double(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(args.token, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    report = build_first_double_report(
        client,
        end_date=parse_yyyymmdd(args.end_date) if args.end_date else None,
        lookback_days=args.lookback_days,
        min_pct_change=args.min_pct_change,
        max_pct_change=args.max_pct_change,
        progress=print if args.progress else None,
    )
    html_path = args.html or output_paths(args.output_dir, "first_double")[0]
    csv_path = args.csv or output_paths(args.output_dir, "first_double")[1]
    json_path = args.json or output_paths(args.output_dir, "first_double")[2]
    write_text(html_path, render_first_double_html(report))
    write_csv_report(report, csv_path)
    write_json_report(report, json_path)
    return {"html": str(html_path), "csv": str(csv_path), "json": str(json_path), "count": str(report.candidate_count)}


def run_tenbagger_watch(args: argparse.Namespace) -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    client = TushareClient(args.token, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    source_report = args.source_report or output_paths(args.output_dir, "first_double")[2]
    source = json.loads(source_report.read_text(encoding="utf-8"))
    report = build_watch_report(source, client=client, source_report=source_report, cache_dir=args.cache_dir, limit=args.limit)
    html_path = args.html or output_paths(args.output_dir, "tenbagger_watch")[0]
    csv_path = args.csv or output_paths(args.output_dir, "tenbagger_watch")[1]
    json_path = args.json or output_paths(args.output_dir, "tenbagger_watch")[2]
    write_text(html_path, render_watch_html(report))
    write_csv_report(report, csv_path)
    write_json_report(report, json_path)
    return {"html": str(html_path), "csv": str(csv_path), "json": str(json_path), "count": str(report.watch_count)}


def run_full_chain(args: argparse.Namespace) -> dict[str, dict[str, str]]:
    first = run_first_double(args)
    args.source_report = Path(first["json"])
    watch = run_tenbagger_watch(args)
    return {"first_double": first, "tenbagger_watch": watch}


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token", default=None, help="Tushare token; defaults to TUSHARE_TOKEN")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Tushare API cache directory")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Report output directory")
    parser.add_argument("--no-cache", action="store_true", help="Disable local Tushare cache")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Tushare recap report skills.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    first = subparsers.add_parser("first-double", help="Generate the first-double stock pool report")
    add_common_arguments(first)
    first.add_argument("--end-date", default=None, help="End date in YYYYMMDD; defaults to today")
    first.add_argument("--lookback-days", type=int, default=183, help="Natural days to look back")
    first.add_argument("--min-pct-change", type=float, default=100.0, help="Minimum interval gain percent")
    first.add_argument("--max-pct-change", type=float, default=None, help="Maximum interval gain percent")
    first.add_argument("--html", type=Path, default=None, help="HTML output path")
    first.add_argument("--csv", type=Path, default=None, help="CSV output path")
    first.add_argument("--json", type=Path, default=None, help="JSON output path")
    first.add_argument("--progress", action="store_true", help="Print each fetched trade date")
    first.set_defaults(func=run_first_double)

    watch = subparsers.add_parser("tenbagger-watch", help="Generate the second-stage watchlist report")
    add_common_arguments(watch)
    watch.add_argument("--source-report", type=Path, default=None, help="first-double JSON report")
    watch.add_argument("--limit", type=int, default=80, help="Maximum candidates to output")
    watch.add_argument("--html", type=Path, default=None, help="HTML output path")
    watch.add_argument("--csv", type=Path, default=None, help="CSV output path")
    watch.add_argument("--json", type=Path, default=None, help="JSON output path")
    watch.set_defaults(func=run_tenbagger_watch)

    full = subparsers.add_parser("full-chain", help="Run first-double and tenbagger-watch in sequence")
    add_common_arguments(full)
    full.add_argument("--end-date", default=None, help="End date in YYYYMMDD; defaults to today")
    full.add_argument("--lookback-days", type=int, default=183, help="Natural days to look back")
    full.add_argument("--min-pct-change", type=float, default=100.0, help="Minimum interval gain percent")
    full.add_argument("--max-pct-change", type=float, default=None, help="Maximum interval gain percent")
    full.add_argument("--limit", type=int, default=80, help="Maximum watch candidates to output")
    full.add_argument("--html", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument("--csv", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument("--json", type=Path, default=None, help=argparse.SUPPRESS)
    full.add_argument("--progress", action="store_true", help="Print each fetched trade date")
    full.set_defaults(func=run_full_chain)

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
