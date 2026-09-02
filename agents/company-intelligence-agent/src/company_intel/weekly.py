from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from .publishing import latest_report_pair, publish_report_pair


class WeeklyRunError(RuntimeError):
    pass


CommandRunner = Callable[[Sequence[str]], int]


def run_weekly_pipeline(
    destination_dir: str | Path,
    export_dir: str | Path = "data/exports",
    database: str | Path = "data/company_intelligence.db",
    checkpoint: str | Path = "data/analysis/company_analysis_checkpoint.json",
    model: str = "qwen3:8b",
    run_command: CommandRunner | None = None,
) -> dict[str, str | None]:
    if run_command is None:
        from .cli import main

        run_command = main

    stages = [
        ["scan", "--database", str(database), "--export-dir", str(export_dir)],
        ["crawl", "--database", str(database), "--export-dir", str(export_dir)],
        ["extract", "--export-dir", str(export_dir)],
        [
            "analyze", "--all", "--export-dir", str(export_dir),
            "--checkpoint", str(checkpoint), "--model", model,
        ],
        ["report", "--export-dir", str(export_dir)],
    ]
    for arguments in stages:
        result = run_command(arguments)
        if result != 0:
            raise WeeklyRunError(
                f"weekly run stopped because '{arguments[0]}' returned {result}"
            )

    html_path, csv_path = latest_report_pair(export_dir)
    return publish_report_pair(html_path, csv_path, destination_dir)


def install_windows_weekly_task(
    destination_dir: str | Path,
    project_dir: str | Path,
    day: str = "MON",
    start_time: str = "09:00",
    task_name: str = "HyperVision Company Intelligence Weekly",
    run_subprocess: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform_name: str | None = None,
) -> dict[str, str]:
    if (platform_name or os.name) != "nt":
        raise WeeklyRunError("Windows Task Scheduler installation is only available on Windows")

    project = Path(project_dir).resolve()
    destination = Path(destination_dir).expanduser().resolve()
    runner = project / "run_company_intelligence_weekly.cmd"
    log_path = project / "data" / "weekly_report.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python_path = Path(sys.executable).resolve()
    runner.write_text(
        "@echo off\n"
        f'cd /d "{project}"\n'
        "set PYTHONPATH=src\n"
        f'"{python_path}" -m company_intel run-weekly '
        f'--dropbox-dir "{destination}" >> "{log_path}" 2>&1\n',
        encoding="utf-8",
    )

    command = [
        "schtasks", "/Create", "/F", "/SC", "WEEKLY", "/D", day.upper(),
        "/ST", start_time, "/TN", task_name, "/TR", str(runner),
    ]
    completed = run_subprocess(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise WeeklyRunError(completed.stderr.strip() or completed.stdout.strip())
    escaped_name = task_name.replace("'", "''")
    settings_command = (
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 12); "
        f"Set-ScheduledTask -TaskName '{escaped_name}' -Settings $settings | Out-Null"
    )
    settings_result = run_subprocess(
        ["powershell", "-NoProfile", "-Command", settings_command],
        capture_output=True,
        text=True,
    )
    if settings_result.returncode != 0:
        raise WeeklyRunError(
            settings_result.stderr.strip() or settings_result.stdout.strip()
        )
    return {
        "task_name": task_name,
        "schedule": f"weekly on {day.upper()} at {start_time}",
        "runner": str(runner),
        "dropbox_dir": str(destination),
    }
