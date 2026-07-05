#!/usr/bin/env python3
"""渲染某复盘任务的 HTML 报告 + 飞书卡片 JSON（daily/weekly/monthly 通用入口）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from recap_agent.reports.pipeline import run_recap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="渲染复盘报告与卡片")
    ap.add_argument("--task", required=True, choices=["daily", "weekly", "monthly"])
    ap.add_argument("--date", required=True, help="交易日或周期标识")
    ap.add_argument("--sections", required=True, help="sections JSON 文件路径")
    ap.add_argument("--output-dir", default="reports")
    ap.add_argument("--artifacts-dir", default="artifacts")
    args = ap.parse_args(argv)

    sections = json.loads(Path(args.sections).read_text(encoding="utf-8"))
    out = run_recap(
        args.task, args.date, sections,
        output_dir=args.output_dir, artifacts_dir=args.artifacts_dir,
    )
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
