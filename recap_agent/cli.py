from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .data import TushareDataCollector, default_market_requests
from .feishu import FeishuConfig, FeishuSender
from .reports import render_recap_report, write_report_files


TASK_TITLES = {
    "daily": "全球市场日报",
    "weekly": "全球市场周报",
    "monthly": "全球市场月报",
}


def run_task(task: str, output_dir: str, dry_run: bool, trade_date: str | None = None) -> dict[str, object]:
    collector = TushareDataCollector()
    datasets = {}
    sources = {}
    for name, (table, params) in default_market_requests(task, trade_date).items():
        result = collector.fetch_table(table, params)
        datasets[name] = result.rows
        sources[name] = {"source": result.source, "warning": result.warning}

    report = render_recap_report(
        task=task,
        title=TASK_TITLES[task],
        datasets=datasets,
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    files = write_report_files(report, output_dir, task)

    push_result = None
    try:
        target = FeishuConfig.from_env().resolve(task)
        push_result = FeishuSender(dry_run=dry_run).send(target, report.card).__dict__
    except ValueError as exc:
        push_result = {"skipped": True, "reason": str(exc)}

    return {"task": task, "files": files, "sources": sources, "feishu": push_result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a global market recap task.")
    parser.add_argument("--task", choices=sorted(TASK_TITLES), default="daily")
    parser.add_argument("--output-dir", default="artifacts/reports")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--trade-date")
    args = parser.parse_args(argv)
    result = run_task(args.task, args.output_dir, args.dry_run, args.trade_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

