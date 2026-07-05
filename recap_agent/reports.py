from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReportResult:
    html: str
    card: dict[str, Any]


def render_recap_report(
    *,
    task: str,
    title: str,
    datasets: Mapping[str, list[Mapping[str, Any]]],
    generated_at: str,
) -> ReportResult:
    sections = "\n".join(_render_table(name, rows) for name, rows in datasets.items())
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #202124; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    .meta {{ color: #5f6368; margin-bottom: 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 28px; }}
    th, td {{ border: 1px solid #dadce0; padding: 8px 10px; text-align: left; }}
    th {{ background: #f8fafd; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">task={html.escape(task)} generated_at={html.escape(generated_at)}</div>
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
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**任务**: {task}\n**生成时间**: {generated_at}"}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": _card_summary(datasets)},
                },
            ],
        },
    }
    return ReportResult(html=html_doc, card=card)


def _render_table(name: str, rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return f"<section><h2>{html.escape(name)}</h2><p>暂无数据</p></section>"
    columns = list(rows[0].keys())
    header = "".join(f"<th>{html.escape(str(col))}</th>" for col in columns)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>"
        for row in rows[:20]
    )
    return f"<section><h2>{html.escape(name)}</h2><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></section>"


def _card_summary(datasets: Mapping[str, list[Mapping[str, Any]]]) -> str:
    lines = [f"- {name}: {len(rows)} rows" for name, rows in datasets.items()]
    return "\n".join(lines) or "暂无数据"


def write_report_files(report: ReportResult, output_dir: str, task: str) -> dict[str, str]:
    from pathlib import Path

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / f"{task}-recap.html"
    card_path = directory / f"{task}-feishu-card.json"
    html_path.write_text(report.html, encoding="utf-8")
    card_path.write_text(json.dumps(report.card, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"html": str(html_path), "card": str(card_path)}

