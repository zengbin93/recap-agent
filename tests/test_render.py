"""HTML 报告与飞书卡片渲染测试。"""

import unittest

from recap_agent.reports import render


class RenderHtmlTest(unittest.TestCase):
    def test_title_escaped(self):
        out = render.render_html("a<b>&c", [])
        self.assertIn("a&lt;b&gt;&amp;c", out)
        self.assertNotIn("a<b>&c", out)

    def test_sections_rendered(self):
        out = render.render_html("T", [{"heading": "H", "bullets": ["x", "y"]}])
        self.assertIn("<h2>H</h2>", out)
        self.assertIn("<li>x</li>", out)
        self.assertIn("<li>y</li>", out)
        self.assertIn("<title>T</title>", out)

    def test_risk_section_gets_class(self):
        out = render.render_html("T", [{"heading": "风险", "bullets": ["z"], "risk": True}])
        self.assertIn('class="risk"', out)


class BuildCardTest(unittest.TestCase):
    def test_card_structure(self):
        card = render.build_card("日报 2024-01-01", "**结论**：上涨")
        self.assertEqual(card["header"]["title"]["content"], "日报 2024-01-01")
        self.assertEqual(card["header"]["template"], "green")
        self.assertEqual(card["elements"][0]["tag"], "div")
        self.assertEqual(card["elements"][0]["text"]["tag"], "lark_md")
        self.assertEqual(card["elements"][0]["text"]["content"], "**结论**：上涨")

    def test_card_header_color(self):
        card = render.build_card("周报", "x", header_color="blue")
        self.assertEqual(card["header"]["template"], "blue")


if __name__ == "__main__":
    unittest.main()
