"""daily-recap.yml 静态校验：cron、手动输入、必需 secrets、失败隔离、产物上传。"""

import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-recap.yml"


class WorkflowStaticTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.exists(), f"workflow 不存在：{WORKFLOW}")
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_cron_is_utc_midnight(self):
        # UTC 00:00 = 北京 08:00
        self.assertIn('cron: "0 0 * * *"', self.text)

    def test_dispatch_inputs_exist(self):
        for key in ("as_of_date", "task", "dry_run"):
            self.assertIn(key, self.text, f"缺少 workflow_dispatch input: {key}")

    def test_required_secrets_injected(self):
        for sec in ("ANTHROPIC_API_KEY", "TUSHARE_TOKEN", "FEISHU_WEBHOOKS"):
            self.assertIn(f"secrets.{sec}", self.text, f"缺少 secret 注入: {sec}")

    def test_single_task_failure_isolated(self):
        # continue-on-error 让单个复盘任务失败不拖垮整批
        self.assertIn("continue-on-error: true", self.text)

    def test_artifact_upload_always(self):
        self.assertIn("actions/upload-artifact", self.text)
        self.assertIn("if: always()", self.text)

    def test_uses_claude_code_action(self):
        self.assertIn("anthropics/claude-code-action", self.text)


if __name__ == "__main__":
    unittest.main()
