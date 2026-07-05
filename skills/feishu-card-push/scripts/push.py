#!/usr/bin/env python3
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from recap_agent.feishu import FeishuConfig, FeishuSender


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=["daily", "weekly", "monthly"], required=True)
    parser.add_argument("--card", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(open(args.card, encoding="utf-8").read())
    target = FeishuConfig.from_env().resolve(args.task)
    result = FeishuSender(dry_run=args.dry_run).send(target, payload)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
