import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "recap-hk-weekly" / "scripts" / "run.py"
SPEC = importlib.util.spec_from_file_location("recap_hk_weekly_run", SCRIPT)
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


class HongKongWeeklyRecapTests(unittest.TestCase):
    def setUp(self):
        self.dates = ["20260713", "20260714", "20260715", "20260716", "20260717"]
        closes = {
            "20260710": {"00700.HK": 100, "09988.HK": 100, "00001.HK": 100},
            "20260713": {"00700.HK": 104, "09988.HK": 102, "00001.HK": 101},
            "20260714": {"00700.HK": 106, "09988.HK": 103, "00001.HK": 99},
            "20260715": {"00700.HK": 108, "09988.HK": 104, "00001.HK": 98},
            "20260716": {"00700.HK": 109, "09988.HK": 105, "00001.HK": 99},
            "20260717": {"00700.HK": 110, "09988.HK": 106, "00001.HK": 101},
        }

        def hk_daily(params):
            trade_date = params["trade_date"]
            prior_date = (
                "20260710"
                if trade_date in {"20260710", "20260713"}
                else self.dates[self.dates.index(trade_date) - 1]
            )
            return [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "close": close,
                    "pct_change": (close / closes[prior_date][code] - 1) * 100,
                    "amount": {"00700.HK": 100, "09988.HK": 80, "00001.HK": 2}[code],
                }
                for code, close in closes[trade_date].items()
            ]

        self.client = FakeTushareClient(
            {
                "hk_tradecal": [
                    {"cal_date": item, "is_open": 1}
                    for item in ["20260710", *self.dates]
                ],
                "hk_basic": [
                    {"ts_code": "00700.HK", "name": "腾讯控股", "industry": "互联网", "market": "主板", "list_date": "20040616"},
                    {"ts_code": "09988.HK", "name": "阿里巴巴", "industry": "互联网", "market": "主板", "list_date": "20191126"},
                    {"ts_code": "00001.HK", "name": "长和", "industry": "综合企业", "market": "主板", "list_date": "19720918"},
                ],
                "hk_daily_adj": hk_daily,
                "index_global": lambda params: [
                    {"ts_code": params["ts_code"], "trade_date": "20260710", "close": 100},
                    {"ts_code": params["ts_code"], "trade_date": "20260717", "close": 104},
                ],
                "ggt_daily": [
                    {"trade_date": "20260716", "buy_amount": 100, "sell_amount": 80},
                    {"trade_date": "20260717", "buy_amount": 120, "sell_amount": 90},
                ],
                "ggt_top10": [
                    {"ts_code": "00700.HK", "name": "腾讯控股", "amount": 50, "net_amount": 10},
                    {"ts_code": "09988.HK", "name": "阿里巴巴", "amount": 40, "net_amount": 6},
                ],
            }
        )

    def test_uses_hong_kong_calendar_and_filters_illiquid_strength(self):
        report = run.build_hk_weekly_report(self.client, date(2026, 7, 17))

        self.assertEqual(report.period.start_trade_date, "20260713")
        self.assertEqual(report.period.prior_trade_date, "20260710")
        self.assertEqual(report.breadth["sample_count"], 3)
        self.assertEqual(report.breadth["up_count"], 3)
        self.assertEqual(report.southbound["net_buy_yi"], 50)
        self.assertEqual(report.candidates[0]["ts_code"], "00700.HK")
        self.assertNotIn("00001.HK", [item["ts_code"] for item in report.candidates])
        self.assertIn("驱动待核验", report.candidates[0]["risk"])
        self.assertTrue(any(call[0] == "hk_tradecal" for call in self.client.calls))
        self.assertTrue(any(call[0] == "hk_daily_adj" for call in self.client.calls))

    def test_writes_readable_report_snapshot_card_and_csv(self):
        report = run.build_hk_weekly_report(self.client, date(2026, 7, 17))
        with tempfile.TemporaryDirectory() as tmp:
            files = run.write_report_files(report, Path(tmp))
            card = json.loads(Path(files["card"]).read_text(encoding="utf-8"))
            html = Path(files["html"]).read_text(encoding="utf-8")
            snapshot = json.loads(Path(files["snapshot"]).read_text(encoding="utf-8"))

        self.assertEqual(card["msg_type"], "interactive")
        self.assertEqual(card["card"]["header"]["title"]["content"], "港股周复盘")
        self.assertIn("### 市场温度", json.dumps(card, ensure_ascii=False))
        self.assertIn("强势研究池", html)
        self.assertIn("不是买入建议", html)
        self.assertEqual(snapshot["period"]["end_trade_date"], "20260717")

    def test_task_specific_hk_weekly_feishu_target_and_workflow(self):
        config = run.FeishuConfig.from_env(
            {
                "FEISHU_WEEKLY_WEBHOOK_URL": "https://example.invalid/weekly",
                "FEISHU_HK_WEEKLY_WEBHOOK_URL": "https://example.invalid/hk-weekly",
            }
        )
        self.assertEqual(config.resolve("hk-weekly").url, "https://example.invalid/hk-weekly")
        workflow = (ROOT / ".github" / "workflows" / "hk-weekly-recap.yml").read_text(encoding="utf-8")
        self.assertIn("30 10 * * 5", workflow)
        self.assertIn("FEISHU_HK_WEEKLY_WEBHOOK_URL", workflow)
        self.assertIn("hk-weekly-recap", workflow)


if __name__ == "__main__":
    unittest.main()
