"""tushare 采集客户端测试：重试 3 次、限流/网络可重试、3 次后跳过降级、不可重试立即抛。

测试通过注入 fake fetcher 模拟 tushare 调用边界，不依赖 tushare/pandas 安装。
"""

import unittest

from recap_agent.data.tushare_client import (
    RateLimitError,
    SkipDataset,
    TushareError,
    fetch_dataset,
)


class _FakeFetcher:
    """按顺序返回预设结果或抛预设异常。"""

    def __init__(self, side_effects):
        self.side_effects = list(side_effects)
        self.calls = []

    def __call__(self, api_name, token, params):
        self.calls.append((api_name, token, dict(params)))
        eff = self.side_effects.pop(0)
        if isinstance(eff, BaseException):
            raise eff
        return eff


class FetchDatasetTest(unittest.TestCase):
    def test_success_first_try(self):
        fetcher = _FakeFetcher([[{"a": 1}]])
        rows = fetch_dataset(
            "daily", "tok", {"trade_date": "20240101"},
            fetcher=fetcher, sleep=lambda s: None,
        )
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(len(fetcher.calls), 1)

    def test_rate_limit_then_success(self):
        fetcher = _FakeFetcher([RateLimitError("lim"), [{"a": 1}]])
        rows = fetch_dataset("daily", "tok", {}, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(len(fetcher.calls), 2)

    def test_network_error_retries(self):
        fetcher = _FakeFetcher([OSError("net"), OSError("net"), [{"a": 1}]])
        rows = fetch_dataset("daily", "tok", {}, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(len(fetcher.calls), 3)

    def test_skip_after_three_rate_limits(self):
        fetcher = _FakeFetcher([RateLimitError("lim")] * 3)
        with self.assertRaises(SkipDataset) as cm:
            fetch_dataset("daily", "tok", {}, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(len(fetcher.calls), 3)
        self.assertIn("daily", str(cm.exception))

    def test_empty_result_is_retried_then_skipped(self):
        fetcher = _FakeFetcher([[], [], []])
        with self.assertRaises(SkipDataset):
            fetch_dataset("daily", "tok", {}, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(len(fetcher.calls), 3)

    def test_non_retryable_error_raised_immediately(self):
        # 认证类错误（TushareError 但非 RateLimitError）不应重试
        fetcher = _FakeFetcher([TushareError("invalid token")])
        with self.assertRaises(TushareError):
            fetch_dataset("daily", "tok", {}, fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(len(fetcher.calls), 1)

    def test_backoff_sleep_called_between_retries(self):
        sleeps = []
        fetcher = _FakeFetcher([RateLimitError("lim"), [{"a": 1}]])
        fetch_dataset(
            "daily", "tok", {},
            fetcher=fetcher, sleep=sleeps.append, backoff_base=2.0,
        )
        self.assertEqual(sleeps, [2.0])

    def test_params_forwarded_to_fetcher(self):
        fetcher = _FakeFetcher([[{"a": 1}]])
        fetch_dataset(
            "daily", "tok", {"trade_date": "20240101", "ts_code": "000001.SZ"},
            fetcher=fetcher, sleep=lambda s: None,
        )
        self.assertEqual(
            fetcher.calls[0][2], {"trade_date": "20240101", "ts_code": "000001.SZ"}
        )


if __name__ == "__main__":
    unittest.main()
