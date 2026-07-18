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


# 板块到常用行业 ETF 的静态映射字典
SECTOR_TO_ETF = {
    "银行": "银行ETF (512800)",
    "电力": "电力ETF (561560)",
    "元器件": "电子ETF (515260)",
    "半导体": "半导体ETF (512480)",
    "芯片": "芯片ETF (159995)",
    "存储芯片": "科创芯片ETF (588200)",
    "光伏": "光伏ETF (515790)",
    "新能源车": "新能源车ETF (515030)",
    "白酒": "酒ETF (512690)",
    "证券": "证券ETF (512880)",
    "券商": "证券ETF (512880)",
    "军工": "军工ETF (512660)",
    "计算机": "计算机ETF (512720)",
    "共封装光学": "5G通信ETF (515050)",
    "通信": "5G通信ETF (515050)",
    "人工智能": "AI.ETF (512930)",
    "软件": "软件ETF (515220)",
    "医药": "医药ETF (512010)",
    "有色": "有色金属ETF (512400)",
    "钢铁": "钢铁ETF (515290)",
    "煤炭": "煤炭ETF (515200)",
    "地产": "房地产ETF (512200)",
    "游戏": "游戏ETF (159869)",
    "传媒": "传媒ETF (512980)",
    "酿酒": "酒ETF (512690)",
    "消费": "消费ETF (159928)",
    "电子": "电子ETF (515260)",
    "黄金": "黄金ETF (518880)",
    "汽车": "汽车ETF (516110)",
    "家电": "龙头家电ETF (159730)",
    "农业": "农业ETF (159825)",
}


def get_matched_etf(sector_name: str) -> str:
    """模糊匹配板块所对应的 ETF"""
    for key, val in SECTOR_TO_ETF.items():
        if key in sector_name or sector_name in key:
            return val
    return "暂无匹配ETF"


