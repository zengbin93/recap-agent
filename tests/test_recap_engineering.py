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
        return [
            {"ts_code": "000001.SZ", "close": 10.5, "table": table, "params": params}
        ]


class FailingGateway:
    def query(self, table, params):
        raise RuntimeError("tushare unavailable")


class CalendarGateway:
    def query(self, table, params):
        if table == "trade_cal":
            return [
                {"cal_date": "20260708", "is_open": "1"},
                {"cal_date": "20260709", "is_open": "1"},
                {"cal_date": "20260710", "is_open": "1"},
            ]
        return []


class RecapGateway:
    def query(self, table, params):
        if table == "trade_cal":
            return [{"cal_date": "20260710", "is_open": "1"}]
        if table == "index_daily":
            return [
                {"ts_code": "000001.SH", "name": "上证指数", "pct_chg": 1.2},
                {"ts_code": "999999.SZ", "name": "其他指数", "pct_chg": 3.0},
            ]
        if table == "moneyflow_ind_ths":
            return [{"ts_code": "885001.TI", "name": "人工智能", "pct_change": 2.5}]
        if table == "daily":
            return [
                {"ts_code": "000001.SZ", "pct_chg": 1.2},
                {"ts_code": "000002.SZ", "pct_chg": -0.5},
                {"ts_code": "000003.SZ", "pct_chg": 0.0},
            ]
        return [{"name": table, "pct_chg": -0.5}]


