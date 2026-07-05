"""飞书 webhook URL 拼接、签名与发送的测试（不触网，opener 注入）。"""

import json
import unittest

from recap_agent.feishu import send


class WebhookUrlTest(unittest.TestCase):
    def test_key_appended_to_prefix(self):
        self.assertEqual(
            send.webhook_url("abc-123"),
            "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123",
        )

    def test_full_url_passthrough(self):
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123"
        self.assertEqual(send.webhook_url(url), url)


class SignTest(unittest.TestCase):
    def test_known_vector(self):
        # timestamp=1700000000, secret="abc"，按飞书自定义机器人官方签名算法独立算出
        self.assertEqual(
            send.sign("abc", 1700000000),
            "VIS10b0EBvzzSdFnuk4tznEmK5wHaruvf/WnViv2yR4=",
        )

    def test_sign_deterministic(self):
        self.assertEqual(send.sign("s", 1), send.sign("s", 1))


class SendCardTest(unittest.TestCase):
    def _fake_opener(self, status=200, body=None):
        captured = {}

        class Resp:
            def __init__(self):
                self.status = status

            def read(self):
                return json.dumps(body if body is not None else {"StatusCode": 0}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            captured["method"] = req.get_method()
            captured["content_type"] = req.get_header("Content-type")
            return Resp()

        return fake_urlopen, captured

    def test_send_without_sign(self):
        fake, captured = self._fake_opener()
        card = {"header": {"title": "daily"}}
        result = send.send_card("abc", card, opener=fake)
        self.assertEqual(captured["url"], send.webhook_url("abc"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["content_type"], "application/json")
        self.assertEqual(captured["data"]["msg_type"], "interactive")
        self.assertEqual(captured["data"]["card"], card)
        self.assertNotIn("sign", captured["data"])
        self.assertNotIn("timestamp", captured["data"])
        self.assertTrue(result["ok"])

    def test_send_with_sign(self):
        fake, captured = self._fake_opener()
        send.send_card(
            "abc",
            {"x": 1},
            sign_secret="abc",
            timestamp=1700000000,
            opener=fake,
        )
        body = captured["data"]
        self.assertEqual(body["timestamp"], "1700000000")
        self.assertEqual(body["sign"], "VIS10b0EBvzzSdFnuk4tznEmK5wHaruvf/WnViv2yR4=")

    def test_send_non_200_returns_not_ok(self):
        fake, _ = self._fake_opener(status=500, body={"msg": "err"})
        result = send.send_card("abc", {}, opener=fake)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 500)

    def test_send_urlopen_raises_returns_error(self):
        def fake(req, timeout=None):
            raise OSError("boom")

        result = send.send_card("abc", {}, opener=fake)
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


if __name__ == "__main__":
    unittest.main()
