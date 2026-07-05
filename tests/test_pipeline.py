"""复盘报告 pipeline 测试：写 HTML + 卡片，标题按任务区分。"""

import json
import tempfile
import unittest
from pathlib import Path

from recap_agent.reports.pipeline import run_recap, sections_to_markdown


class RunRecapTest(unittest.TestCase):
    def test_writes_html_and_card_for_daily(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_recap(
                "daily", "2024-01-02",
                [{"heading": "核心结论", "bullets": ["大盘上涨"]}],
                output_dir=tmp + "/reports",
                artifacts_dir=tmp + "/artifacts",
            )
            html_p, card_p = Path(out["html"]), Path(out["card"])
            self.assertTrue(html_p.exists())
            self.assertTrue(card_p.exists())
            html = html_p.read_text(encoding="utf-8")
            self.assertIn("日复盘", html)
            self.assertIn("2024-01-02", html)
            self.assertIn("大盘上涨", html)
            card = json.loads(card_p.read_text(encoding="utf-8"))
            self.assertIn("日复盘", card["header"]["title"]["content"])
            self.assertIn("大盘上涨", card["elements"][0]["text"]["content"])

    def test_title_differs_by_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_recap("weekly", "2024-W01", [{"heading": "x", "bullets": []}],
                      output_dir=tmp + "/r", artifacts_dir=tmp + "/a")
            card = json.loads((Path(tmp) / "a/cards/weekly.json").read_text("utf-8"))
            self.assertIn("周复盘", card["header"]["title"]["content"])

    def test_sections_to_markdown(self):
        md = sections_to_markdown([
            {"heading": "结论", "bullets": ["a", "b"]},
            {"heading": "风险", "bullets": ["c"]},
        ])
        self.assertIn("**结论**", md)
        self.assertIn("- a", md)
        self.assertIn("- c", md)


if __name__ == "__main__":
    unittest.main()