class RecapEngineeringTests(unittest.TestCase):
    def test_tushare_gateway_uses_configured_url(self):
        data = import_required("recap_agent.data")
        pro = mock.Mock()
        pro.query.return_value = []
        tushare = mock.Mock()
        tushare.pro_api.return_value = pro

        with mock.patch.dict(sys.modules, {"tushare": tushare}):
            rows = data.TushareGateway(token="token", url="https://tushare.example/api").query("daily", {})

        self.assertEqual(rows, [])
        self.assertEqual(pro._DataApi__http_url, "https://tushare.example/api")

    def test_tushare_collector_retries_and_persists_cache(self):
        data = import_required("recap_agent.data")

        with tempfile.TemporaryDirectory() as tmpdir:
            collector = data.TushareDataCollector(
                gateway=FlakyGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=pathlib.Path(tmpdir) / "fallback",
                sleep=lambda _seconds: None,
            )

            first = collector.fetch_table(
                "daily", {"trade_date": "20260705"}, retries=2
            )
            second = collector.fetch_table(
                "daily", {"trade_date": "20260705"}, retries=2
            )

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
            fallback_name = data.cache_file_name(
                "moneyflow", {"trade_date": "20260705"}
            )
            fallback_rows = [{"sector": "AI", "net_mf_amount": 1234}]
            (fallback_dir / fallback_name).write_text(
                json.dumps(fallback_rows), encoding="utf-8"
            )

            collector = data.TushareDataCollector(
                gateway=FailingGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=fallback_dir,
                sleep=lambda _seconds: None,
            )

            result = collector.fetch_table(
                "moneyflow", {"trade_date": "20260705"}, retries=1
            )

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
        self.assertEqual(config.resolve("potential"), target)
        payload = feishu.build_signed_payload(
            {"msg_type": "interactive"}, "daily-secret", timestamp=1700000000
        )
        expected_sign = base64.b64encode(
            hmac.new(
                b"1700000000\ndaily-secret", b"", digestmod=hashlib.sha256
            ).digest()
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
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"StatusCode":0}'
            )
            real = feishu.FeishuSender(dry_run=False).send(
                target, {"msg_type": "interactive"}
            )

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
                "a_share_daily": [
                    {"ts_code": "000001.SZ", "pct_chg": 1.2},
                    {"ts_code": "000002.SZ", "pct_chg": -0.5},
                    {"ts_code": "000003.SZ", "pct_chg": 0.0},
                ],
            },
            generated_at="2026-07-05T08:00:00+08:00",
            period={"task": "daily", "start_date": "20260705", "end_date": "20260705"},
            sources={"hot_sectors": {"source": "fixture", "warning": None}},
        )

        self.assertIn("<!doctype html>", result.html.lower())
        self.assertIn("全球市场日报", result.html)
        self.assertIn("AI", result.html)
        self.assertEqual(result.snapshot["summary"]["trend"], "分化")
        self.assertEqual(result.snapshot["summary"]["stock_count"], 3)
        self.assertEqual(
            result.snapshot["datasets"]["hot_sectors"]["source"], "fixture"
        )
        self.assertEqual(result.card["msg_type"], "interactive")
        self.assertIn("全球市场日报", json.dumps(result.card, ensure_ascii=False))

    def test_market_period_bounds_requests_and_latest_trade_date(self):
        data = import_required("recap_agent.data")

        weekly = data.build_market_period("weekly", "20260710")
        monthly = data.build_market_period("monthly", "20260710")
        self.assertEqual((weekly.start_date, weekly.end_date), ("20260706", "20260710"))
        self.assertEqual(
            (monthly.start_date, monthly.end_date), ("20260701", "20260710")
        )
        self.assertEqual(
            data.default_market_requests("weekly", "20260710")["indices"][1],
            {"start_date": "20260706", "end_date": "20260710"},
        )
        self.assertEqual(
            data.default_market_requests("daily", "20260710")["indices"][1],
            {"trade_date": "20260710"},
        )
        self.assertIn(
            "a_share_daily", data.default_market_requests("daily", "20260710")
        )
        self.assertEqual(
            len(
                data.filter_recap_dataset(
                    "indices",
                    [
                        {"ts_code": "000300.SH"},
                        {"ts_code": "999999.SZ"},
                    ],
                )
            ),
            1,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            collector = data.TushareDataCollector(
                gateway=CalendarGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=pathlib.Path(tmpdir) / "fallback",
                sleep=lambda _seconds: None,
            )
            self.assertEqual(
                data.resolve_latest_trade_date(collector, "20260710"), "20260710"
            )
            self.assertEqual(
                data.resolve_latest_trade_date(collector, "20260709"), "20260709"
            )

    def test_report_files_include_structured_snapshot(self):
        reports = import_required("recap_agent.reports")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = reports.render_recap_report(
                task="daily",
                title="全球市场日报",
                datasets={
                    "indices": [{"name": "指数", "pct_chg": -1.2}],
                    "a_share_daily": [{"ts_code": "000001.SZ", "pct_chg": -1.2}],
                },
                generated_at="2026-07-10T08:00:00+08:00",
                period={
                    "task": "daily",
                    "start_date": "20260710",
                    "end_date": "20260710",
                },
                sources={
                    "indices": {"source": "cache", "warning": "stale"},
                    "a_share_daily": {"source": "tushare", "warning": None},
                },
            )
            files = reports.write_report_files(result, tmpdir, "daily")
            snapshot = json.loads(
                pathlib.Path(files["snapshot"]).read_text(encoding="utf-8")
            )

        self.assertEqual(snapshot["summary"]["trend"], "偏弱")
        self.assertEqual(snapshot["datasets"]["indices"]["warning"], "stale")
        self.assertIn("snapshot", files)

    def test_cli_task_resolves_period_and_writes_snapshot(self):
        cli = import_required("recap_agent.cli")
        data = import_required("recap_agent.data")
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = data.TushareDataCollector(
                gateway=RecapGateway(),
                cache_dir=pathlib.Path(tmpdir) / "cache",
                fallback_dir=pathlib.Path(tmpdir) / "fallback",
                sleep=lambda _seconds: None,
            )
            with mock.patch.object(cli, "TushareDataCollector", return_value=collector):
                result = cli.run_task(
                    "daily", tmpdir, dry_run=True, trade_date="20260710"
                )

            snapshot_path = pathlib.Path(result["files"]["snapshot"])
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot_exists = snapshot_path.exists()

        self.assertEqual(
            result["period"],
            {"task": "daily", "start_date": "20260710", "end_date": "20260710"},
        )
        self.assertEqual(snapshot["summary"]["trend"], "分化")
        self.assertEqual(snapshot["summary"]["stock_count"], 3)
        self.assertEqual(snapshot["datasets"]["indices"]["rows"], 1)
        self.assertEqual(snapshot["datasets"]["indices"]["raw_rows"], 2)
        self.assertTrue(snapshot_exists)

    def test_daily_workflow_has_beijing_8am_cron_manual_dry_run_and_artifacts(self):
        workflow = ROOT / ".github" / "workflows" / "daily-recap.yml"
        if not workflow.exists():
            self.fail("Expected .github/workflows/daily-recap.yml to exist")

        text = workflow.read_text(encoding="utf-8")

        self.assertIn("0 11 * * 1-5", text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("dry_run", text)
        self.assertIn("ANTHROPIC_API_KEY", text)
        self.assertIn("TUSHARE_TOKEN", text)
        self.assertIn("TUSHARE_URL", text)
        self.assertIn("FEISHU_WEBHOOK", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("if: always()", text)
        self.assertIn("claude", text.lower())
        self.assertIn("potential", text)
        self.assertIn("scripts/run_recap.py --task potential", text)

    def test_potential_stock_workflow_dispatches_current_recap_chain(self):
        workflow = ROOT / ".github" / "workflows" / "potential-stock-recap.yml"
        if not workflow.exists():
            self.fail("Expected .github/workflows/potential-stock-recap.yml to exist")

        text = workflow.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch", text)
        self.assertIn("TUSHARE_TOKEN", text)
        self.assertIn("--task potential", text)
        self.assertIn("min-trading-days", text)
        self.assertIn("FEISHU_POTENTIAL_WEBHOOK_URL", text)
        self.assertIn("actions/upload-artifact", text)

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

    def test_tushare_recap_reports_skill_wraps_pr_topics(self):
        skill_dir = ROOT / "skills" / "tushare-recap-reports"
        script = skill_dir / "scripts" / "run.py"

        self.assertTrue((skill_dir / "SKILL.md").exists())
        self.assertTrue(script.exists())
        self.assertFalse((ROOT / "scripts" / "run_first_double.py").exists())
        self.assertFalse((ROOT / "scripts" / "run_tenbagger_watch.py").exists())

        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertIn("first-double", proc.stdout)
        self.assertIn("tenbagger-watch", proc.stdout)
        self.assertIn("full-chain", proc.stdout)

        first_double = subprocess.run(
            [sys.executable, str(script), "first-double", "--help"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIn("--lookback-days", first_double.stdout)
        self.assertIn("--min-pct-change", first_double.stdout)
        self.assertIn("--price-mode", first_double.stdout)
        self.assertIn("--min-trading-days", first_double.stdout)

        watch = subprocess.run(
            [sys.executable, str(script), "tenbagger-watch", "--help"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIn("--source-report", watch.stdout)
        self.assertIn("--limit", watch.stdout)


if __name__ == "__main__":
    unittest.main()
