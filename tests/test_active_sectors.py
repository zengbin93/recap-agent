"""Offline tests for the recap-active-sectors skill.

Everything runs against a fake Tushare client, so no real ``TUSHARE_TOKEN`` or
network access is required.
"""
import importlib.util
import os
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_PY = ROOT / "skills" / "recap-active-sectors" / "scripts" / "run.py"


def load_run_module():
    spec = importlib.util.spec_from_file_location("active_sectors_run", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    # dataclass 解析字符串注解时需要模块已在 sys.modules 中。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = load_run_module()


class FakeClient:
    """Returns canned rows keyed by (api_name, frozenset of params)."""

    def __init__(self, tables):
        self.tables = tables

    def query(self, api_name, *, params=None, fields=None, cache_key=None):
        params = params or {}
        handler = self.tables.get(api_name)
        if handler is None:
            return []
        if callable(handler):
            return handler(params)
        return handler


def make_client():
    daily_top = [
        {"ts_code": f"{i:06d}.SZ", "trade_date": "20260710", "close": 10 + i,
         "pct_chg": 5 - i * 0.1, "amount": (200 - i) * 1000}
        for i in range(6)
    ]
    # 加一只 ST 和一只北交所，验证过滤。
    daily_top.append({"ts_code": "000900.SZ", "trade_date": "20260710", "close": 3,
                      "pct_chg": 1, "amount": 199_500})  # name=ST 华测
    daily_top.append({"ts_code": "830001.BJ", "trade_date": "20260710", "close": 8,
                      "pct_chg": 2, "amount": 199_400})

    basic = {
        **{f"{i:06d}.SZ": {"ts_code": f"{i:06d}.SZ", "name": f"股票{i}", "market": "主板"} for i in range(6)},
        "000900.SZ": {"ts_code": "000900.SZ", "name": "ST华测", "market": "主板"},
        "830001.BJ": {"ts_code": "830001.BJ", "name": "北交X", "market": "北交所"},
    }

    ths_index = {
        "N": [
            {"ts_code": "885001.TI", "name": "人工智能", "count": 50, "type": "N"},
            {"ts_code": "885002.TI", "name": "冷门概念", "count": 10, "type": "N"},
        ],
        "I": [
            {"ts_code": "881001.TI", "name": "半导体", "count": 40, "type": "I"},
        ],
    }

    # 板块成分：人工智能命中 4 只，半导体命中 3 只，冷门 1 只。
    members = {
        "885001.TI": [{"con_code": f"{i:06d}.SZ", "con_name": f"股票{i}"} for i in range(4)],
        "881001.TI": [{"con_code": f"{i:06d}.SZ", "con_name": f"股票{i}"} for i in range(1, 4)],
        "885002.TI": [{"con_code": "000005.SZ", "con_name": "股票5"}],
    }

    def daily_handler(params):
        if params.get("trade_date") == "20260710":
            return daily_top
        # 个股区间行情（近 5 日）
        ts = params.get("ts_code")
        if ts:
            return [
                {"ts_code": ts, "trade_date": "20260704", "close": 100, "pre_close": 90},
                {"ts_code": ts, "trade_date": "20260710", "close": 120, "pre_close": 118},
            ]
        return []

    ths_daily = {
        "885001.TI": [
            {"ts_code": "885001.TI", "trade_date": "20260704", "close": 1000, "pre_close": 980,
             "high": 1010, "low": 970, "pct_change": 1.5},
            {"ts_code": "885001.TI", "trade_date": "20260710", "close": 1100, "pre_close": 1080,
             "high": 1120, "low": 1070, "pct_change": 2.5},
        ],
        "881001.TI": [
            {"ts_code": "881001.TI", "trade_date": "20260704", "close": 500, "pre_close": 500,
             "high": 505, "low": 495, "pct_change": 0.0},
            {"ts_code": "881001.TI", "trade_date": "20260710", "close": 520, "pre_close": 515,
             "high": 525, "low": 510, "pct_change": 1.0},
        ],
    }

    trade_cal = [{"cal_date": d, "is_open": "1"} for d in ("20260704", "20260710")]

    tables = {
        "trade_cal": trade_cal,
        "stock_basic": list(basic.values()),
        "daily": daily_handler,
        "ths_index": lambda p: ths_index.get(p.get("type"), []),
        "ths_member": lambda p: members.get(p.get("ts_code"), []),
        "ths_daily": lambda p: ths_daily.get(p.get("ts_code"), []),
    }
    return FakeClient(tables)


class ActiveSectorsTests(unittest.TestCase):
    def test_tushare_client_uses_url_from_environment(self):
        with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "token", "TUSHARE_URL": "https://tushare.example/api"}):
            client = run.TushareClient(use_cache=False)
        with mock.patch.object(run, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"code":0,"data":{"fields":[],"items":[]}}'
            client.query("daily")

        self.assertEqual(urlopen.call_args.args[0].full_url, "https://tushare.example/api")

    def test_top_amount_filters_st_and_bj(self):
        client = make_client()
        basic = run.load_stock_basic(client)
        tops = run.load_top_amount_stocks(
            client, "20260710", top_n=100, exclude_st=True, exclude_bj=True, basic=basic
        )
        codes = [t.ts_code for t in tops]
        self.assertNotIn("000900.SZ", codes)  # ST 被剔除
        self.assertNotIn("830001.BJ", codes)  # 北交所被剔除
        self.assertEqual(tops[0].rank, 1)
        # amount 千元 -> 亿元
        self.assertAlmostEqual(tops[0].amount_yi, 2.0, places=2)

    def test_membership_and_aggregation(self):
        client = make_client()
        basic = run.load_stock_basic(client)
        tops = run.load_top_amount_stocks(
            client, "20260710", top_n=100, exclude_st=True, exclude_bj=False, basic=basic
        )
        index = run.load_sector_index(client, ["N", "I"])
        membership = run.build_membership(client, index, trade_date="20260710", throttle=0.0)
        active = run.aggregate_active_sectors(tops, membership, min_count=3)
        names = [a["name"] for a in active]
        self.assertIn("人工智能", names)  # 命中 4 次
        self.assertIn("半导体", names)  # 命中 3 次
        self.assertNotIn("冷门概念", names)  # 命中 1 次 < 3
        # 排序：命中多的在前
        self.assertEqual(active[0]["name"], "人工智能")

    def test_build_report_end_to_end(self):
        client = make_client()
        report = run.build_report(
            client,
            trade_date="20260710",
            top_n=100,
            min_count=3,
            recent_days=5,
            sector_types=["N", "I"],
            rep_stocks=5,
            throttle=0.0,
        )
        self.assertEqual(report.trade_date, "20260710")
        self.assertEqual(report.active_sector_count, 2)
        self.assertEqual(report.theme_cluster_count, 1)
        self.assertEqual(report.displayed_sector_count, 1)
        ai = report.sectors[0]
        self.assertEqual(ai.name, "人工智能")
        self.assertTrue(ai.quote_available)
        self.assertEqual(ai.sector_size, 4)
        self.assertEqual(ai.coverage_pct, 100.0)
        self.assertEqual(ai.related_sectors, ["半导体"])
        self.assertEqual(ai.today_pct, 2.5)
        # 近 5 日 = (1100 - 980) / 980 * 100
        self.assertAlmostEqual(ai.recent_pct, (1100 - 980) / 980 * 100, places=1)
        self.assertTrue(ai.representatives)
        self.assertTrue(all(r.in_top for r in ai.representatives))

    def test_candidate_count_is_not_replaced_by_display_limit(self):
        client = make_client()
        report = run.build_report(
            client,
            trade_date="20260710",
            top_n=100,
            min_count=3,
            recent_days=5,
            sector_types=["N", "I"],
            rep_stocks=5,
            throttle=0.0,
            max_sectors=1,
        )

        self.assertEqual(report.active_sector_count, 2)
        self.assertEqual(report.theme_cluster_count, 1)
        self.assertEqual(report.displayed_sector_count, 1)
        card = run.build_feishu_card(report, top=10)
        card_text = __import__("json").dumps(card, ensure_ascii=False)
        self.assertIn("候选板块", card_text)
        self.assertIn("去重主题", card_text)

    def test_render_html_and_csv_smoke(self):
        client = make_client()
        report = run.build_report(
            client, trade_date="20260710", top_n=100, min_count=3, recent_days=5,
            sector_types=["N", "I"], rep_stocks=5, throttle=0.0,
        )
        html = run.render_html(report)
        self.assertIn("成交活跃板块复盘", html)
        self.assertIn("人工智能", html)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = pathlib.Path(tmp) / "out.csv"
            run.write_csv_report(report, csv_path)
            text = csv_path.read_text(encoding="utf-8")
            self.assertIn("人工智能", text)
            self.assertIn("hit_count", text)

    def test_broad_sector_filtered(self):
        self.assertTrue(run.is_broad_sector("融资融券"))
        self.assertTrue(run.is_broad_sector("沪深300样本股"))
        self.assertTrue(run.is_broad_sector("深股通"))
        self.assertTrue(run.is_broad_sector("国家大基金持股"))
        self.assertTrue(run.is_broad_sector("同花顺漂亮100"))
        self.assertFalse(run.is_broad_sector("半导体"))
        tables = {
            "ths_index": lambda p: [
                {"ts_code": "1.TI", "name": "融资融券", "type": "N"},
                {"ts_code": "2.TI", "name": "人工智能", "type": "N"},
            ]
        }
        client = FakeClient(tables)
        kept = run.load_sector_index(client, ["N"], exclude_broad=True)
        self.assertEqual([r["name"] for r in kept], ["人工智能"])
        kept_all = run.load_sector_index(client, ["N"], exclude_broad=False)
        self.assertEqual(len(kept_all), 2)

    def test_build_feishu_card(self):
        client = make_client()
        report = run.build_report(
            client, trade_date="20260710", top_n=100, min_count=3, recent_days=5,
            sector_types=["N", "I"], rep_stocks=5, throttle=0.0,
        )
        card = run.build_feishu_card(report, top=10)
        self.assertEqual(card["msg_type"], "interactive")
        self.assertIn("20260710", card["card"]["header"]["title"]["content"])
        import json as _json
        blob = _json.dumps(card, ensure_ascii=False)
        self.assertIn("人工智能", blob)
        self.assertIn("column_set", blob)  # 分栏布局
        self.assertIn("<font color='red'>", blob)  # 涨跌上色（人工智能今日 +2.5%）


if __name__ == "__main__":
    unittest.main()
