from __future__ import annotations

import unittest
from recap_agent.tracker import (
    SectorRiskSignal,
    StrategyPerformance,
    calculate_strategy_performance,
    evaluate_sector_risk,
)


class TrackerTests(unittest.TestCase):
    def test_calculate_strategy_performance_defaults(self):
        perf = calculate_strategy_performance(None)
        self.assertIsInstance(perf, StrategyPerformance)
        self.assertGreater(perf.t1_win_rate, 50.0)
        self.assertGreater(perf.t3_win_rate, 50.0)
        self.assertEqual(perf.sample_count, 30)

    def test_calculate_strategy_performance_dynamic(self):
        sample_sectors = [
            {"net_amount": 35.0, "pct_change": 1.5},
            {"net_amount": 20.0, "pct_change": 0.8},
            {"net_amount": -10.0, "pct_change": -1.2},
        ]
        perf = calculate_strategy_performance(sample_sectors)
        self.assertGreaterEqual(perf.t1_win_rate, 50.0)
        self.assertLessEqual(perf.t1_win_rate, 85.0)

    def test_evaluate_sector_risk_high_risk(self):
        high_risk_sector = {
            "net_amount": 10.0,
            "pct_change": 2.0,
            "recent_pct": 18.5,  # 累涨 > 15%
            "amplitude": 12.0,
        }
        sig = evaluate_sector_risk(high_risk_sector)
        self.assertEqual(sig.risk_level, "high_risk")
        self.assertIn("高位", sig.tag_label)

    def test_evaluate_sector_risk_divergence(self):
        divergence_sector = {
            "net_amount": -15.0,  # 大额流出
            "pct_change": 1.8,    # 但收涨
            "recent_pct": 3.0,
            "amplitude": 4.0,
        }
        sig = evaluate_sector_risk(divergence_sector)
        self.assertEqual(sig.risk_level, "divergence")
        self.assertIn("背离", sig.tag_label)

    def test_evaluate_sector_risk_stealth(self):
        stealth_sector = {
            "net_amount": 12.0,
            "pct_change": 0.5,    # 温和吸筹
            "is_stealth": True,
        }
        sig = evaluate_sector_risk(stealth_sector)
        self.assertEqual(sig.risk_level, "stealth")
        self.assertIn("潜伏", sig.tag_label)


if __name__ == "__main__":
    unittest.main()
