"""HTML 报告与飞书卡片的最小渲染。

视图层刻意保持薄：``render_html`` 把 ``sections`` 拼成带转义的 HTML；
``build_card`` 把标题 + lark_md 文本封装成飞书互动卡片。
复盘观点由 Claude Code 在编排时填入，本模块只负责确定性渲染。
"""

from __future__ import annotations

import html

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
         max-width: 920px; margin: 32px auto; padding: 0 16px; color: #1f2937; line-height: 1.65; }}
  h1 {{ border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ color: #2563eb; margin-top: 28px; }}
  .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 24px; }}
  ul {{ padding-left: 22px; }}
  li {{ margin: 4px 0; }}
  .risk {{ background: #fef3c7; border-left: 4px solid #b45309; padding: 10px 14px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">{meta}</div>
{body}
</body>
</html>
"""


def render_html(title: str, sections: list, meta: str = "") -> str:
    """``sections``: ``[{"heading": str, "bullets": [str], "risk"?: bool}]`` → 完整 HTML 字符串。"""
    parts = []
    for sec in sections:
        heading = html.escape(str(sec.get("heading", "")))
        cls = ' class="risk"' if sec.get("risk") else ""
        parts.append(f"<h2{cls}>{heading}</h2>")
        bullets = sec.get("bullets") or []
        if bullets:
            items = "".join(f"<li>{html.escape(str(b))}</li>" for b in bullets)
            parts.append(f"<ul>{items}</ul>")
    return _HTML_TEMPLATE.format(
        title=html.escape(title),
        meta=html.escape(meta),
        body="\n".join(parts),
    )


def build_card(title: str, markdown_content: str, header_color: str = "green") -> dict:
    """飞书互动卡片：``header`` + 一段 ``lark_md`` 文本。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": header_color,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": markdown_content}}
        ],
    }
