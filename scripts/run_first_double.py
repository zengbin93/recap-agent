#!/usr/bin/env python3
"""Run topic 01: stocks that doubled in the recent half year."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from recap_agent.topics.first_double import (
    build_first_double_report,
    write_csv,
    write_html,
    write_json,
)
from recap_agent.env import load_dotenv
from recap_agent.tushare_client import TushareClient, TushareError


DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "tushare"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "first_double"


def parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="筛选最近半年区间涨幅超过 100% 的股票池，并生成 HTML 复盘报告。"
    )
    parser.add_argument("--token", default=None, help="Tushare token；默认读取 TUSHARE_TOKEN")
    parser.add_argument("--end-date", default=None, help="统计截止日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--lookback-days", type=int, default=183, help="回看自然日天数")
    parser.add_argument("--min-pct-change", type=float, default=100.0, help="最低区间涨幅百分比")
    parser.add_argument("--max-pct-change", type=float, default=None, help="最高区间涨幅百分比；不填则不设上限")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Tushare API 缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="禁用本地缓存")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="报告输出目录")
    parser.add_argument("--html", type=Path, default=None, help="HTML 输出路径")
    parser.add_argument("--csv", type=Path, default=None, help="CSV 输出路径")
    parser.add_argument("--json", type=Path, default=None, help="JSON 输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    end_date = parse_yyyymmdd(args.end_date) if args.end_date else None
    try:
        client = TushareClient(
            args.token,
            cache_dir=args.cache_dir,
            use_cache=not args.no_cache,
        )
        report = build_first_double_report(
            client,
            end_date=end_date,
            lookback_days=args.lookback_days,
            min_pct_change=args.min_pct_change,
            max_pct_change=args.max_pct_change,
            progress=print,
        )
    except TushareError as error:
        print(f"错误：{error}")
        return 1

    args.report_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.html or args.report_dir / "latest.html"
    csv_path = args.csv or args.report_dir / "latest.csv"
    json_path = args.json or args.report_dir / "latest.json"
    write_html(report, html_path)
    write_csv(report, csv_path)
    write_json(report, json_path)

    pct_scope = f"{args.min_pct_change:.2f}% 以上"
    if args.max_pct_change is not None:
        pct_scope = f"{args.min_pct_change:.2f}% - {args.max_pct_change:.2f}%"
    print(f"筛选完成：{report.candidate_count} 只股票区间涨幅在 {pct_scope}")
    print(f"区间：{report.start_trade_date} - {report.end_trade_date}")
    print(f"HTML: {html_path}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
