"""编排调度逻辑测试：daily 每天、weekly 周一、monthly 月初 1-3 号。"""

import datetime as dt
import unittest

from recap_agent.schedule import plan


class PlanTest(unittest.TestCase):
    def test_monday_runs_daily_and_weekly_not_monthly(self):
        # 2024-01-08 是周一，非月初
        p = plan(dt.date(2024, 1, 8), "all")
        self.assertTrue(p["daily"])
        self.assertTrue(p["weekly"])
        self.assertFalse(p["monthly"])

    def test_midweek_runs_daily_only(self):
        # 2024-01-10 周三，非月初
        p = plan(dt.date(2024, 1, 10), "all")
        self.assertTrue(p["daily"])
        self.assertFalse(p["weekly"])
        self.assertFalse(p["monthly"])

    def test_month_first_three_days(self):
        for day in (1, 2, 3):
            self.assertTrue(plan(dt.date(2024, 1, day), "all")["monthly"], day)
        self.assertFalse(plan(dt.date(2024, 1, 4), "all")["monthly"])

    def test_explicit_task_overrides_schedule(self):
        # 手动指定 weekly 时，即使不是周一也只跑 weekly
        p = plan(dt.date(2024, 1, 10), "weekly")
        self.assertEqual(p, {"daily": False, "weekly": True, "monthly": False})

    def test_explicit_all_token(self):
        p = plan(dt.date(2024, 1, 10), "all")
        self.assertTrue(p["daily"])


if __name__ == "__main__":
    unittest.main()
