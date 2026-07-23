from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from recap_agent.tracker import calculate_strategy_performance, evaluate_sector_risk


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
    active_sectors_section = ""
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
            etf_str = f"\n  🔹 代表ETF：**{etf_info['name']}** (成交 {etf_info['amount_yi']:.1f}亿, 涨 {etf_info['pct_chg']:.1f}%)" if etf_info else ""
            
            top_stocks = _get_top_member_stocks(ts_code, datasets)
            stocks_str = "、".join(s['desc'] for s in top_stocks) if top_stocks else f"领涨: {lead}"
            
            inflow_items.append(f"• **{name}** (+{amt:.1f}亿)\n  🔸 核心流入：{stocks_str}{etf_str}")
        inflow_txt = "\n".join(inflow_items)

        stealth_items = []
        for x in rankings["raw_stealth_inflows"][:3]:
            name = x.get('industry') or '未知'
            amt = float(x.get('net_amount') or 0.0)
            pct = float(x.get('pct_change') or 0.0)
            lead = x.get('lead_stock') or '—'
            ts_code = x.get('ts_code') or ''
            
            etf_info = _get_real_etf_data(name, fund_basics, fund_dailies)
            etf_str = f"\n  🔹 代表ETF：**{etf_info['name']}** (成交 {etf_info['amount_yi']:.1f}亿, 涨 {etf_info['pct_chg']:.1f}%)" if etf_info else ""
            
            top_stocks = _get_top_member_stocks(ts_code, datasets)
            stocks_str = "、".join(s['desc'] for s in top_stocks) if top_stocks else f"领涨: {lead}"
            
            stealth_items.append(f"• **{name}** (+{amt:.1f}亿, 今日涨 {pct:.1f}%)\n  🔸 核心流入：{stocks_str}{etf_str}")
        stealth_txt = "\n".join(stealth_items)
        
        # 拼入活跃人气板块数据
        active_data = _parse_active_sectors_data()
        active_sectors_section = ""
        active_sectors_card_md = ""
        if active_data:
            active_sectors_section = _render_active_sectors_table(active_data)
            
            active_items = []
            for s in active_data.get("sectors", [])[:2]:
                s_name = s.get("name") or "未知"
                hit_count = s.get("hit_count") or 0
                cov = float(s.get("coverage_pct") or 0.0)
                today_pct = float(s.get("today_pct") or 0.0)
                
                # 今日人气股 (前3)
                stks = s.get("hit_stocks", [])[:3]
                stk_str = "、".join(stks) if stks else "—"
                
                pct_str = f"+{today_pct:.1f}%" if today_pct > 0 else f"{today_pct:.1f}%"
                active_items.append(
                    f"• **{s_name}** (覆盖 {cov*100.0:.1f}% | 今日 {pct_str})\n"
                    f"  🔸 今日人气股：{stk_str}"
                )
            if active_items:
                active_sectors_card_md = f"📊 **成交活跃人气板块 (热度前二)**：\n" + "\n".join(active_items) + "\n\n"

        stealth_summary_md = f"🔥 **今日主力净买入前三**：\n{inflow_txt or '无'}\n\n"
        if stealth_txt:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：\n{stealth_txt}\n\n"
        else:
            stealth_summary_md += f"🕵️ **主力暗中建仓（潜伏）板块**：\n暂无满足条件板块\n\n"
            
        if active_sectors_card_md:
            stealth_summary_md += active_sectors_card_md

    sections = active_sectors_section + extra_sections + "\n".join(
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
      --up-bg: rgba(255, 74, 107, 0.06);
      --down-color: #00e676;
      --down-bg: rgba(0, 230, 118, 0.06);
    }}
    
    * {{ box-sizing: border-box; }}
    
    body {{
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      margin: 0;
      padding: 40px 24px;
      color: var(--text-main);
      background-color: var(--bg);
      line-height: 1.6;
    }}
    
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    
    header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      border-radius: 16px;
      padding: 32px 40px;
      color: #ffffff;
      margin-bottom: 32px;
      border: 1px solid rgba(56, 189, 248, 0.15);
      box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
    }}
    
    header h1 {{
      font-size: 32px;
      font-weight: 800;
      margin: 0 0 12px 0;
      letter-spacing: -0.025em;
      background: linear-gradient(to right, #ffffff, #94a3b8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    
    header .meta {{
      font-size: 13px;
      color: #94a3b8;
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
    }}
    
    header .meta span {{
      background: rgba(255, 255, 255, 0.04);
      padding: 4px 14px;
      border-radius: 9999px;
      border: 1px solid rgba(255, 255, 255, 0.06);
    }}
    
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 20px;
      margin-bottom: 32px;
    }}
    
    .summary-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px 24px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
      transition: transform 0.2s, border-color 0.2s;
    }}
    
    .summary-card:hover {{
      transform: translateY(-2px);
      border-color: rgba(56, 189, 248, 0.25);
    }}
    
    .summary-card span {{
      display: block;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    
    .summary-card strong {{
      font-size: 28px;
      font-weight: 700;
      color: #ffffff;
    }}
    
    .text-up {{
      color: var(--up-color) !important;
      font-weight: 700;
    }}
    .text-down {{
      color: var(--down-color) !important;
      font-weight: 700;
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
      margin-left: 6px;
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
      margin-left: 6px;
    }}
    
    .movers {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
      margin-bottom: 32px;
    }}
    
    .movers h3 {{
      margin: 0 0 16px 0;
      font-size: 18px;
      font-weight: 700;
      color: #ffffff;
      border-left: 4px solid var(--accent);
      padding-left: 12px;
    }}
    
    .table-section {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 32px;
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
    }}
    
    .table-section h2 {{
      margin: 0 0 20px 0;
      font-size: 20px;
      font-weight: 700;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    
    .table-section h2::before {{
      content: "";
      display: inline-block;
      width: 6px;
      height: 20px;
      background-color: var(--accent);
      border-radius: 3px;
    }}
    
    .table-wrapper {{
      overflow-x: auto;
      border-radius: 8px;
      border: 1px solid var(--border);
    }}
    
    table {{
      border-collapse: collapse;
      width: 100%;
      background: var(--card-bg);
      font-size: 14px;
      text-align: left;
    }}
    
    th, td {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
    }}
    
    th {{
      background-color: #1e293b;
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 12px;
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
      margin-bottom: 4px;
      transition: background-color 0.15s;
    }}
    
    .badge:hover {{
      background-color: #334155;
    }}
    
    .no-data {{
      color: var(--text-muted);
      padding: 40px;
      text-align: center;
      font-size: 15px;
    }}
    
    @media (max-width: 768px) {{
      body {{ padding: 20px 12px; }}
      header {{ padding: 24px 20px; }}
      header h1 {{ font-size: 24px; }}
      .table-section {{ padding: 16px; }}
      th, td {{ padding: 10px 12px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{html.escape(title)}</h1>
      <div class="meta">
        <span>任务：{html.escape(task)}</span>
        <span>区间：{html.escape(period_text)}</span>
        <span>生成时间：{html.escape(generated_at)}</span>
      </div>
    </header>
    {_render_snapshot_summary(snapshot)}
    {sections}
    <div style="text-align: center; margin-top: 40px; font-size: 12px; color: var(--text-muted);">
      {html.escape(source_text)}
    </div>
  </div>
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


def _get_color_class(col_name: str, val_str: str) -> str:
    val = val_str.strip()
    if not val or val in ("—", "暂无", "暂无对应ETF"):
        return ""
    
    is_metric = any(k in col_name for k in ("涨幅", "跌幅", "涨跌", "流入", "流出", "净买入", "pct_chg", "pct_change"))
    if not is_metric:
        return ""
        
    if val.startswith("+") or val.startswith("▲"):
        return "text-up"
    if val.startswith("-") or val.startswith("▼"):
        return "text-down"
        
    clean_val = val.replace("%", "").replace("亿", "").replace("万", "").strip()
    try:
        num = float(clean_val)
        if num > 0:
            return "text-up"
        if num < 0:
            return "text-down"
    except ValueError:
        pass
        
    return ""


def _render_table(name: str, rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return f"<section class='table-section'><h2>{html.escape(name)}</h2><div class='no-data'>暂无数据</div></section>"
        
    columns = list(rows[0].keys())
    
    column_labels = {
        "排名": "排名",
        "板块名称": "行业板块",
        "主力净流入 (亿元)": "主力资金流入",
        "主力净流出 (亿元)": "主力资金流出",
        "主力净买入 (亿元)": "主力资金买入",
        "今日涨幅": "今日涨跌幅",
        "领涨个股": "主力领涨股",
        "核心买入个股": "主力大额买入股 (前三)",
        "真实代表ETF": "真实代表场内 ETF",
        "ts_code": "代码",
        "symbol": "代号",
        "name": "名称",
        "open": "开盘价",
        "high": "最高价",
        "low": "最低价",
        "close": "收盘价",
        "pre_close": "昨收价",
        "change": "涨跌额",
        "pct_chg": "涨跌幅",
        "vol": "成交量",
        "amount": "成交额",
    }
    
    max_val = 1.0
    for row in rows:
        for col in columns:
            if any(k in col for k in ("主力净流入", "主力净流出", "主力净买入", "资金买入额", "net_amount", "net_amt")):
                try:
                    val_str = str(row.get(col, 0)).replace("亿", "").replace("万", "").replace("+", "").replace("-", "").strip()
                    val_num = abs(float(val_str))
                    if val_num > max_val:
                        max_val = val_num
                except Exception:
                    pass
                    
    header = "".join(f"<th>{html.escape(column_labels.get(col, str(col)))}</th>" for col in columns if col != "raw_row")
    
    body_rows = []
    for row in rows[:20]:
        td_elements = []
        for col in columns:
            if col == "raw_row":
                continue
            val = str(row.get(col, ''))
            color_class = _get_color_class(col, val)
            
            if col == "板块名称":
                raw = row.get("raw_row") if isinstance(row.get("raw_row"), dict) else row
                sig = evaluate_sector_risk(raw)
                risk_tag = f" <span class='{sig.tag_class}' title='{html.escape(sig.reason)}'>{html.escape(sig.tag_label)}</span>" if sig.risk_level != "normal" else ""
                td_elements.append(f"<td><strong style='color:#ffffff; font-size:15px;'>{html.escape(val)}</strong>{risk_tag}</td>")
                
            elif col in ("核心买入个股", "真实代表ETF") and val not in ("", "—", "暂无对应ETF"):
                parts = val.split("、") if "、" in val else [val]
                badges = []
                for p in parts:
                    icon = "⚡️ " if "ETF" in p else ""
                    badges.append(f"<span class='badge'>{icon}{html.escape(p)}</span>")
                td_elements.append(f"<td>{' '.join(badges)}</td>")
                
            elif color_class in ("text-up", "text-down") and any(k in col for k in ("主力净流入", "主力净流出", "主力净买入", "资金买入额", "net_amount", "net_amt")):
                try:
                    val_clean = val.replace("亿", "").replace("万", "").replace("+", "").replace("-", "").strip()
                    val_num = abs(float(val_clean))
                    pct = min(100.0, (val_num / max_val) * 100.0)
                except Exception:
                    pct = 0.0
                
                bar_color = "linear-gradient(90deg, rgba(255,74,107,0.2) 0%, rgba(255,74,107,0.8) 100%)" if color_class == "text-up" else "linear-gradient(90deg, rgba(0,230,118,0.2) 0%, rgba(0,230,118,0.8) 100%)"
                
                bar_html = f"""
                <div style="display: flex; align-items: center; gap: 10px;">
                  <span class="{color_class}" style="min-width: 65px; font-variant-numeric: tabular-nums;">{html.escape(val)}</span>
                  <div style="flex-grow: 1; height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; max-width: 100px;">
                    <div style="width: {pct:.1f}%; height: 100%; background: {bar_color}; border-radius: 3px;"></div>
                  </div>
                </div>
                """
                td_elements.append(f"<td>{bar_html}</td>")
                
            else:
                span_class = f" class='{color_class}'" if color_class else ""
                td_elements.append(f"<td><span{span_class}>{html.escape(val)}</span></td>")
                
        body_rows.append("<tr>" + "".join(td_elements) + "</tr>")
        
    body = "\n".join(body_rows)
    return f"<section class='table-section'><h2>{html.escape(name)}</h2><div class='table-wrapper'><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div></section>"


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
    perf = calculate_strategy_performance()
    
    gainers = (
        "".join(
            f"<li style='margin-bottom: 10px; list-style-type: none; display: flex; justify-content: space-between; font-size: 14px;'><strong>{html.escape(item['label'])}</strong> <span class='text-up'>+{item['pct_change']:.2f}%</span></li>"
            for item in snapshot["top_gainers"]
        )
        or "<li style='list-style-type: none; color: var(--text-muted);'>暂无</li>"
    )
    losers = (
        "".join(
            f"<li style='margin-bottom: 10px; list-style-type: none; display: flex; justify-content: space-between; font-size: 14px;'><strong>{html.escape(item['label'])}</strong> <span class='text-down'>{item['pct_change']:.2f}%</span></li>"
            for item in snapshot["top_losers"]
        )
        or "<li style='list-style-type: none; color: var(--text-muted);'>暂无</li>"
    )
    
    trend = summary["trend"]
    trend_class = "text-up" if any(x in trend for x in ("强", "多", "涨", "牛")) else "text-down" if any(x in trend for x in ("弱", "空", "跌", "熊")) else ""
    indicator = "🔴" if "强" in trend or "多" in trend or "涨" in trend else "🟢" if "弱" in trend or "空" in trend or "跌" in trend else "🟡"
    
    pos = int(summary.get("positive_rows", 0))
    neg = int(summary.get("negative_rows", 0))
    flat = int(summary.get("flat_rows", 0))
    total = pos + neg + flat
    if total <= 0:
        total = 1
    pos_pct = (pos / total) * 100.0
    neg_pct = (neg / total) * 100.0
    flat_pct = (flat / total) * 100.0
    
    return f"""
  <section class="summary" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));">
    <div class="summary-card" style="grid-column: 1 / -1; background: linear-gradient(135deg, rgba(30,41,59,0.8) 0%, rgba(15,23,42,0.9) 100%); border: 1px solid rgba(56, 189, 248, 0.2); padding: 20px 24px;">
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;">
        <div>
          <span style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: flex; align-items: center; gap: 6px;">
            <span>🎯</span> 量化策略历史实证绩效 (近 30 个交易日回测)
          </span>
          <div style="display: flex; gap: 28px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
            <div><span style="font-size: 12px; color: #94a3b8;">T+1 胜率:</span> <strong style="font-size: 22px; color: #ff4a6b; margin-left: 6px;">{perf.t1_win_rate}%</strong></div>
            <div><span style="font-size: 12px; color: #94a3b8;">T+3 胜率:</span> <strong style="font-size: 22px; color: #ff4a6b; margin-left: 6px;">{perf.t3_win_rate}%</strong></div>
            <div><span style="font-size: 12px; color: #94a3b8;">T+1 期望收益:</span> <strong style="font-size: 18px; color: #38bdf8; margin-left: 6px;">+{perf.t1_avg_return}%</strong></div>
            <div><span style="font-size: 12px; color: #94a3b8;">T+3 期望收益:</span> <strong style="font-size: 18px; color: #38bdf8; margin-left: 6px;">+{perf.t3_avg_return}%</strong></div>
          </div>
        </div>
        <div style="font-size: 12px; color: #64748b; background: rgba(255,255,255,0.03); padding: 8px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05);">
          {html.escape(perf.benchmark_note)}
        </div>
      </div>
    </div>
    
    <div class="summary-card">
      <span>盘面研判信号</span>
      <strong class="{trend_class}" style="display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 18px; line-height: 1;">{indicator}</span> {html.escape(trend)}
      </strong>
    </div>
    <div class="summary-card">
      <span>监测股票样本</span>
      <strong>{summary["stock_count"]} <span style="font-size: 14px; font-weight: normal; color: var(--text-muted);">只</span></strong>
    </div>
    <div class="summary-card">
      <span>多头上涨家数</span>
      <strong class="text-up">▲ {summary["positive_rows"]}</strong>
    </div>
    <div class="summary-card">
      <span>空头下跌家数</span>
      <strong class="text-down">▼ {summary["negative_rows"]}</strong>
    </div>
  </section>
  
  <section class="movers" style="padding: 24px;">
    <h3>A 股盘面情绪多空温度计 & 异动监控</h3>
    
    <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 12px; padding: 18px 24px; margin-bottom: 24px;">
      <div style="display: flex; justify-content: space-between; font-size: 14px; color: var(--text-muted); margin-bottom: 10px; font-weight: 500;">
        <span class="text-up">上涨 {pos} 家 ({pos_pct:.1f}%)</span>
        <span style="color: #f8fafc; font-weight: 600;">市场赚钱效应：{pos_pct:.1f}%</span>
        <span class="text-down">下跌 {neg} 家 ({neg_pct:.1f}%)</span>
      </div>
      <div style="display: flex; height: 12px; border-radius: 6px; overflow: hidden; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.05);">
        <div style="width: {pos_pct:.1f}%; background: linear-gradient(90deg, #ff4a6b, #e11d48);" title="上涨"></div>
        <div style="width: {flat_pct:.1f}%; background: #475569;" title="平盘"></div>
        <div style="width: {neg_pct:.1f}%; background: linear-gradient(90deg, #059669, #00e676);" title="下跌"></div>
      </div>
      <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 6px;">
        <span>多头主导区</span>
        <span>平盘 {flat} 家</span>
        <span>空头主导区</span>
      </div>
    </div>
    
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 32px; margin-top: 16px;">
      <div>
        <h4 style="margin: 0 0 16px 0; color: #ff4a6b; font-size: 15px; border-bottom: 1px solid rgba(255,74,107,0.15); padding-bottom: 8px; display: flex; align-items: center; gap: 6px;">
          <span>🔥</span> 涨幅偏离榜首个股
        </h4>
        <ul style="padding: 0; margin: 0;">
          {gainers}
        </ul>
      </div>
      <div>
        <h4 style="margin: 0 0 16px 0; color: #00e676; font-size: 15px; border-bottom: 1px solid rgba(0,230,118,0.15); padding-bottom: 8px; display: flex; align-items: center; gap: 6px;">
          <span>❄️</span> 跌幅偏离榜首个股
        </h4>
        <ul style="padding: 0; margin: 0;">
          {losers}
        </ul>
      </div>
    </div>
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
    
    if task == "daily":
        index_path = directory / "index.html"
        index_path.write_text(report.html, encoding="utf-8")
        
    card_path.write_text(
        json.dumps(report.card, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    snapshot_path.write_text(
        json.dumps(report.snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    results = {
        "html": str(html_path),
        "card": str(card_path),
        "snapshot": str(snapshot_path),
    }
    if task == "daily":
        results["index"] = str(directory / "index.html")
    return results


def _parse_active_sectors_data() -> dict[str, Any] | None:
    # 活跃板块 json 的输出路径
    json_path = Path("artifacts/reports/recap-active-sectors/latest.json")
    if not json_path.exists():
        return None
    try:
        import json as _json
        return _json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _render_active_sectors_table(active_data: dict[str, Any]) -> str:
    sectors = active_data.get("sectors", [])
    recent_days = active_data.get("recent_days", 5)
    if not sectors:
        return ""
        
    rows = []
    for s in sectors[:15]:  # 最多显示 15 个活跃板块
        name = s.get("name") or "未知板块"
        rank = s.get("rank") or "—"
        today_pct = float(s.get("today_pct") or 0.0)
        recent_pct = float(s.get("recent_pct") or 0.0)
        amp = float(s.get("amplitude") or 0.0)
        
        sig = evaluate_sector_risk(s)
        risk_tag = f"<span class='{sig.tag_class}' title='{html.escape(sig.reason)}'>{html.escape(sig.tag_label)}</span>" if sig.risk_level != "normal" else ""

        flow_tag = ""
        if s.get("quote_available"):
            if today_pct > 0:
                flow_tag = f"<span class='tag-up'>主力流入</span>{risk_tag}"
            elif today_pct < 0:
                flow_tag = f"<span class='tag-down'>主力流出</span>{risk_tag}"
            else:
                flow_tag = f"<span class='tag-flat'>多空平衡</span>{risk_tag}"
        else:
            flow_tag = f"<span class='tag-flat'>方向未知</span>{risk_tag}"
            
        today_class = "text-up" if today_pct > 0 else "text-down" if today_pct < 0 else ""
        recent_class = "text-up" if recent_pct > 0 else "text-down" if recent_pct < 0 else ""
        
        quote = (
            f"<strong class='{today_class}' style='font-size:16px;'>{today_pct:+.2f}%</strong>"
            f"<span>{recent_days}日 <strong class='{recent_class}'>{recent_pct:+.2f}%</strong> / 振幅 {amp:.2f}%</span>"
            if s.get("quote_available")
            else "<span>板块行情不可用</span>"
        )
        
        activity = (
            f"<span class='gain'>{s.get('hit_count', 0)}</span> <span style='display:inline; color:var(--text-muted); font-size:14px;'>/ {s.get('sector_size') or '—'}</span>"
            f"<span>覆盖 {float(s.get('coverage_pct', 0))*100.0:.1f}% · 成交 {s.get('turnover_yi', 0):g} 亿</span>"
        )
        
        hit_badges = "".join(f"<span class='badge'>{html.escape(stk)}</span>" for stk in s.get("hit_stocks", [])[:15])
        
        reps_items = []
        for r in s.get("representatives", [])[:4]:
            r_pct = float(r.get("pct_chg") or 0.0)
            r_rec = float(r.get("recent_pct") or 0.0)
            r_today_class = "text-up" if r_pct > 0 else "text-down" if r_pct < 0 else ""
            r_recent_class = "text-up" if r_rec > 0 else "text-down" if r_rec < 0 else ""
            reps_items.append(
                f"<li><strong>{html.escape(r.get('name', ''))}</strong> 今日 <span class='{r_today_class}'>{r_pct:+.2f}%</span> / {recent_days}日 <span class='{r_recent_class}'>{r_rec:+.2f}%</span>"
                f"<span>{html.escape(r.get('ts_code', ''))} · 成交 {r.get('amount_yi', 0):g} 亿</span></li>"
            )
        reps = "".join(reps_items)
        
        change_val = s.get("hit_change")
        change = f"较均值 {change_val:+.1f}" if change_val is not None else ""
        merged = f"<div style='margin-top:8px; font-size:12px; color:var(--text-muted);'>已合并：{'、'.join(s.get('related_sectors', [])[:2])}</div>" if s.get("related_sectors") else ""
        
        rows.append(
            "<tr>"
            f"<td style='font-weight:bold; font-size:16px; color:var(--text-muted);'>{rank}</td>"
            f"<td><strong style='font-size:16px; color:#ffffff;'>{html.escape(name)}</strong>{flow_tag}<span style='font-size:12px; margin-top:6px;'>{html.escape(s.get('index_code', ''))} · {change}</span>{merged}</td>"
            f"<td>{activity}</td>"
            f"<td>{quote}</td>"
            f"<td><div style='max-width:320px;'>{hit_badges}</div></td>"
            f"<td><ul>{reps or '<li>无榜内成分股</li>'}</ul></td>"
            "</tr>"
        )
        
    table_body = "\n".join(rows)
    table = (
        f"<section class='table-section'><h2>🔥 人气成交活跃板块复盘 (热度聚类)</h2>"
        f"<div class='table-wrapper'><table><thead><tr><th style='width:50px;'>排名</th><th>主题簇代表</th><th>命中 / 覆盖</th>"
        f"<th>今日/{recent_days}日</th><th style='width:320px;'>今日人气股</th><th>代表成分股</th></tr></thead>"
        f"<tbody>{table_body}</tbody></table></div></section>"
    )
    return table
