"""日常数据更新任务的共享状态与互斥控制。"""

from __future__ import annotations

import sqlite3
from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now


class DailyJobBusyError(RuntimeError):
    """已有待执行或运行中的日常任务。"""


_KEYS = (
    "daily_job_status",
    "daily_job_source",
    "daily_job_requested_at",
    "daily_job_requested_by",
    "daily_job_started_at",
    "daily_job_finished_at",
    "daily_job_result_code",
    "daily_job_message",
)


def _read(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def _write(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_setting(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (key, value, utc_now()),
    )


def get_daily_job(app_db: AppDatabase) -> dict[str, Any]:
    with app_db.connect() as conn:
        values = {key: _read(conn, key) for key in _KEYS}
    result_code = values["daily_job_result_code"]
    return {
        "status": values["daily_job_status"] or "IDLE",
        "source": values["daily_job_source"] or None,
        "requested_at": values["daily_job_requested_at"] or None,
        "requested_by": values["daily_job_requested_by"] or None,
        "started_at": values["daily_job_started_at"] or None,
        "finished_at": values["daily_job_finished_at"] or None,
        "result_code": int(result_code) if result_code else None,
        "message": values["daily_job_message"] or None,
    }


def request_daily_job(app_db: AppDatabase, actor: str) -> dict[str, Any]:
    """将手动日跑请求原子地加入队列。"""
    now = utc_now()
    with app_db.transaction() as conn:
        status = _read(conn, "daily_job_status", "IDLE")
        if status in {"PENDING", "RUNNING"}:
            raise DailyJobBusyError("已有数据更新任务等待执行或正在运行")
        values = {
            "daily_job_status": "PENDING",
            "daily_job_source": "MANUAL",
            "daily_job_requested_at": now,
            "daily_job_requested_by": actor,
            "daily_job_started_at": "",
            "daily_job_finished_at": "",
            "daily_job_result_code": "",
            "daily_job_message": "已加入执行队列",
        }
        for key, value in values.items():
            _write(conn, key, value)
    return get_daily_job(app_db)


def claim_daily_job(app_db: AppDatabase, source: str) -> bool:
    """由 Worker 原子地领取手动或定时任务。"""
    now = utc_now()
    with app_db.transaction() as conn:
        status = _read(conn, "daily_job_status", "IDLE")
        if status == "RUNNING":
            return False
        if source == "MANUAL" and status != "PENDING":
            return False
        _write(conn, "daily_job_status", "RUNNING")
        _write(conn, "daily_job_source", source)
        _write(conn, "daily_job_started_at", now)
        _write(conn, "daily_job_finished_at", "")
        _write(conn, "daily_job_result_code", "")
        _write(conn, "daily_job_message", "正在更新行情并执行策略")
    return True


def finish_daily_job(
    app_db: AppDatabase,
    return_code: int,
    message: str | None = None,
) -> None:
    """记录日常任务最终状态。"""
    with app_db.transaction() as conn:
        _write(conn, "daily_job_status", "SUCCEEDED" if return_code == 0 else "FAILED")
        _write(conn, "daily_job_finished_at", utc_now())
        _write(conn, "daily_job_result_code", str(return_code))
        _write(
            conn,
            "daily_job_message",
            message
            or (
                "数据更新完成"
                if return_code == 0
                else f"数据更新失败，退出码 {return_code}"
            ),
        )
