"""独立日程 Worker：交易日收盘后运行主流程并执行备份。"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from sequoia_x.app.backup import backup_sqlite
from sequoia_x.app.daily_job import claim_daily_job, finish_daily_job, get_daily_job
from sequoia_x.app.db import AppDatabase
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
    if get_daily_job(app_db)["status"] == "RUNNING":
        finish_daily_job(app_db, -1, "Worker 重启，上一次数据更新已中断")
    while True:
        now = datetime.now()
        schedule = app_db.get_setting("daily_run_time", settings.daily_run_time) or "18:30"
        last_run = app_db.get_setting("last_scheduler_date")
        manual = get_daily_job(app_db)["status"] == "PENDING"
        schedule_due = (
            now.strftime("%H:%M") >= schedule
            and last_run != now.date().isoformat()
        )
        scheduled = schedule_due and is_trade_date(now.date())
        source = "MANUAL" if manual else "SCHEDULED"
        if (manual or scheduled) and claim_daily_job(app_db, source):
            logger.info(f"触发 {now.date().isoformat()} 日常流程，来源 {source}")
            return_code = run_daily_once()
            if scheduled:
                app_db.set_setting("last_scheduler_date", now.date().isoformat())
            app_db.set_setting("last_scheduler_result", str(return_code))
            if return_code == 0:
                run_backups(app_db)
            else:
                logger.error(f"日常流程失败，退出码 {return_code}")
            finish_daily_job(app_db, return_code)
        time.sleep(30)


if __name__ == "__main__":
    main()
