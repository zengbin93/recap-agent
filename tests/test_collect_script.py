"""recap-data-collect script behavior tests."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from recap_agent.data.cache import Cache


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude/skills/recap-data-collect/scripts/collect.py"
)


def load_collect_script():
    spec = importlib.util.spec_from_file_location("collect_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollectScriptTest(unittest.TestCase):
    def test_uses_valid_cache_without_fetching_tushare(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            out_dir = root / "out"
            rows = [{"ts_code": "000001.SZ", "close": 10.0}]
            Cache(cache_dir).put("daily", rows, trade_date="20240101")

            collect = load_collect_script()

            def fail_fetch(*args, **kwargs):
                raise AssertionError("fetch_dataset should not be called on cache hit")

            collect.fetch_dataset = fail_fetch

            code = collect.main([
                "--as-of-date", "20240101",
                "--output-dir", str(out_dir),
                "--datasets", "daily",
                "--token", "tok",
                "--cache-dir", str(cache_dir),
            ])

            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads((out_dir / "daily.json").read_text(encoding="utf-8")),
                rows,
            )
            manifest = json.loads((out_dir / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["datasets"]["daily"]["rows"], 1)
            self.assertEqual(manifest["skipped"], [])


if __name__ == "__main__":
    unittest.main()
