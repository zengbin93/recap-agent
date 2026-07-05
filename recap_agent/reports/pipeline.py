"""复盘报告 pipeline：把结构化 sections 渲染成 HTML 报告 + 飞书卡片 JSON。

daily / weekly / monthly 复盘 skill 共用本模块，由各自 ``scripts/run.py`` 薄入口调用。
"""

from __future__ import annotations

import json
from pathlib import Path

from .render import build_card, render_html

_TITLES = {
    "daily": "全球市场日复盘 {date}",
    "weekly": "全球市场周复盘 {date}",
    "monthly": "全球市场月复盘 {date}",
}


def sections_to_markdown(sections: list) -> str:
    """sections → lark_md 文本（用于卡片正文）。"""
    lines = []
    for sec in sections:
        lines.append(f"**{sec.get('heading', '')}**")
        for bullet in sec.get("bullets", []) or []:
            lines.append(f"- {bullet}")
        lines.append("")
    return "\n".join(lines).strip()


def run_recap(
    task: str,
    as_of_date: str,
    sections: list,
    output_dir: str = "reports",
    artifacts_dir: str = "artifacts",
) -> dict:
    """渲染 HTML 报告与飞书卡片，落盘并返回两处路径。"""
    title = _TITLES[task].format(date=as_of_date)
    meta = f"task={task}   as_of={as_of_date}"

    html_out = render_html(title, sections, meta=meta)
    card = build_card(title, sections_to_markdown(sections))

    out_dir = Path(output_dir) / task
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{as_of_date}.html"
    html_path.write_text(html_out, encoding="utf-8")

    cards_dir = Path(artifacts_dir) / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    card_path = cards_dir / f"{task}.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    return {"html": str(html_path), "card": str(card_path)}
