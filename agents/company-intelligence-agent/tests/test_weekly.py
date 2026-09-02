import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from company_intel.weekly import (
    WeeklyRunError, install_windows_weekly_task, run_weekly_pipeline,
)


class WeeklyPipelineTests(unittest.TestCase):
    def test_runs_all_stages_in_order_and_publishes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            exports = root / "exports"
            exports.mkdir()
            calls = []

            def runner(arguments):
                calls.append(arguments[0])
                if arguments[0] == "report":
                    (exports / "company_report_20260901090000.html").write_text("html")
                    (exports / "company_report_20260901090000.csv").write_text("csv")
                return 0

            run_weekly_pipeline(root / "Dropbox", exports, run_command=runner)

            self.assertEqual(calls, ["scan", "crawl", "extract", "analyze", "report"])
            self.assertTrue((root / "Dropbox" / "latest_report.html").exists())
            self.assertTrue((root / "Dropbox" / "latest_report.csv").exists())

    def test_stops_before_publish_when_a_stage_fails(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            calls = []

            def runner(arguments):
                calls.append(arguments[0])
                return 2 if arguments[0] == "analyze" else 0

            with self.assertRaises(WeeklyRunError):
                run_weekly_pipeline(root / "Dropbox", root / "exports", run_command=runner)
            self.assertEqual(calls, ["scan", "crawl", "extract", "analyze"])
            self.assertFalse((root / "Dropbox").exists())

    def test_installs_weekly_windows_task_and_start_when_available(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []

            def fake_subprocess(command, **kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = install_windows_weekly_task(
                Path(root) / "Dropbox", root, "FRI", "08:30",
                run_subprocess=fake_subprocess, platform_name="nt",
            )

            self.assertIn("WEEKLY", calls[0])
            self.assertIn("FRI", calls[0])
            self.assertIn("08:30", calls[0])
            self.assertIn("-StartWhenAvailable", calls[1][-1])
            self.assertTrue(Path(result["runner"]).exists())
