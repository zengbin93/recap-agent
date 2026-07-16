import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "tushare-recap-reports" / "scripts" / "run.py"
SPEC = importlib.util.spec_from_file_location("tushare_recap_reports_run", SCRIPT)
run = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = run
SPEC.loader.exec_module(run)


class FakeTushareClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def query(self, api_name, *, params=None, fields=None, cache_key=None):
        self.calls.append((api_name, params or {}, cache_key))
        response = self.responses.get(api_name)
        if callable(response):
            return response(params or {})
        return response or []


class TushareRecapReportsTests(unittest.TestCase):
    def test_is_a_share_excludes_b_shares_and_other_markets(self):
        main_board = {"market": "主板"}
        self.assertTrue(run.is_a_share("600000.SH", main_board))
        self.assertTrue(run.is_a_share("300000.SZ", {"market": "创业板"}))
        self.assertTrue(run.is_a_share("430001.BJ", {"market": "北交所"}))
        self.assertFalse(run.is_a_share("200001.SZ", main_board))
        self.assertFalse(run.is_a_share("600000.SH", {"market": "ETF"}))

    def test_market_regime_uses_broad_index_trend(self):
        index_rows = [
            {
                "ts_code": "000300.SH",
                "trade_date": f"2026{index:04d}",
                "close": 100 + index,
            }
            for index in range(1, 81)
        ]
        client = FakeTushareClient({"index_daily": index_rows})

        regime = run.load_market_regime(client, "20260101", "20260710")

        self.assertEqual(regime["label"], "偏强")
        self.assertEqual(regime["score"], 10)
        self.assertIn("20日中位", regime["evidence"])

    def test_qfq_daily_batch_applies_adjustment_factor(self):
        client = FakeTushareClient(
            {
                "daily": [
                    {"ts_code": "600000.SH", "trade_date": "20260710", "close": 10}
                ],
                "adj_factor": [
                    {"ts_code": "600000.SH", "trade_date": "20260710", "adj_factor": 2}
                ],
            }
        )

        batch = run.load_daily_by_date(client, "20260710", price_mode="qfq")

        self.assertEqual(batch.rows[0]["close"], 20)
        self.assertEqual(batch.adjusted_count, 1)
        self.assertIsNone(batch.warning)

        normalized = run.load_daily_by_date(
            client,
            "20260710",
            price_mode="qfq",
            reference_factors={"600000.SH": 4},
        )
        self.assertEqual(normalized.rows[0]["close"], 5)

    def test_first_double_requires_minimum_trading_days_and_a_share_universe(self):
        dates = ["20260706", "20260707", "20260708", "20260709"]

        def daily(params):
            trade_date = params["trade_date"]
            rows = [
                {
                    "ts_code": "600000.SH",
                    "trade_date": trade_date,
                    "close": len(dates[: dates.index(trade_date) + 1]),
                }
            ]
            if trade_date in dates[:2]:
                rows.append(
                    {
                        "ts_code": "300000.SZ",
                        "trade_date": trade_date,
                        "close": 10 + dates.index(trade_date),
                    }
                )
            rows.append({"ts_code": "200001.SZ", "trade_date": trade_date, "close": 10})
            return rows

        client = FakeTushareClient(
            {
                "trade_cal": [{"cal_date": item, "is_open": 1} for item in dates],
                "stock_basic": [
                    {
                        "ts_code": "600000.SH",
                        "name": "Alpha",
                        "industry": "软件服务",
                        "market": "主板",
                        "list_date": "20200101",
                    },
                    {
                        "ts_code": "300000.SZ",
                        "name": "Beta",
                        "industry": "软件服务",
                        "market": "创业板",
                        "list_date": "20200101",
                    },
                    {
                        "ts_code": "200001.SZ",
                        "name": "BShare",
                        "industry": "软件服务",
                        "market": "主板",
                        "list_date": "20200101",
                    },
                ],
                "daily": daily,
                "adj_factor": lambda params: [
                    {
                        "ts_code": row["ts_code"],
                        "trade_date": params["trade_date"],
                        "adj_factor": 1,
                    }
                    for row in daily(params)
                ],
            }
        )

        report = run.build_first_double_report(
            client,
            end_date=date(2026, 7, 9),
            lookback_days=3,
            min_pct_change=0,
            min_trading_days=3,
        )

        self.assertEqual(report.stock_count, 2)
        self.assertEqual(report.candidate_count, 1)
        self.assertEqual(report.candidates[0].ts_code, "600000.SH")
        self.assertEqual(report.price_mode, "qfq")
        self.assertEqual(report.adjustment_coverage, 100.0)

    def test_watch_backfills_missing_price_cache(self):
        source = {
            "price_mode": "qfq",
            "start_trade_date": "20260701",
            "end_trade_date": "20260710",
            "candidates": [
                {
                    "rank": 1,
                    "ts_code": "600000.SH",
                    "name": "Alpha",
                    "industry": "软件服务",
                    "market": "主板",
                    "pct_change": 120,
                    "pullback_from_high": -5,
                }
            ],
        }
        client = FakeTushareClient(
            {
                "daily_basic": [
                    {
                        "ts_code": "600000.SH",
                        "turnover_rate_f": 5,
                        "volume_ratio": 1,
                        "pe_ttm": 20,
                        "pb": 2,
                        "total_mv": 100000,
                        "circ_mv": 50000,
                    }
                ],
                "daily": lambda params: [
                    {"ts_code": "600000.SH", "trade_date": "20260709", "close": 10},
                    {"ts_code": "600000.SH", "trade_date": "20260710", "close": 20},
                ],
                "adj_factor": lambda params: [
                    {"ts_code": "600000.SH", "trade_date": "20260709", "adj_factor": 1},
                    {"ts_code": "600000.SH", "trade_date": "20260710", "adj_factor": 1},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run.build_watch_report(
                source,
                client=client,
                source_report=Path(tmp) / "source.json",
                cache_dir=Path(tmp) / "cache",
            )

        self.assertEqual(report.price_mode, "qfq")
        self.assertEqual(report.scoring_version, run.SCORING_VERSION)
        self.assertEqual(report.candidates[0].recent_20d_pct, 100.0)
        self.assertNotIn("近半年价格数据缺失", report.candidates[0].risk_flags)
        self.assertTrue(
            any(call[0] == "daily" and "ts_code" in call[1] for call in client.calls)
        )

    def test_feishu_card_contains_recap_summary_and_quality_warning(self):
        card = run.build_feishu_card(
            {
                "start_trade_date": "20260101",
                "end_trade_date": "20260710",
                "price_mode": "qfq",
                "min_trading_days": 80,
                "stock_count": 5000,
                "candidate_count": 12,
                "data_warnings": ["复权覆盖率不足"],
                "candidates": [
                    {
                        "rank": 1,
                        "name": "Alpha",
                        "ts_code": "600000.SH",
                        "pct_change": 120,
                        "industry": "软件服务",
                    }
                ],
            },
            {
                "watch_count": 5,
                "core_count": 1,
                "scoring_version": run.SCORING_VERSION,
                "data_warnings": [],
                "candidates": [
                    {
                        "rank": 1,
                        "name": "Alpha",
                        "ts_code": "600000.SH",
                        "tier": "A 核心跟踪",
                        "score": 101,
                        "industry": "软件服务",
                    }
                ],
            },
        )

        self.assertEqual(card["msg_type"], "interactive")
        self.assertEqual(
            card["card"]["header"]["title"]["content"], "过去半年潜力股复盘"
        )
        content = json.dumps(card, ensure_ascii=False)
        self.assertIn("复权覆盖率不足", content)
        self.assertIn("Alpha", content)

    def test_feishu_card_prioritizes_conclusion_and_research_candidates(self):
        card = run.build_feishu_card(
            {
                "candidate_count": 151,
                "candidates": [
                    {
                        "name": "回放甲",
                        "ts_code": "600000.SH",
                        "pct_change": 480,
                    }
                ],
            },
            {
                "watch_count": 80,
                "core_count": 0,
                "market_regime": "偏弱",
                "candidates": [
                    {
                        "rank": 1,
                        "name": "优先乙",
                        "ts_code": "688001.SH",
                        "tier": "B 重点观察",
                        "archetype": "趋势龙头回踩",
                        "industry": "半导体",
                        "quality_score": 21,
                        "setup_score": 25,
                        "rise_drivers": ["行业共振", "成交活跃度抬升"],
                        "financial_evidence": ["20260331净利润同比 +35.2%"],
                        "first_rejection": "第一拒绝点：离高点过近，等待回踩",
                    },
                    {
                        "rank": 2,
                        "name": "次级丙",
                        "ts_code": "000001.SZ",
                        "tier": "C 观察名单",
                    },
                ],
            },
        )

        content = json.dumps(card, ensure_ascii=False)

        self.assertEqual(card["card"]["header"]["template"], "orange")
        self.assertIn("本期结论｜市场偏弱｜暂无 A 级核心", content)
        self.assertIn("本期优先研究", content)
        self.assertIn("优先乙", content)
        self.assertIn("暂缓条件", content)
        self.assertIn("半年强势回放", content)
        self.assertNotIn("为什么现在", content)
        self.assertNotIn("第一拒绝点", content)
        self.assertNotIn("次级丙", content)

    def test_fundamental_evidence_is_rendered_from_latest_indicator_row(self):
        client = FakeTushareClient(
            {
                "fina_indicator": [
                    {
                        "end_date": "20260331",
                        "ann_date": "20260430",
                        "netprofit_yoy": 35.2,
                        "op_yoy": 28.1,
                        "roe": 12.4,
                        "grossprofit_margin": 31.5,
                    }
                ]
            }
        )

        evidence = run.load_fundamental_evidence(client, "600000.SH")

        self.assertIn("20260331净利润同比 +35.2%", evidence)
        self.assertIn("营业利润同比 +28.1%", evidence)
        self.assertIn("ROE 12.4%", evidence)

    def test_fundamental_evidence_respects_report_cutoff_date(self):
        client = FakeTushareClient(
            {
                "fina_indicator": [
                    {
                        "end_date": "20260630",
                        "ann_date": "20260720",
                        "netprofit_yoy": 99.0,
                    },
                    {
                        "end_date": "20260331",
                        "ann_date": "20260430",
                        "netprofit_yoy": 12.0,
                    },
                ]
            }
        )

        evidence = run.load_fundamental_evidence(
            client, "600000.SH", as_of_date="20260710"
        )

        self.assertIn("20260331净利润同比 +12.0%", evidence)
        self.assertNotIn("+99.0%", "；".join(evidence))

    def test_fetched_fundamental_evidence_replaces_pending_marker(self):
        source = {
            "price_mode": "raw",
            "start_trade_date": "20260709",
            "end_trade_date": "20260710",
            "candidates": [
                {
                    "rank": 1,
                    "ts_code": "600000.SH",
                    "name": "Alpha",
                    "industry": "软件服务",
                    "market": "主板",
                    "pct_change": 120,
                    "pullback_from_high": -5,
                }
            ],
        }
        client = FakeTushareClient(
            {
                "daily_basic": [
                    {
                        "ts_code": "600000.SH",
                        "turnover_rate_f": 5,
                        "volume_ratio": 1,
                        "pe_ttm": 20,
                        "pb": 2,
                        "total_mv": 100000,
                        "circ_mv": 50000,
                    }
                ],
                "daily": [
                    {"ts_code": "600000.SH", "trade_date": "20260709", "close": 10},
                    {"ts_code": "600000.SH", "trade_date": "20260710", "close": 20},
                ],
                "fina_indicator": [
                    {
                        "end_date": "20260331",
                        "ann_date": "20260430",
                        "netprofit_yoy": 35.2,
                    }
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run.build_watch_report(
                source,
                client=client,
                source_report=Path(tmp) / "source.json",
                cache_dir=Path(tmp) / "cache",
            )

        candidate = report.candidates[0]
        self.assertIn("20260331净利润同比 +35.2%", candidate.financial_evidence)
        self.assertGreater(candidate.quality_score, 0)
        self.assertIn(candidate.quality_status, {"质量中性", "质量较强"})
        self.assertGreaterEqual(candidate.setup_score, 0)
        self.assertNotIn(run.FUNDAMENTAL_PENDING_DRIVER, candidate.unverified_drivers)
        self.assertNotIn(run.FUNDAMENTAL_PENDING_DRIVER, candidate.thesis)


if __name__ == "__main__":
    unittest.main()
