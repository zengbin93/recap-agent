"""数据缓存测试：空结果不缓存，脏/截断缓存视为未命中。"""

import json
import tempfile
import unittest
from pathlib import Path

from recap_agent.data.cache import Cache


class CacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Cache(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _data_file(self):
        files = list(Path(self._tmp.name).rglob("*.json"))
        self.assertEqual(len(files), 1, "expected exactly one cache data file")
        return files[0]

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("daily", trade_date="20240101"))

    def test_put_then_get_hit(self):
        rows = [{"ts_code": "000001.SZ", "close": 10.0}]
        self.cache.put("daily", rows, trade_date="20240101")
        self.assertEqual(self.cache.get("daily", trade_date="20240101"), rows)

    def test_empty_rows_not_cached(self):
        # 空结果不应落盘，get 仍为未命中（避免缓存"脏/空"数据）
        self.cache.put("daily", [], trade_date="20240101")
        self.assertIsNone(self.cache.get("daily", trade_date="20240101"))
        self.assertEqual(list(Path(self._tmp.name).rglob("*.json")), [])

    def test_corrupt_cache_treated_as_miss(self):
        self.cache.put("daily", [{"x": 1}], trade_date="20240101")
        self._data_file().write_text("{broken json", encoding="utf-8")
        self.assertIsNone(self.cache.get("daily", trade_date="20240101"))

    def test_count_mismatch_treated_as_miss(self):
        # 截断/被改写过的缓存：写入的 count 与实际行数不符 → 视为未命中
        self.cache.put("daily", [{"x": 1}], trade_date="20240101")
        self._data_file().write_text(
            json.dumps({"count": 5, "rows": [{"x": 1}]}), encoding="utf-8"
        )
        self.assertIsNone(self.cache.get("daily", trade_date="20240101"))

    def test_different_params_do_not_collide(self):
        self.cache.put("daily", [{"close": 1}], trade_date="20240101")
        self.cache.put("daily", [{"close": 2}], trade_date="20240102")
        self.assertEqual(self.cache.get("daily", trade_date="20240101"), [{"close": 1}])
        self.assertEqual(self.cache.get("daily", trade_date="20240102"), [{"close": 2}])

    def test_different_datasets_do_not_collide(self):
        self.cache.put("daily", [{"a": 1}], trade_date="20240101")
        self.cache.put("adj_factor", [{"b": 2}], trade_date="20240101")
        self.assertEqual(self.cache.get("daily", trade_date="20240101"), [{"a": 1}])
        self.assertEqual(self.cache.get("adj_factor", trade_date="20240101"), [{"b": 2}])


if __name__ == "__main__":
    unittest.main()
