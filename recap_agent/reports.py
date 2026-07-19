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


def _get_real_etf_data(
    sector_name: str, 
    fund_basics: list[dict[str, Any]], 
    fund_dailies: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not fund_basics or not fund_dailies:
        return None
        
    # 定义关键字匹配规则，映射特制行业
    keywords = [sector_name]
    if "元器件" in sector_name or "电子" in sector_name or "元件" in sector_name:
        keywords = ["电子", "元器件", "半导体", "芯片"]
    elif "半导体" in sector_name or "芯片" in sector_name or "存储芯片" in sector_name or "先进封装" in sector_name:
        keywords = ["芯片", "半导体", "科创芯片"]
    elif "证券" in sector_name or "券商" in sector_name:
        keywords = ["证券", "券商"]
    elif "酿酒" in sector_name or "白酒" in sector_name:
        keywords = ["酒", "食品饮料"]
    elif "通信" in sector_name or "共封装光学" in sector_name:
        keywords = ["5G", "通信"]
    elif "人工智能" in sector_name:
        keywords = ["AI", "人工智能", "软件"]
    elif len(sector_name) >= 2:
        keywords = [sector_name[:2], sector_name]

    # 1. 过滤出名字包含关键字且包含 "ETF" 的基金
    matched_basics = []
    for f in fund_basics:
        fname = f.get("name") or ""
        if "ETF" in fname and any(kw in fname for kw in keywords):
            matched_basics.append(f)
            
    if not matched_basics:
        return None
        
    # 2. 匹配当日行情
    basic_map = {f["ts_code"]: f for f in matched_basics}
    matched_dailies = []
    for d in fund_dailies:
        code = d.get("ts_code")
        if code in basic_map:
            matched_dailies.append({
                "ts_code": code,
                "name": basic_map[code]["name"],
                "pct_chg": d.get("pct_chg", 0.0),
                "amount": d.get("amount", 0.0) # 单位：千元
            })
            
    if not matched_dailies:
        return None
        
    # 3. 取出成交额最大的 ETF 作为流动性最好的代表
    best_etf = max(matched_dailies, key=lambda x: x["amount"])
    return {
        "ts_code": best_etf["ts_code"],
        "name": best_etf["name"],
        "pct_chg": float(best_etf["pct_chg"]),
        "amount_yi": float(best_etf["amount"]) / 100000.0
    }


def _get_top_member_stocks(
    ts_code: str,
    datasets: Mapping[str, list[Mapping[str, Any]]]
) -> list[dict[str, Any]]:
    members = datasets.get(f"members_{ts_code}", [])
    individual_mf = datasets.get("individual_moneyflow", [])
    a_share_daily = datasets.get("a_share_daily", [])
    
    if not members or not individual_mf:
        return []
        
    # 建立个股行情和资金流的快速查询 map
    mf_map = {}
    for r in individual_mf:
        code = r.get("ts_code")
        if code:
            mf_map[code] = r
            
    daily_map = {}
    for r in a_share_daily:
        code = r.get("ts_code")
        if code:
            daily_map[code] = r
            
    scored_stocks = []
    for m in members:
        code = m.get("con_code")
        name = m.get("con_name") or "未知个股"
        if not code:
            continue
            
        mf_info = mf_map.get(code)
        daily_info = daily_map.get(code)
        
        # 主力资金净流入 (万元)
        net_amt = 0.0
        if mf_info:
            try:
                net_amt = float(mf_info.get("net_mf_amount") or 0.0)
            except (ValueError, TypeError):
                pass
                
        # 涨幅 (%)
        pct = 0.0
        if daily_info:
            try:
                pct = float(daily_info.get("pct_change") or daily_info.get("pct_chg") or 0.0)
            except (ValueError, TypeError):
                pass
                
        scored_stocks.append({
            "code": code,
            "name": name,
            "net_amount_wan": net_amt,
            "pct_change": pct
        })
        
    # 按主力净流入降序，选出前三名
    top_stocks = sorted(scored_stocks, key=lambda x: x["net_amount_wan"], reverse=True)[:3]
    
    formatted = []
    for x in top_stocks:
        net_str = f"+{x['net_amount_wan']/10000.0:.2f}亿" if abs(x["net_amount_wan"]) >= 10000 else f"+{x['net_amount_wan']:.1f}万"
        if x["net_amount_wan"] < 0:
            net_str = f"-{abs(x['net_amount_wan'])/10000.0:.2f}亿" if abs(x["net_amount_wan"]) >= 10000 else f"-{abs(x['net_amount_wan']):.1f}万"
            
        formatted.append({
            "name": x["name"],
            "net_amount_str": net_str,
            "pct_change_str": f"{x['pct_change']:.2f}%",
            "desc": f"{x['name']}({net_str}, 涨{x['pct_change']:.1f}%)"
        })
    return formatted


def _process_stealth_flow(
    rows: list[Mapping[str, Any]], 
    datasets: Mapping[str, list[Mapping[str, Any]]] = None
) -> dict[str, list[dict[str, Any]]]:
    if datasets is None:
        datasets = {}
        
    fund_basics = datasets.get("fund_basics", [])
    fund_dailies = datasets.get("fund_dailies", [])

    # 提取主力净流入额和涨幅
    cleaned = []
    for r in rows:
        net_amt = None
        for k in ("net_amount", "net_amt"):
            if r.get(k) is not None:
                try:
                    net_amt = float(r[k])
                except (ValueError, TypeError):
                    pass
        if net_amt is None:
            continue
            
        pct = None
        for k in ("pct_change", "pct_chg", "change_pct"):
            if r.get(k) is not None:
                try:
                    pct = float(r[k])
                except (ValueError, TypeError):
                    pass
                    
        name = r.get("industry") or r.get("name") or r.get("con_name") or "未知板块"
        lead = r.get("lead_stock") or r.get("lead") or "—"
        ts_code = r.get("ts_code") or r.get("code") or ""
        
        cleaned.append({
            "industry": name,
            "net_amount": net_amt,
            "pct_change": pct,
            "lead_stock": lead,
            "ts_code": ts_code,
            "raw_row": r
        })
        
    # 1. 净流入排行 (降序)
    inflows_sorted = sorted([c for c in cleaned if c["net_amount"] > 0], key=lambda x: x["net_amount"], reverse=True)
    top_inflows = []
    for i, c in enumerate(inflows_sorted[:5], 1):
        etf_info = _get_real_etf_data(c["industry"], fund_basics, fund_dailies)
        etf_str = f"{etf_info['name']} ({etf_info['ts_code']}) [成交:{etf_info['amount_yi']:.2f}亿, 涨:{etf_info['pct_chg']:.2f}%]" if etf_info else "暂无对应ETF"
        
        top_stocks = _get_top_member_stocks(c["ts_code"], datasets)
        stocks_str = "、".join(x["desc"] for x in top_stocks) or "—"
        
        top_inflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净流入 (亿元)": f"{c['net_amount']:.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%" if c["pct_change"] is not None else "—",
            "领涨个股": c["lead_stock"],
            "核心买入个股": stocks_str,
            "真实代表ETF": etf_str
        })
        
    # 2. 净流出排行 (升序)
    outflows_sorted = sorted([c for c in cleaned if c["net_amount"] < 0], key=lambda x: x["net_amount"])
    top_outflows = []
    for i, c in enumerate(outflows_sorted[:5], 1):
        etf_info = _get_real_etf_data(c["industry"], fund_basics, fund_dailies)
        etf_str = f"{etf_info['name']} ({etf_info['ts_code']}) [成交:{etf_info['amount_yi']:.2f}亿, 涨:{etf_info['pct_chg']:.2f}%]" if etf_info else "暂无对应ETF"
        
        top_outflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净流出 (亿元)": f"{abs(c['net_amount']):.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%" if c["pct_change"] is not None else "—",
            "领涨个股": c["lead_stock"],
            "真实代表ETF": etf_str
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
        etf_info = _get_real_etf_data(c["industry"], fund_basics, fund_dailies)
        etf_str = f"{etf_info['name']} ({etf_info['ts_code']}) [成交:{etf_info['amount_yi']:.2f}亿, 涨:{etf_info['pct_chg']:.2f}%]" if etf_info else "暂无对应ETF"
        
        top_stocks = _get_top_member_stocks(c["ts_code"], datasets)
        stocks_str = "、".join(x["desc"] for x in top_stocks) or "—"
        
        stealth_inflows.append({
            "排名": i,
            "板块名称": c["industry"],
            "主力净买入 (亿元)": f"{c['net_amount']:.2f}",
            "今日涨幅": f"{c['pct_change']:.2f}%",
            "领涨个股": c["lead_stock"],
            "核心买入个股": stocks_str,
            "真实代表ETF": etf_str
        })
        
    return {
        "top_inflows": top_inflows,
        "top_outflows": top_outflows,
        "stealth_inflows": stealth_inflows,
        "raw_top_inflows": inflows_sorted[:5],
        "raw_stealth_inflows": stealth_sorted[:5]
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
        rankings = _process_stealth_flow(hot_sectors_rows, datasets)
        extra_sections += _render_table("主力资金净流入排行 (Top 5)", rankings["top_inflows"])
        extra_sections += _render_table("主力资金净流出排行 (Top 5)", rankings["top_outflows"])
        extra_sections += _render_table("主力潜伏板块 (资金悄悄建仓)", rankings["stealth_inflows"])
        
        fund_basics = datasets.get("fund_basics", [])
        fund_dailies = datasets.get("fund_dailies", [])
        
        # 飞书卡片主力流向文本提炼 (集成个股及 ETF 推荐，分行树状排版可读性升级)
        inflow_items = []
        for x in rankings["raw_top_inflows"][:3]:
            name = x.get('industry') or '未知'
            amt = float(x.get('net_amount') or 0.0)
            lead = x.get('lead_stock') or '—'
            ts_code = x.get('ts_code') or ''
            
            etf_info = _get_real_etf_data(name, fund_basics, fund_dailies)
            etf_str = f" | ETF: **{etf_info['name']}** (成交 {etf_info['amount_yi']:.1f}亿, 涨 {etf_info['pct_chg']:.1f}%)" if etf_info else ""
            
            top_stocks = _get_top_member_stocks(ts_code, datasets)
            stocks_str = "、".join(s['desc'] for s in top_stocks) if top_stocks else f"领涨: {lead}"
            
            inflow_items.append(f"• **{name}** (+{amt:.1f}亿)\n  * 核心流入股：{stocks_str}{etf_str}")
        inflow_txt = "\n".join(inflow_items)

        stealth_items = []
        for x in rankings["raw_stealth_inflows"][:3]:
            name = x.get('industry') or '未知'
            amt = float(x.get('net_amount') or 0.0)
            pct = float(x.get('pct_change') or 0.0)
            lead = x.get('lead_stock') or '—'
            ts_code = x.get('ts_code') or ''
            
            etf_info = _get_real_etf_data(name, fund_basics, fund_dailies)
            etf_str = f" | ETF: **{etf_info['name']}** (成交 {etf_info['amount_yi']:.1f}亿, 涨 {etf_info['pct_chg']:.1f}%)" if etf_info else ""
            
            top_stocks = _get_top_member_stocks(ts_code, datasets)
            stocks_str = "、".join(s['desc'] for s in top_stocks) if top_stocks else f"领涨: {lead}"
            
            stealth_items.append(f"• **{name}** (+{amt:.1f}亿, 今日涨 {pct:.1f}%)\n  * 核心流入股：{stocks_str}{etf_str}")
        stealth_txt = "\n".join(stealth_items)
        
        stealth_summary_md = f"🔥 **今日主力净买入前三**：\n{inflow_txt or '无'}\n\n"
        if stealth_txt:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：\n{stealth_txt}\n"
        else:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：\n暂无满足条件板块\n"

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
