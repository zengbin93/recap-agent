#!/usr/bin/env python3
"""feishu-card-push: 按 FEISHU_WEBHOOKS 把指定任务的卡片推送到飞书。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from recap_agent.feishu import send
from recap_agent.feishu.webhooks import parse_webhooks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="推送飞书复盘卡片")
    ap.add_argument("--task", required=True, choices=["daily", "weekly", "monthly"])
    ap.add_argument("--card", required=True, help="卡片 JSON 文件路径")
    ap.add_argument("--webhooks", default=os.environ.get("FEISHU_WEBHOOKS"))
    args = ap.parse_args(argv)

    if not args.webhooks:
        print("ERROR: FEISHU_WEBHOOKS 未配置（用 --webhooks 或环境变量）", file=sys.stderr)
        return 2
    try:
        wh = parse_webhooks(args.webhooks)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    target = wh.resolve(args.task)
    card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    result = send.send_card(target.key, card, sign_secret=target.sign_secret)

    if result["ok"]:
        print(f"OK: {args.task} 卡片已推送（HTTP {result['status']}）")
        return 0
    print(f"ERROR: 推送失败 {result}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
