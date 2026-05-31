#!/usr/bin/env python3
"""Run topic 02: second-stage tenbagger potential watchlist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from recap_agent.env import load_dotenv
from recap_agent.topics.tenbagger_watch import (
    build_watch_report,
    load_first_double_report,
    write_csv,
    write_html,
    write_json,
)
from recap_agent.tushare_client import TushareClient, TushareError


DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "tushare"
DEFAULT_SOURCE_REPORT = PROJECT_ROOT / "reports" / "first_double" / "latest.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "tenbagger_watch"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从半年翻倍股中生成十倍潜力跟踪池。"
    )
    parser.add_argument("--token", default=None, help="Tushare token；默认读取 TUSHARE_TOKEN/.env")
    parser.add_argument("--source-report", type=Path, default=DEFAULT_SOURCE_REPORT, help="半年翻倍股票池 JSON")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Tushare API 缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="禁用 Tushare 请求缓存")
    parser.add_argument("--limit", type=int, default=80, help="输出候选数量")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="报告输出目录")
    parser.add_argument("--html", type=Path, default=None, help="HTML 输出路径")
    parser.add_argument("--csv", type=Path, default=None, help="CSV 输出路径")
    parser.add_argument("--json", type=Path, default=None, help="JSON 输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        source = load_first_double_report(args.source_report)
        client = TushareClient(
            args.token,
            cache_dir=args.cache_dir,
            use_cache=not args.no_cache,
        )
        report = build_watch_report(
            source,
            client=client,
            source_report=args.source_report,
            cache_dir=args.cache_dir,
            limit=args.limit,
        )
    except (TushareError, FileNotFoundError, KeyError) as error:
        print(f"错误：{error}")
        return 1

    args.report_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.html or args.report_dir / "latest.html"
    csv_path = args.csv or args.report_dir / "latest.csv"
    json_path = args.json or args.report_dir / "latest.json"
    write_html(report, html_path)
    write_csv(report, csv_path)
    write_json(report, json_path)

    print(f"生成完成：输入 {report.input_count} 只，输出 {report.watch_count} 只，A 级 {report.core_count} 只")
    print(f"区间：{report.start_trade_date} - {report.end_trade_date}")
    print(f"HTML: {html_path}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
