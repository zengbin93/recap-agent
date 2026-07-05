#!/usr/bin/env python3
"""recap-data-collect: 采集 tushare 复盘数据集，写标准化 JSON + manifest。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from recap_agent.data.cache import Cache
from recap_agent.data.tushare_client import SkipDataset, fetch_dataset


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="采集 tushare 复盘数据集")
    ap.add_argument("--as-of-date", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument(
        "--datasets", default="daily,adj_factor",
        help="逗号分隔的 tushare API 名（默认 daily,adj_factor）",
    )
    ap.add_argument("--token", default=os.environ.get("TUSHARE_TOKEN"))
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)

    if not args.token:
        print("ERROR: TUSHARE_TOKEN 未配置（用 --token 或环境变量）", file=sys.stderr)
        return 2

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cache = Cache(args.cache_dir or (output / ".cache"))

    manifest = {"as_of_date": args.as_of_date, "datasets": {}, "skipped": []}
    for ds in [d.strip() for d in args.datasets.split(",") if d.strip()]:
        params = {"trade_date": args.as_of_date}
        rows = cache.get(ds, trade_date=args.as_of_date)
        source = "cache"
        if rows is None:
            source = "tushare"
            try:
                rows = fetch_dataset(ds, args.token, params)
            except SkipDataset as exc:
                print(f"WARN: 跳过 {ds}: {exc}", file=sys.stderr)
                manifest["skipped"].append({"dataset": ds, "reason": str(exc)})
                continue
            cache.put(ds, rows, trade_date=args.as_of_date)
        out_file = output / f"{ds}.json"
        out_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        manifest["datasets"][ds] = {
            "rows": len(rows),
            "file": out_file.name,
            "source": source,
        }
        print(f"OK: {ds}  {len(rows)} 行  [{source}] →  {out_file.name}")

    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 全部跳过 → 退出码非零，方便 CI 察觉
    return 0 if manifest["datasets"] else 1


if __name__ == "__main__":
    sys.exit(main())
