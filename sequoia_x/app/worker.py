"""独立日程 Worker：交易日收盘后运行主流程并执行备份。"""

from __future__ import annotations

import subprocess
import sys
import time
import os
import re
from datetime import date, datetime
from pathlib import Path

from sequoia_x.app.backup import backup_sqlite
from sequoia_x.app.db import AppDatabase
from sequoia_x.app.jobs import (
    append_job_log,
    claim_next_job,
    enqueue_scheduled_daily,
    fail_interrupted_jobs,
    finish_job,
    update_job_progress,
)
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


def is_trade_date(day: date) -> bool:
    try:
        import baostock as bs

        login = bs.login()
        if login.error_code != "0":
            return day.weekday() < 5
        try:
            result = bs.query_trade_dates(start_date=day.isoformat(), end_date=day.isoformat())
            if result.error_code == "0" and result.next():
                row = result.get_row_data()
                return len(row) > 1 and row[1] == "1"
        finally:
            bs.logout()
    except Exception:
        pass
    return day.weekday() < 5


def run_daily_once() -> int:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "main.py")],
        cwd=root,
        check=False,
    )
    return int(result.returncode)


def run_job(app_db: AppDatabase, job: dict[str, object]) -> int:
    root = Path(__file__).resolve().parents[2]
    commands = {
        "DAILY_UPDATE": [],
        "REFRESH_MARKET_CAP": ["--refresh-market-cap"],
        "BACKFILL": ["--backfill"],
    }
    job_type = str(job["job_type"])
    args = [sys.executable, str(root / "main.py"), *commands[job_type]]
    env = os.environ.copy()
    env["SEQUOIA_JOB_ID"] = str(job["id"])
    process = subprocess.Popen(
        args,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        append_job_log(app_db, int(job["id"]), line)
        matched = re.search(r"(\d+)\s*/\s*(\d+)", line)
        stage = "执行主流程"
        if "市值" in line:
            stage = "刷新市值"
        elif "行情" in line or "K线" in line:
            stage = "更新行情"
        elif "策略" in line:
            stage = "执行策略"
        elif "报告" in line or "飞书" in line:
            stage = "生成报告"
        if matched:
            update_job_progress(app_db, int(job["id"]), stage, int(matched[1]), int(matched[2]))
        else:
            update_job_progress(app_db, int(job["id"]), stage)
    return int(process.wait())


def run_backups(app_db: AppDatabase) -> None:
    settings = get_settings()
    backup_sqlite(settings.app_db_path, "backups/app", "sequoia_app", 30)
    today = date.today()
    last_week = app_db.get_setting("last_market_backup_week")
    week = f"{today.isocalendar().year}-{today.isocalendar().week}"
    if last_week != week:
        backup_sqlite(settings.db_path, "backups/market", "sequoia_market", 4)
        app_db.set_setting("last_market_backup_week", week)


def main() -> None:
    settings = get_settings()
    app_db = AppDatabase(settings.app_db_path)
    logger.info("Sequoia-X 日程 Worker 启动")
    fail_interrupted_jobs(app_db)
    while True:
        now = datetime.now()
        schedule = app_db.get_setting("daily_run_time", settings.daily_run_time) or "18:30"
        last_run = app_db.get_setting("last_scheduler_date")
        schedule_due = (
            now.strftime("%H:%M") >= schedule
            and last_run != now.date().isoformat()
        )
        scheduled = schedule_due and is_trade_date(now.date())
        if scheduled:
            try:
                enqueue_scheduled_daily(app_db, now.date().isoformat())
                app_db.set_setting("last_scheduler_date", now.date().isoformat())
            except Exception as exc:
                logger.warning(f"定时任务入队失败：{exc}")
        job = claim_next_job(app_db)
        if job:
            logger.info(f"执行任务 {job['id']}：{job['job_type']}")
            try:
                return_code = run_job(app_db, job)
                if return_code == 0 and job["job_type"] == "DAILY_UPDATE":
                    run_backups(app_db)
                finish_job(app_db, int(job["id"]), return_code)
            except Exception as exc:
                logger.exception("任务执行异常")
                finish_job(app_db, int(job["id"]), -1, f"任务执行异常：{exc}")
        time.sleep(30)


if __name__ == "__main__":
    main()
