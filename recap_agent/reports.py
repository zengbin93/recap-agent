from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ReportResult:
    html: str
    card: dict[str, Any]
    snapshot: dict[str, Any] = field(default_factory=dict)


def render_recap_report(
    *,
    task: str,
    title: str,
    datasets: Mapping[str, list[Mapping[str, Any]]],
    generated_at: str,
    period: Mapping[str, str] | None = None,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
) -> ReportResult:
    snapshot = build_market_snapshot(
        task=task,
        title=title,
        datasets=datasets,
        generated_at=generated_at,
        period=period,
        sources=sources,
    )
    sections = "\n".join(
        _render_table(name, rows)
        for name, rows in datasets.items()
        if name != "a_share_daily"
    )
    period_text = _period_text(snapshot)
    source_text = _source_text(snapshot)
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #202124; background: #fafbfc; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    h2 {{ margin-top: 28px; }}
    .meta {{ color: #5f6368; margin-bottom: 24px; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .summary-card {{ background: white; border: 1px solid #dadce0; border-radius: 8px; padding: 14px; }}
    .summary-card span {{ display: block; color: #5f6368; font-size: 12px; margin-bottom: 6px; }}
    .summary-card strong {{ font-size: 20px; }}
    .movers {{ background: white; border: 1px solid #dadce0; border-radius: 8px; padding: 14px 18px; margin-bottom: 26px; }}
    .movers ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .warning {{ color: #9a3412; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 28px; background: white; }}
    th, td {{ border: 1px solid #dadce0; padding: 8px 10px; text-align: left; }}
    th {{ background: #f8fafd; }}
    @media (max-width: 760px) {{ .summary {{ grid-template-columns: repeat(2, minmax(140px, 1fr)); }} }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">task={html.escape(task)} · {html.escape(period_text)} · generated_at={html.escape(generated_at)}<br>{html.escape(source_text)}</div>
  {_render_snapshot_summary(snapshot)}
  {sections}
</body>
</html>
"""
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**区间**: {period_text}\n**市场状态**: {snapshot['summary']['trend']}\n**生成时间**: {generated_at}",
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": _card_summary(snapshot)},
                },
            ],
        },
    }
    return ReportResult(html=html_doc, card=card, snapshot=snapshot)


def build_market_snapshot(
    *,
    task: str,
    title: str,
    datasets: Mapping[str, list[Mapping[str, Any]]],
    generated_at: str,
    period: Mapping[str, str] | None = None,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a small, deterministic evidence object for reports and later LLM use."""

    breadth_rows = datasets.get("a_share_daily", [])
    signal_datasets = {"a_share_daily": breadth_rows} if breadth_rows else {}
    movers = []
    positive_rows = negative_rows = flat_rows = 0
    for dataset_name, rows in signal_datasets.items():
        for row in rows:
            pct_change = _pct_change(row)
            if pct_change is None:
                continue
            if pct_change > 0:
                positive_rows += 1
            elif pct_change < 0:
                negative_rows += 1
            else:
                flat_rows += 1
            movers.append(
                {
                    "dataset": dataset_name,
                    "label": _row_label(row),
                    "pct_change": round(pct_change, 2),
                }
            )

    movers.sort(key=lambda item: item["pct_change"], reverse=True)
    gainers = [item for item in movers if item["pct_change"] > 0]
    losers = [item for item in movers if item["pct_change"] < 0]
    stock_count = positive_rows + negative_rows + flat_rows
    if not stock_count:
        trend = "数据不足"
    elif positive_rows == negative_rows:
        trend = "分化"
    elif positive_rows > negative_rows:
        trend = "偏强"
    else:
        trend = "偏弱"

    source_meta = {
        name: {
            "rows": len(rows),
            "label": _dataset_label(name),
            "source": (sources or {}).get(name, {}).get("source"),
            "warning": (sources or {}).get(name, {}).get("warning"),
            "raw_rows": (sources or {}).get(name, {}).get("raw_rows", len(rows)),
        }
        for name, rows in datasets.items()
    }
    return {
        "task": task,
        "title": title,
        "generated_at": generated_at,
        "period": dict(period or {}),
        "summary": {
            "trend": trend,
            "positive_rows": positive_rows,
            "negative_rows": negative_rows,
            "flat_rows": flat_rows,
            "stock_count": stock_count,
            "breadth_basis": "a_share_daily" if stock_count else None,
            "total_rows": sum(len(rows) for rows in datasets.values()),
        },
        "top_gainers": gainers[:5],
        "top_losers": list(reversed(losers[-5:])),
        "datasets": source_meta,
    }


def _render_table(name: str, rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return f"<section><h2>{html.escape(name)}</h2><p>暂无数据</p></section>"
    columns = list(rows[0].keys())
    header = "".join(f"<th>{html.escape(str(col))}</th>" for col in columns)
    body = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns)
        + "</tr>"
        for row in rows[:20]
    )
    return f"<section><h2>{html.escape(name)}</h2><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></section>"


def _pct_change(row: Mapping[str, Any]) -> float | None:
    for key in ("pct_chg", "pct_change", "change_pct"):
        value = row.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _row_label(row: Mapping[str, Any]) -> str:
    for key in ("name", "con_name", "index_name", "ts_code"):
        if row.get(key):
            return str(row[key])
    return "未命名"


def _dataset_label(name: str) -> str:
    return {
        "indices": "主要指数",
        "hot_sectors": "板块资金流",
        "a_share_daily": "A股日行情",
        "weekly_moneyflow": "周资金流",
        "monthly_fund_flow": "月基金流向",
    }.get(name, name)


def _period_text(snapshot: Mapping[str, Any]) -> str:
    period = snapshot.get("period") or {}
    start = period.get("start_date")
    end = period.get("end_date")
    if start and end and start != end:
        return f"{start} - {end}"
    return str(end or start or "未指定交易日")


def _source_text(snapshot: Mapping[str, Any]) -> str:
    sources = []
    for name, meta in (snapshot.get("datasets") or {}).items():
        source = meta.get("source") or "unknown"
        warning = f" ({meta['warning']})" if meta.get("warning") else ""
        raw_rows = meta.get("raw_rows", meta.get("rows", 0))
        rows = meta.get("rows", 0)
        raw_note = f"，原始返回 {raw_rows} 条" if raw_rows != rows else ""
        sources.append(
            f"{meta.get('label') or name}: {rows} 条，来源 {source}{raw_note}{warning}"
        )
    return "数据源：" + "；".join(sources) if sources else "数据源：暂无"


def _render_snapshot_summary(snapshot: Mapping[str, Any]) -> str:
    summary = snapshot["summary"]
    gainers = (
        "".join(
            f"<li>{html.escape(item['label'])}：{item['pct_change']:.2f}%</li>"
            for item in snapshot["top_gainers"]
        )
        or "<li>暂无</li>"
    )
    losers = (
        "".join(
            f"<li>{html.escape(item['label'])}：{item['pct_change']:.2f}%</li>"
            for item in snapshot["top_losers"]
        )
        or "<li>暂无</li>"
    )
    return f"""
  <section class="summary">
    <div class="summary-card"><span>市场状态</span><strong>{html.escape(summary["trend"])}</strong></div>
    <div class="summary-card"><span>A股样本</span><strong>{summary["stock_count"]}</strong></div>
    <div class="summary-card"><span>A股上涨</span><strong>{summary["positive_rows"]}</strong></div>
    <div class="summary-card"><span>A股下跌</span><strong>{summary["negative_rows"]}</strong></div>
  </section>
  <section class="movers">
    <strong>规则化摘要</strong>
    <ul><li>A股涨幅靠前：<ul>{gainers}</ul></li><li>A股跌幅靠前：<ul>{losers}</ul></li></ul>
  </section>
"""


def _card_summary(snapshot: Mapping[str, Any]) -> str:
    summary = snapshot["summary"]
    lines = [
        f"- 市场状态: {summary['trend']}",
        f"- A股上涨/下跌/平盘: {summary['positive_rows']}/{summary['negative_rows']}/{summary['flat_rows']}",
        f"- A股样本: {summary['stock_count']}",
    ]
    for name, meta in snapshot["datasets"].items():
        warning = f"，警告: {meta['warning']}" if meta.get("warning") else ""
        raw_rows = meta.get("raw_rows", meta["rows"])
        raw_note = f"，原始返回 {raw_rows} 条" if raw_rows != meta["rows"] else ""
        lines.append(
            f"- {meta.get('label') or name}: {meta['rows']} 条，来源 {meta.get('source') or 'unknown'}{raw_note}{warning}"
        )
    return "\n".join(lines) or "暂无数据"


def write_report_files(
    report: ReportResult, output_dir: str, task: str
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / f"{task}-recap.html"
    card_path = directory / f"{task}-feishu-card.json"
    snapshot_path = directory / f"{task}-snapshot.json"
    html_path.write_text(report.html, encoding="utf-8")
    card_path.write_text(
        json.dumps(report.card, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    snapshot_path.write_text(
        json.dumps(report.snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "html": str(html_path),
        "card": str(card_path),
        "snapshot": str(snapshot_path),
    }