def _process_stealth_flow(rows: list[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    # 提取主力净流入额和涨幅
    cleaned = []
    for r in rows:
        # 获取 net_amount
        net_amt = None
        for k in ("net_amount", "net_amt"):
            if r.get(k) is not None:
                try:
                    net_amt = float(r[k])
                except (ValueError, TypeError):
                    pass
        if net_amt is None:
            continue
            
        # 获取 pct_change
        pct = None
        for k in ("pct_change", "pct_chg", "change_pct"):
            if r.get(k) is not None:
                try:
                    pct = float(r[k])
                except (ValueError, TypeError):
                    pass
                    
        # 获取板块名
        name = r.get("industry") or r.get("name") or r.get("con_name") or "未知板块"
        lead = r.get("lead_stock") or r.get("lead") or "—"
        
        cleaned.append({
            "industry": name,
            "net_amount": net_amt,
            "pct_change": pct,
            "lead_stock": lead,
            "raw_row": r
        })
        
    # 1. 净流入排行 (降序)
    inflows_sorted = sorted([c for c in cleaned if c["net_amount"] > 0], key=lambda x: x["net_amount"], reverse=True)
    top_inflows = []
    for i, c in enumerate(inflows_sorted[:5], 1):
        top_inflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净流入 (亿元)": f"{c['net_amount']:.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%" if c["pct_change"] is not None else "—",
            "领涨个股": c["lead_stock"],
            "对应ETF": get_matched_etf(c["industry"])
        })
        
    # 2. 净流出排行 (升序)
    outflows_sorted = sorted([c for c in cleaned if c["net_amount"] < 0], key=lambda x: x["net_amount"])
    top_outflows = []
    for i, c in enumerate(outflows_sorted[:5], 1):
        top_outflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净流出 (亿元)": f"{abs(c['net_amount']):.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%" if c["pct_change"] is not None else "—",
            "领涨个股": c["lead_stock"],
            "对应ETF": get_matched_etf(c["industry"])
        })
        
    # 3. 主力潜伏（悄悄建仓）板块 (默认使用回测出的最优经验保底参数：净买入 > 5.0亿 且 0.0% <= 涨幅 <= 3.0%)
    min_amount = 5.0
    lower_pct = 0.0
    upper_pct = 3.0
    try:
        best_cfg_path = Path("artifacts/reports/backtest_best.json")
        if best_cfg_path.exists():
            import json as _json
            best_cfg = _json.loads(best_cfg_path.read_text(encoding="utf-8"))
            min_amount = float(best_cfg.get("min_amount", min_amount))
            lower_pct = float(best_cfg.get("lower_pct", lower_pct))
            upper_pct = float(best_cfg.get("upper_pct", upper_pct))
    except Exception:
        pass

    stealth_list = []
    for c in cleaned:
        if c["net_amount"] > min_amount and c["pct_change"] is not None and lower_pct <= c["pct_change"] <= upper_pct:
            stealth_list.append(c)
    stealth_sorted = sorted(stealth_list, key=lambda x: x["net_amount"], reverse=True)
    stealth_inflows = []
    for i, c in enumerate(stealth_sorted[:5], 1):
        stealth_inflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净买入 (亿元)": f"{c['net_amount']:.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%",
            "领涨个股": c["lead_stock"],
            "对应ETF": get_matched_etf(c["industry"])
        })
        
    return {
        "top_inflows": top_inflows,
        "top_outflows": top_outflows,
        "stealth_inflows": stealth_inflows,
        "raw_top_inflows": [x for x in inflows_sorted[:5]],
        "raw_stealth_inflows": [x for x in stealth_sorted[:5]]
    }


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
    
    # 提取主力板块资金流数据，生成精简特制表格展示在正文头部
    hot_sectors_rows = datasets.get("hot_sectors", [])
    extra_sections = ""
    stealth_summary_md = ""
    if hot_sectors_rows:
        rankings = _process_stealth_flow(hot_sectors_rows)
        extra_sections += _render_table("主力资金净流入排行 (Top 5)", rankings["top_inflows"])
        extra_sections += _render_table("主力资金净流出排行 (Top 5)", rankings["top_outflows"])
        extra_sections += _render_table("主力潜伏板块 (资金悄悄建仓)", rankings["stealth_inflows"])
        
        # 飞书卡片主力流向文本提炼 (集成个股及 ETF 推荐)
        inflow_items = []
        for x in rankings["raw_top_inflows"][:3]:
            name = x.get('industry') or x.get('name') or '未知'
            amt = float(x.get('net_amount') or x.get('net_amt') or 0.0)
            lead = x.get('lead_stock') or x.get('lead') or '—'
            etf = get_matched_etf(name)
            etf_str = f" | ETF: {etf}" if etf != "暂无匹配ETF" else ""
            inflow_items.append(f"{name}(+{amt:.1f}亿 | 领涨: {lead}{etf_str})")
        inflow_txt = "、".join(inflow_items)

        stealth_items = []
        for x in rankings["raw_stealth_inflows"][:3]:
            name = x.get('industry') or x.get('name') or '未知'
            amt = float(x.get('net_amount') or x.get('net_amt') or 0.0)
            pct = float(x.get('pct_change') or x.get('pct_chg') or 0.0)
            lead = x.get('lead_stock') or x.get('lead') or '—'
            etf = get_matched_etf(name)
            etf_str = f" | ETF: {etf}" if etf != "暂无匹配ETF" else ""
            stealth_items.append(f"{name}(+{amt:.1f}亿, 涨{pct:.1f}% | 领涨: {lead}{etf_str})")
        stealth_txt = "、".join(stealth_items)
        
        stealth_summary_md = f"🔥 **今日主力净买入前三**：{inflow_txt or '无'}\n"
        if stealth_txt:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：{stealth_txt}\n"
        else:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：暂无满足条件板块\n"

    sections = extra_sections + "\n".join(
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
    
    card_elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**区间**: {period_text}\n**市场状态**: {snapshot['summary']['trend']}\n**生成时间**: {generated_at}",
            },
        }
    ]
    if stealth_summary_md:
        card_elements.append({"tag": "hr"})
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": stealth_summary_md,
            }
        })
    card_elements.extend([
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": _card_summary(snapshot)},
        }
    ])

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": card_elements,
        },
    }
    import os
    pages_url = os.environ.get("RECAP_PAGES_URL")
    if pages_url:
        base_url = pages_url.rstrip("/")
        report_url = f"{base_url}/{task}-recap.html"
        card["card"]["elements"].append(
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
