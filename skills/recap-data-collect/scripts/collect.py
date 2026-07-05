#!/usr/bin/env python3
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from recap_agent.data import TushareDataCollector, default_market_requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=["daily", "weekly", "monthly"], default="daily")
    parser.add_argument("--output-dir", default="artifacts/data")
    parser.add_argument("--trade-date")
    args = parser.parse_args()

    collector = TushareDataCollector()
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (table, params) in default_market_requests(args.task, args.trade_date).items():
        result = collector.fetch_table(table, params)
        path = output_dir / f"{name}.json"
        path.write_text(json.dumps(result.rows, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest[name] = {"path": str(path), "source": result.source, "warning": result.warning}
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
