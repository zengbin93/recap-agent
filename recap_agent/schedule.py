"""编排调度：根据日期决定本次跑哪些复盘任务。

- ``daily``：每个编排日都跑；
- ``weekly``：周一跑；
- ``monthly``：每月 1–3 号跑（覆盖月初首个交易日）。

手动 ``--task daily|weekly|monthly`` 时覆盖日期规则，只跑指定任务。
cron 在 UTC 00:00（北京 08:00）触发；CI 设 ``TZ=Asia/Shanghai``，``dt.date.today()`` 即北京日期。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys


def plan(today: dt.date, task: str = "all") -> dict:
    """返回 ``{"daily": bool, "weekly": bool, "monthly": bool}``。"""
    if task and task != "all":
        return {
            "daily": task == "daily",
            "weekly": task == "weekly",
            "monthly": task == "monthly",
        }
    return {
        "daily": True,
        "weekly": today.weekday() == 0,  # 周一
        "monthly": today.day <= 3,       # 月初 1–3 号
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="决定本次编排要跑哪些复盘任务")
    ap.add_argument("--task", default="all", choices=["all", "daily", "weekly", "monthly"])
    ap.add_argument("--as-of", default="", help="YYYY-MM-DD，默认今天")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    decision = plan(today, args.task)
    print(f"as_of={today.strftime('%Y%m%d')}")
    print(f"date_iso={today.isoformat()}")
    for key in ("daily", "weekly", "monthly"):
        print(f"{key}={'true' if decision[key] else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
