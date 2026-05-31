"""Second-stage watchlist for first-double stocks with tenbagger potential."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from recap_agent.topics.first_double import parse_float
from recap_agent.tushare_client import TushareClient


THEME_SCORES: dict[str, tuple[int, str]] = {
    "半导体": (10, "半导体国产替代/景气弹性"),
    "通信设备": (9, "AI 算力与通信基础设施"),
    "元器件": (8, "电子硬件周期与 AI 端侧链条"),
    "电气设备": (8, "新能源/电力设备分支机会"),
    "软件服务": (8, "AI 应用与数字化扩散"),
    "专用机械": (7, "设备更新与先进制造"),
    "机床制造": (7, "高端制造母机"),
    "IT设备": (7, "AI 硬件与国产化"),
    "互联网": (6, "应用流量与商业模式弹性"),
    "机械基件": (6, "制造业补链与设备周期"),
    "化工原料": (5, "新材料/周期价格弹性"),
    "玻璃": (4, "材料周期与供需反转"),
    "铝": (4, "有色价格与供给约束"),
    "矿物制品": (4, "材料价格弹性"),
    "环境保护": (4, "政策驱动但弹性分化"),
    "火力发电": (3, "公用事业弹性较低"),
    "供气供热": (3, "公用事业弹性较低"),
    "广告包装": (2, "产业想象力偏弱"),
    "服饰": (2, "消费属性偏强，十倍弹性需额外验证"),
    "纺织机械": (2, "细分制造，需订单验证"),
}


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


def load_first_double_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing first-double report: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


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
            ts_code = item[ts_index]
            close = parse_float(item[close_index])
            if close <= 0:
                continue
            actual_date = item[date_index] if date_index is not None else trade_date
            series.setdefault(ts_code, []).append((actual_date, close))

    for prices in series.values():
        prices.sort(key=lambda item: item[0])
    return series


def recent_pct(prices: list[tuple[str, float]], days: int = 20) -> float:
    if len(prices) < 2:
        return 0.0
    window = prices[-days:] if len(prices) >= days else prices
    start = window[0][1]
    end = window[-1][1]
    if start <= 0:
        return 0.0
    return round((end / start - 1.0) * 100.0, 2)


def stage_score(pct_change: float) -> tuple[int, str]:
    if 120 <= pct_change <= 350:
        return 25, "已完成第一阶段翻倍，但尚未极端透支"
    if 100 <= pct_change < 120:
        return 15, "刚进入翻倍区，仍需确认强度"
    if 350 < pct_change <= 500:
        return 12, "涨幅已很高，后续要看基本面兑现"
    return 5, "涨幅过热或阶段位置不理想"


def size_score(circ_mv_yi: float) -> tuple[int, str]:
    if 20 <= circ_mv_yi <= 150:
        return 25, "流通市值处在较容易继续扩张的区间"
    if 150 < circ_mv_yi <= 300:
        return 16, "中等市值，仍有空间但弹性下降"
    if 300 < circ_mv_yi <= 600:
        return 8, "市值偏大，十倍难度提高"
    if 0 < circ_mv_yi < 20:
        return 10, "市值很小，弹性高但流动性/操纵风险更高"
    return 0, "缺少有效市值数据"


def trend_score(pullback_from_high: float) -> tuple[int, str]:
    if pullback_from_high >= -8:
        return 20, "贴近阶段高点，趋势仍强"
    if pullback_from_high >= -18:
        return 12, "有回撤但趋势未明显破坏"
    if pullback_from_high >= -30:
        return 5, "回撤偏深，需要等待修复"
    return 0, "高位回撤过深，先降优先级"


def liquidity_score(turnover_rate_f: float) -> tuple[int, str]:
    if 3 <= turnover_rate_f <= 18:
        return 15, "换手适中，资金参与充分但未极端拥挤"
    if 1 <= turnover_rate_f < 3 or 18 < turnover_rate_f <= 30:
        return 8, "换手可用，但活跃度偏弱或偏热"
    if turnover_rate_f > 30:
        return 2, "换手过高，短线拥挤"
    return 0, "换手不足或缺少数据"


def heat_score(volume_ratio: float) -> tuple[int, str]:
    if 0.8 <= volume_ratio <= 2.5:
        return 7, "量比温和，未明显高潮"
    if 2.5 < volume_ratio <= 5:
        return 3, "放量较明显，注意短线兑现"
    return 0, "量能状态不理想或缺少数据"


def recent_momentum_score(value: float) -> tuple[int, str]:
    if 5 <= value <= 60:
        return 8, "近 20 日仍有趋势延续"
    if -8 <= value < 5:
        return 4, "近 20 日横向消化"
    if value > 60:
        return 2, "近 20 日涨幅过热"
    return 0, "近 20 日转弱"


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


def build_thesis(industry: str, pct_change: float, circ_mv_yi: float, pullback: float, turnover: float, recent_20d: float) -> str:
    theme = THEME_SCORES.get(industry, (3, "行业弹性需要单独验证"))[1]
    return (
        f"{theme}；半年涨幅 {pct_change:.2f}%，已被市场初步验证；"
        f"流通市值约 {circ_mv_yi:.2f} 亿，回撤 {pullback:.2f}%，"
        f"自由流通换手 {turnover:.2f}%，近 20 日涨幅 {recent_20d:.2f}%。"
    )


def next_checks_for_candidate(industry: str, pe_ttm: float, risk_flags: list[str]) -> list[str]:
    checks = [
        "核对最近两期营收/利润是否出现加速",
        "追踪公告、互动易和机构调研中是否有订单/产能/客户变化",
        "复盘涨停与放量日，确认是板块共振还是孤立炒作",
    ]
    if industry in {"半导体", "元器件", "通信设备", "软件服务", "IT设备"}:
        checks.append("确认是否真实受益于 AI/国产替代/算力链，而非概念蹭热点")
    if industry in {"专用机械", "机床制造", "机械基件", "电气设备"}:
        checks.append("核对下游资本开支、在手订单和设备更新周期")
    if pe_ttm <= 0:
        checks.append("当前 PE_TTM 无效或亏损，必须优先验证盈利拐点")
    if risk_flags:
        checks.append("先处理风险标签，再决定是否进入核心跟踪")
    return checks


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
    for candidate in first_double_report.get("candidates", []):
        ts_code = candidate["ts_code"]
        basic = daily_basic.get(ts_code, {})
        industry = str(candidate.get("industry") or "")
        pct_change = parse_float(candidate.get("pct_change"))
        pullback = parse_float(candidate.get("pullback_from_high"))
        circ_mv_yi = round(parse_float(basic.get("circ_mv")) / 10000.0, 2)
        total_mv_yi = round(parse_float(basic.get("total_mv")) / 10000.0, 2)
        turnover_rate_f = parse_float(basic.get("turnover_rate_f") or basic.get("turnover_rate"))
        volume_ratio = parse_float(basic.get("volume_ratio"))
        pe_ttm = parse_float(basic.get("pe_ttm"))
        pb = parse_float(basic.get("pb"))
        recent_20d = recent_pct(price_series.get(ts_code, []), 20)

        stage_points, stage_reason = stage_score(pct_change)
        size_points, size_reason = size_score(circ_mv_yi)
        trend_points, trend_reason = trend_score(pullback)
        liquidity_points, liquidity_reason = liquidity_score(turnover_rate_f)
        heat_points, heat_reason = heat_score(volume_ratio)
        recent_points, recent_reason = recent_momentum_score(recent_20d)
        theme_points, theme_reason = THEME_SCORES.get(industry, (3, "行业弹性需要单独验证"))
        penalty_points, flags = risk_penalties(
            str(candidate.get("name") or ""),
            str(candidate.get("market") or ""),
            pe_ttm,
            pb,
        )

        score = (
            stage_points
            + size_points
            + trend_points
            + liquidity_points
            + theme_points
            + heat_points
            + recent_points
            + penalty_points
        )
        reasons = [
            stage_reason,
            size_reason,
            trend_reason,
            liquidity_reason,
            heat_reason,
            recent_reason,
        ]
        if flags:
            reasons.extend(flags)

        scored.append(
            WatchCandidate(
                rank=0,
                tier=tier_for_score(score),
                score=score,
                ts_code=ts_code,
                name=str(candidate.get("name") or ""),
                industry=industry,
                market=str(candidate.get("market") or ""),
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
                thesis=build_thesis(industry, pct_change, circ_mv_yi, pullback, turnover_rate_f, recent_20d),
                risk_flags=flags or ["无明显规则风险"],
                next_checks=next_checks_for_candidate(industry, pe_ttm, flags),
                breakdown=ScoreBreakdown(
                    stage=stage_points,
                    size=size_points,
                    trend=trend_points,
                    liquidity=liquidity_points,
                    theme=theme_points,
                    heat=heat_points,
                    recent_momentum=recent_points,
                    penalties=penalty_points,
                ),
                source_rank=int(candidate.get("rank") or 0),
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


def write_json(report: WatchReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(report: WatchReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in report.candidates:
        row = asdict(item)
        row["risk_flags"] = "；".join(item.risk_flags)
        row["next_checks"] = "；".join(item.next_checks)
        row["breakdown"] = json.dumps(asdict(item.breakdown), ensure_ascii=False, sort_keys=True)
        rows.append(row)
    columns = list(rows[0].keys()) if rows else [field.name for field in WatchCandidate.__dataclass_fields__.values()]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def score_bar(value: int) -> str:
    width = max(0, min(100, value))
    return f"<div class='scorebar'><span style='width:{width}%'></span></div>"


def render_html(report: WatchReport) -> str:
    rows = []
    for item in report.candidates:
        risk = "；".join(item.risk_flags)
        checks = "".join(f"<li>{escape(check)}</li>" for check in item.next_checks)
        breakdown = asdict(item.breakdown)
        breakdown_text = " / ".join(f"{key}:{value}" for key, value in breakdown.items())
        rows.append(
            "<tr>"
            f"<td>{item.rank}</td>"
            f"<td><strong>{escape(item.name)}</strong><span>{escape(item.ts_code)}</span></td>"
            f"<td><b>{escape(item.tier)}</b>{score_bar(item.score)}<span>{item.score} 分</span></td>"
            f"<td>{escape(item.industry)}<span>{escape(item.theme_reason)}</span></td>"
            f"<td class='gain'>{fmt_pct(item.pct_change)}<span>20日 {fmt_pct(item.recent_20d_pct)}</span></td>"
            f"<td>{fmt_num(item.circ_mv_yi)} 亿<span>总市值 {fmt_num(item.total_mv_yi)} 亿</span></td>"
            f"<td>{fmt_pct(item.pullback_from_high)}<span>换手 {fmt_pct(item.turnover_rate_f)} / 量比 {fmt_num(item.volume_ratio)}</span></td>"
            f"<td>{escape(item.thesis)}<span>{escape(breakdown_text)}</span></td>"
            f"<td>{escape(risk)}<ul>{checks}</ul></td>"
            "</tr>"
        )
    table_body = "\n".join(rows) or "<tr><td colspan='9' class='empty'>暂无候选。</td></tr>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>十倍潜力跟踪池</title>
  <style>
    :root {{
      --bg:#f6f7f9; --panel:#fff; --text:#20242a; --muted:#667085;
      --line:#dde2e8; --accent:#0f766e; --gain:#b42318; --warn:#9a3412;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    }}
    header {{
      padding:28px 36px 18px; background:var(--panel); border-bottom:1px solid var(--line);
    }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    p {{ margin:0; line-height:1.7; }}
    .subtitle {{ color:var(--muted); }}
    main {{ padding:22px 36px 42px; }}
    .cards {{
      display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; margin-bottom:18px;
    }}
    .card {{
      background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px;
    }}
    .card span {{ display:block; color:var(--muted); font-size:13px; margin-bottom:8px; }}
    .card strong {{ font-size:24px; }}
    .method {{
      background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:18px;
    }}
    .method h2 {{ margin:0 0 8px; font-size:18px; }}
    .method ol {{ margin:8px 0 0 20px; padding:0; color:var(--muted); line-height:1.8; }}
    .table-wrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; min-width:1440px; border-collapse:collapse; }}
    th,td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#f9fafb; color:#475467; font-size:13px; position:sticky; top:0; }}
    td span {{ display:block; color:var(--muted); margin-top:4px; font-size:12px; line-height:1.5; }}
    td ul {{ margin:8px 0 0 18px; padding:0; color:var(--muted); line-height:1.6; }}
    tr:last-child td {{ border-bottom:0; }}
    .gain {{ color:var(--gain); font-weight:700; }}
    .scorebar {{ width:92px; height:7px; background:#e7eaee; border-radius:999px; margin-top:8px; overflow:hidden; }}
    .scorebar span {{ display:block; height:100%; background:var(--accent); margin:0; }}
    .empty {{ text-align:center; color:var(--muted); padding:32px; }}
    .note {{ margin-top:14px; color:var(--muted); font-size:13px; }}
    @media (max-width:900px) {{
      header,main {{ padding-left:18px; padding-right:18px; }}
      .cards {{ grid-template-columns:repeat(2,minmax(140px,1fr)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>十倍潜力跟踪池</h1>
    <p class="subtitle">从“最近半年已翻倍”的股票中，继续筛选更值得深挖的二阶段候选。不是买入建议，是复盘研究队列。</p>
  </header>
  <main>
    <section class="cards">
      <div class="card"><span>源区间</span><strong>{report.start_trade_date} - {report.end_trade_date}</strong></div>
      <div class="card"><span>输入翻倍股</span><strong>{report.input_count}</strong></div>
      <div class="card"><span>跟踪池数量</span><strong>{report.watch_count}</strong></div>
      <div class="card"><span>A 级核心</span><strong>{report.core_count}</strong></div>
      <div class="card"><span>生成时间</span><strong>{escape(report.generated_at[:10])}</strong></div>
    </section>
    <section class="method">
      <h2>代码化筛选思路</h2>
      <ol>
        <li>先确认市场已经投票：最近半年涨幅超过 100%。</li>
        <li>再看还有没有空间：流通市值 20-150 亿给最高分，市值越大十倍难度越高。</li>
        <li>看趋势质量：离阶段高点越近越好，深回撤先降级。</li>
        <li>看资金参与：自由流通换手适中、量比温和优于极端放量。</li>
        <li>看产业想象力：半导体、通信设备、元器件、电气设备、软件、专用机械等优先。</li>
        <li>最后加风险扣分：ST、北交所流动性、过高估值等进入风险标签。</li>
      </ol>
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>排名</th><th>股票</th><th>分层/分数</th><th>行业</th><th>涨幅</th>
            <th>流通市值</th><th>趋势/资金</th><th>候选逻辑</th><th>风险与下一步</th>
          </tr>
        </thead>
        <tbody>{table_body}</tbody>
      </table>
    </section>
    <p class="note">说明：评分模型用于复盘研究，不构成投资建议。当前基础行情为 Tushare daily/daily_basic，涨幅仍使用未复权收盘价；下一步可接入前复权、财报增速、公告催化和资金流。</p>
  </main>
</body>
</html>
"""


def write_html(report: WatchReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
