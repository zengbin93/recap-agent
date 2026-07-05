"""FEISHU_WEBHOOKS JSON 解析与 daily/weekly/monthly 回退逻辑的测试。

契约：FEISHU_WEBHOOKS 是一个 JSON 对象
    {"daily": {"key": "...", "sign_secret": "..."}, "weekly": {...}, "monthly": {...}}
- daily 必填且必须有 key；
- weekly / monthly 缺失时，resolve 回退到 daily。
"""

import json
import unittest

from recap_agent.feishu.webhooks import parse_webhooks


class ParseWebhooksTest(unittest.TestCase):
    def test_all_three_configured(self):
        raw = json.dumps({
            "daily": {"key": "d-key", "sign_secret": "d-sec"},
            "weekly": {"key": "w-key"},
            "monthly": {"key": "m-key"},
        })
        wh = parse_webhooks(raw)
        self.assertEqual(wh.resolve("daily").key, "d-key")
        self.assertEqual(wh.resolve("daily").sign_secret, "d-sec")
        self.assertEqual(wh.resolve("weekly").key, "w-key")
        self.assertIsNone(wh.resolve("weekly").sign_secret)
        self.assertEqual(wh.resolve("monthly").key, "m-key")

    def test_daily_missing_raises(self):
        raw = json.dumps({"weekly": {"key": "w"}})
        with self.assertRaises(ValueError) as cm:
            parse_webhooks(raw)
        self.assertIn("daily", str(cm.exception).lower())

    def test_daily_without_key_raises(self):
        raw = json.dumps({"daily": {"sign_secret": "s"}})
        with self.assertRaises(ValueError) as cm:
            parse_webhooks(raw)
        self.assertIn("daily", str(cm.exception).lower())

    def test_weekly_falls_back_to_daily(self):
        raw = json.dumps({"daily": {"key": "d", "sign_secret": "s"}})
        wh = parse_webhooks(raw)
        # weekly 未配置 → 回退 daily（含 sign_secret）
        self.assertEqual(wh.resolve("weekly").key, "d")
        self.assertEqual(wh.resolve("weekly").sign_secret, "s")

    def test_monthly_falls_back_to_daily(self):
        raw = json.dumps({"daily": {"key": "d"}})
        wh = parse_webhooks(raw)
        self.assertEqual(wh.resolve("monthly").key, "d")

    def test_weekly_explicit_does_not_fall_back(self):
        raw = json.dumps({"daily": {"key": "d"}, "weekly": {"key": "w"}})
        wh = parse_webhooks(raw)
        self.assertEqual(wh.resolve("weekly").key, "w")

    def test_weekly_without_key_falls_back_to_daily(self):
        # weekly 显式给出但缺 key，视为未配置，回退 daily
        raw = json.dumps({"daily": {"key": "d"}, "weekly": {}})
        wh = parse_webhooks(raw)
        self.assertEqual(wh.resolve("weekly").key, "d")

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError) as cm:
            parse_webhooks("{not json")
        self.assertIn("json", str(cm.exception).lower())

    def test_empty_raw_raises(self):
        for raw in (None, "", "   ", "\t\n"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_webhooks(raw)

    def test_top_level_not_object_raises(self):
        with self.assertRaises(ValueError):
            parse_webhooks(json.dumps(["daily"]))
        with self.assertRaises(ValueError):
            parse_webhooks(json.dumps("daily"))

    def test_unknown_task_raises(self):
        wh = parse_webhooks(json.dumps({"daily": {"key": "d"}}))
        with self.assertRaises(ValueError):
            wh.resolve("quarterly")

    def test_sign_secret_optional(self):
        wh = parse_webhooks(json.dumps({"daily": {"key": "d"}}))
        self.assertIsNone(wh.resolve("daily").sign_secret)


if __name__ == "__main__":
    unittest.main()
