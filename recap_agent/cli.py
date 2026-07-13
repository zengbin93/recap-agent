from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .data import (
    TushareDataCollector,
    build_market_period,
    default_market_requests,
    filter_recap_dataset,
    resolve_latest_trade_date,
)
from .feishu import FeishuConfig, FeishuSender
from .reports import render_recap_report, write_report_files


TASK_TITLES = {
    "daily": "全球市场日报",
    "weekly": "全球市场周报",
    "monthly": "全球市场月报",
    "potential": "过去半年潜力股复盘",
}


def run_potential_task(
    output_dir: str,
    dry_run: bool,
    trade_date: str | None = None,
    min_pct_change: float = 100.0,
    min_trading_days: int = 80,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[1]
    report_dir = Path(output_dir) / "tushare-recap-reports"
    command = [
        sys.executable,
        str(repo_root / "skills" / "tushare-recap-reports" / "scripts" / "run.py"),
        "full-chain",
        "--output-dir",
        str(output_dir),
        "--progress",
        "--min-pct-change",
        str(min_pct_change),
        "--min-trading-days",
        str(min_trading_days),
    ]
    if trade_date:
        command.extend(["--end-date", trade_date])
    subprocess.run(command, cwd=repo_root, check=True)
    card_path = report_dir / "latest-card.json"
    push_result: dict[str, object]
    try:
        target = FeishuConfig.from_env().resolve("potential")
        payload = json.loads(card_path.read_text(encoding="utf-8"))
        push_result = FeishuSender(dry_run=dry_run).send(target, payload).__dict__
    except ValueError as exc:
        push_result = {"skipped": True, "reason": str(exc)}
    return {
        "task": "potential",
        "files": {
            "first_double_html": str(report_dir / "first_double" / "latest.html"),
            "first_double_csv": str(report_dir / "first_double" / "latest.csv"),
            "first_double_json": str(report_dir / "first_double" / "latest.json"),
            "watch_html": str(report_dir / "tenbagger_watch" / "latest.html"),
            "watch_csv": str(report_dir / "tenbagger_watch" / "latest.csv"),
            "watch_json": str(report_dir / "tenbagger_watch" / "latest.json"),
            "card": str(card_path),
        },
        "feishu": push_result,
    }


def run_task(
    task: str,
    output_dir: str,
    dry_run: bool,
    trade_date: str | None = None,
    potential_min_pct_change: float = 100.0,
    potential_min_trading_days: int = 80,
) -> dict[str, object]:
    if task == "potential":
        return run_potential_task(
            output_dir,
            dry_run,
            trade_date,
            min_pct_change=potential_min_pct_change,
            min_trading_days=potential_min_trading_days,
        )
    collector = TushareDataCollector()
    end_trade_date = resolve_latest_trade_date(collector, trade_date)
    period = build_market_period(task, end_trade_date)
    datasets = {}
    sources = {}
    for name, (table, params) in default_market_requests(task, end_trade_date).items():
        result = collector.fetch_table(table, params)
        datasets[name] = filter_recap_dataset(name, result.rows)
        sources[name] = {
            "source": result.source,
            "warning": result.warning,
            "raw_rows": len(result.rows),
        }

    report = render_recap_report(
        task=task,
        title=TASK_TITLES[task],
        datasets=datasets,
        generated_at=datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds"),
        period=asdict(period),
        sources=sources,
    )
    files = write_report_files(report, output_dir, task)

    push_result = None
    try:
        target = FeishuConfig.from_env().resolve(task)
        push_result = FeishuSender(dry_run=dry_run).send(target, report.card).__dict__
    except ValueError as exc:
        push_result = {"skipped": True, "reason": str(exc)}

    return {
        "task": task,
        "period": asdict(period),
        "files": files,
        "sources": sources,
        "snapshot": report.snapshot,
        "feishu": push_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a global market recap task.")
    parser.add_argument("--task", choices=sorted(TASK_TITLES), default="daily")
    parser.add_argument("--output-dir", default="artifacts/reports")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--trade-date")
    parser.add_argument("--potential-min-pct-change", type=float, default=100.0)
    parser.add_argument("--potential-min-trading-days", type=int, default=80)
    args = parser.parse_args(argv)
    result = run_task(
        args.task,
        args.output_dir,
        args.dry_run,
        args.trade_date,
        args.potential_min_pct_change,
        args.potential_min_trading_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
