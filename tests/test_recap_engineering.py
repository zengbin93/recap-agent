import base64
import hashlib
import hmac
import importlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def import_required(module_name):
    module_path = ROOT / (module_name.replace(".", "/") + ".py")
    package_path = ROOT / module_name.replace(".", "/") / "__init__.py"
    if not module_path.exists() and not package_path.exists():
        raise AssertionError(f"Expected module {module_name} to exist")
    return importlib.import_module(module_name)


class FlakyGateway:
    def __init__(self):
        self.calls = 0

    def query(self, table, params):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary tushare outage")
        return [{"ts_code": "000001.SZ", "close": 10.5, "table": table, "params": params}]


class FailingGateway:
    def query(self, table, params):
        raise RuntimeError("tushare unavailable")


class RecapEngineeringTests(unittest.TestCase):
    def test_tushare_collector_retries_and_persists_cache(self):
        data = import_required("recap_agent.data")

        with tempfile.TemporaryDirectory() as tmpdir:
            collector = data.TushareDataCollector(
                gateway=FlakyGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=pathlib.Path(tmpdir) / "fallback",
                sleep=lambda _seconds: None,
            )

            first = collector.fetch_table("daily", {"trade_date": "20260705"}, retries=2)
            second = collector.fetch_table("daily", {"trade_date": "20260705"}, retries=2)

        self.assertEqual(first.rows[0]["ts_code"], "000001.SZ")
        self.assertEqual(first.source, "tushare")
        self.assertEqual(second.rows, first.rows)
        self.assertEqual(second.source, "cache")
        self.assertEqual(collector.gateway.calls, 2)

    def test_tushare_collector_uses_fallback_when_gateway_and_cache_miss(self):
        data = import_required("recap_agent.data")

        with tempfile.TemporaryDirectory() as tmpdir:
            fallback_dir = pathlib.Path(tmpdir) / "fallback"
            fallback_dir.mkdir(parents=True)
            fallback_name = data.cache_file_name("moneyflow", {"trade_date": "20260705"})
            fallback_rows = [{"sector": "AI", "net_mf_amount": 1234}]
            (fallback_dir / fallback_name).write_text(json.dumps(fallback_rows), encoding="utf-8")

            collector = data.TushareDataCollector(
                gateway=FailingGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=fallback_dir,
                sleep=lambda _seconds: None,
            )

            result = collector.fetch_table("moneyflow", {"trade_date": "20260705"}, retries=1)

        self.assertEqual(result.rows, fallback_rows)
        self.assertEqual(result.source, "fallback")
        self.assertIn("tushare unavailable", result.warning)

    def test_feishu_config_resolves_task_specific_webhook_and_signed_payload(self):
        feishu = import_required("recap_agent.feishu")
        env = {
            "FEISHU_WEBHOOK_URL": "https://example.invalid/default",
            "FEISHU_WEBHOOK_SECRET": "default-secret",
            "FEISHU_DAILY_WEBHOOK_URL": "https://example.invalid/daily",
            "FEISHU_DAILY_WEBHOOK_SECRET": "daily-secret",
        }

        config = feishu.FeishuConfig.from_env(env)
        target = config.resolve("daily")
        payload = feishu.build_signed_payload({"msg_type": "interactive"}, "daily-secret", timestamp=1700000000)
        expected_sign = base64.b64encode(
            hmac.new(b"1700000000\ndaily-secret", b"", digestmod=hashlib.sha256).digest()
        ).decode("utf-8")

        self.assertEqual(target.url, "https://example.invalid/daily")
        self.assertEqual(target.secret, "daily-secret")
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["sign"], expected_sign)

    def test_feishu_sender_supports_dry_run_and_real_post_result(self):
        feishu = import_required("recap_agent.feishu")
        sender = feishu.FeishuSender(dry_run=True)
        target = feishu.FeishuTarget(url="https://example.invalid/hook", secret=None)

        dry = sender.send(target, {"msg_type": "interactive"})
        self.assertTrue(dry.dry_run)
        self.assertEqual(dry.status_code, 0)

        with mock.patch("recap_agent.feishu.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"StatusCode":0}'
            real = feishu.FeishuSender(dry_run=False).send(target, {"msg_type": "interactive"})

        self.assertFalse(real.dry_run)
        self.assertEqual(real.status_code, 200)
        self.assertIn("StatusCode", real.body)

    def test_report_renderer_outputs_html_and_feishu_card(self):
        reports = import_required("recap_agent.reports")
        result = reports.render_recap_report(
            task="daily",
            title="全球市场日报",
            datasets={
                "hot_sectors": [{"name": "AI", "change_pct": 2.3}],
                "indices": [{"name": "沪深300", "close": 3500}],
            },
            generated_at="2026-07-05T08:00:00+08:00",
        )

        self.assertIn("<!doctype html>", result.html.lower())
        self.assertIn("全球市场日报", result.html)
        self.assertIn("AI", result.html)
        self.assertEqual(result.card["msg_type"], "interactive")
        self.assertIn("全球市场日报", json.dumps(result.card, ensure_ascii=False))

    def test_daily_workflow_has_beijing_8am_cron_manual_dry_run_and_artifacts(self):
        workflow = ROOT / ".github" / "workflows" / "daily-recap.yml"
        if not workflow.exists():
            self.fail("Expected .github/workflows/daily-recap.yml to exist")

        text = workflow.read_text(encoding="utf-8")

        self.assertIn("0 0 * * *", text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("dry_run", text)
        self.assertIn("ANTHROPIC_API_KEY", text)
        self.assertIn("TUSHARE_TOKEN", text)
        self.assertIn("FEISHU_WEBHOOK", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("if: always()", text)
        self.assertIn("claude", text.lower())

    def test_task_skill_wrappers_forward_cli_arguments(self):
        for task in ("daily", "weekly", "monthly"):
            script = ROOT / "skills" / f"recap-{task}" / "scripts" / "run.py"
            proc = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=ROOT,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertIn("--dry-run", proc.stdout)
            self.assertIn("--output-dir", proc.stdout)


if __name__ == "__main__":
    unittest.main()
