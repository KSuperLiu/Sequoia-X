"""持久化串行任务队列。"""

from __future__ import annotations

from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now

JOB_TYPES = {"DAILY_UPDATE", "REFRESH_MARKET_CAP", "REFRESH_FINANCIALS", "BACKFILL"}
ACTIVE_STATUSES = {"PENDING", "RUNNING"}


class JobBusyError(RuntimeError):
    pass


class JobCancellationError(RuntimeError):
    pass


def enqueue_job(
    db: AppDatabase,
    job_type: str,
    actor: str,
    source: str = "MANUAL",
    retry_of: int | None = None,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise ValueError("任务类型无效")
    with db.transaction() as conn:
        backtest = conn.execute(
            "SELECT id FROM backtest_run WHERE status IN ('PENDING','RUNNING') ORDER BY id LIMIT 1"
        ).fetchone()
        if backtest:
            raise JobBusyError(f"回测任务 #{backtest['id']} 正在执行，请完成后再运行数据维护任务")
        active = conn.execute(
            "SELECT id,job_type FROM job_run WHERE status IN ('PENDING','RUNNING') ORDER BY id LIMIT 1"
        ).fetchone()
        if active:
            raise JobBusyError(f"已有 {active['job_type']} 任务等待或正在执行")
        cursor = conn.execute(
            "INSERT INTO job_run(job_type,source,status,requested_by,requested_at,message,retry_of) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_type, source, "PENDING", actor, utc_now(), "已加入串行执行队列", retry_of),
        )
        job_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO job_log(job_run_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, "INFO", "任务已加入队列", utc_now()),
        )
    return get_job(db, job_id) or {}


def enqueue_scheduled_daily(db: AppDatabase, trade_date: str) -> dict[str, Any] | None:
    existing = db.query_one(
        "SELECT * FROM job_run WHERE job_type='DAILY_UPDATE' AND source='SCHEDULED' "
        "AND substr(requested_at,1,10)=? ORDER BY id DESC LIMIT 1",
        (trade_date,),
    )
    if existing:
        return None
    return enqueue_job(db, "DAILY_UPDATE", "scheduler", "SCHEDULED")


def claim_next_job(db: AppDatabase) -> dict[str, Any] | None:
    with db.transaction() as conn:
        running = conn.execute(
            "SELECT id FROM job_run WHERE status='RUNNING' ORDER BY id LIMIT 1"
        ).fetchone()
        if running:
            return None
        row = conn.execute(
            "SELECT * FROM job_run WHERE status='PENDING' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE job_run SET status='RUNNING',started_at=?,current_stage='启动',message=? WHERE id=?",
            (utc_now(), "正在启动任务", row["id"]),
        )
        return dict(conn.execute("SELECT * FROM job_run WHERE id=?", (row["id"],)).fetchone())


def append_job_log(db: AppDatabase, job_id: int, message: str, level: str = "INFO") -> None:
    cleaned = message.strip()
    if not cleaned:
        return
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO job_log(job_run_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, level, cleaned[:4000], utc_now()),
        )
        conn.execute(
            "UPDATE job_run SET message=? WHERE id=?", (cleaned[-500:], job_id)
        )


def update_job_progress(
    db: AppDatabase, job_id: int, stage: str, current: int = 0, total: int = 0
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_run SET current_stage=?,progress_current=?,progress_total=? WHERE id=?",
            (stage, current, total, job_id),
        )


def finish_job(db: AppDatabase, job_id: int, exit_code: int, message: str | None = None) -> None:
    status = "SUCCEEDED" if exit_code == 0 else "FAILED"
    final_message = message or ("任务执行成功" if exit_code == 0 else f"任务失败，退出码 {exit_code}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_run SET status=?,finished_at=?,exit_code=?,current_stage='完成',message=? WHERE id=?",
            (status, utc_now(), exit_code, final_message, job_id),
        )
        conn.execute(
            "INSERT INTO job_log(job_run_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, "INFO" if exit_code == 0 else "ERROR", final_message, utc_now()),
        )


def request_job_cancel(db: AppDatabase, job_id: int, actor: str) -> dict[str, Any]:
    """取消网页手动任务；等待任务立即取消，运行任务交由 Worker 停止进程。"""
    now = utc_now()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM job_run WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobCancellationError("任务不存在")
        if row["source"] != "MANUAL":
            raise JobCancellationError("仅允许中止网页手动启动的任务")
        if row["status"] == "PENDING":
            message = f"任务在启动前由 {actor} 中止"
            conn.execute(
                "UPDATE job_run SET status='CANCELLED',finished_at=?,exit_code=-15,"
                "current_stage='已取消',message=?,cancel_requested=1,cancel_requested_at=?,"
                "cancel_requested_by=? WHERE id=? AND status='PENDING'",
                (now, message, now, actor, job_id),
            )
        elif row["status"] == "RUNNING":
            if row["cancel_requested"]:
                raise JobCancellationError("任务正在中止，请稍候")
            message = f"{actor} 已请求中止，正在停止任务进程"
            conn.execute(
                "UPDATE job_run SET cancel_requested=1,cancel_requested_at=?,"
                "cancel_requested_by=?,message=?,current_stage='正在中止' "
                "WHERE id=? AND status='RUNNING'",
                (now, actor, message, job_id),
            )
        else:
            raise JobCancellationError("只有等待中或运行中的任务可以中止")
        conn.execute(
            "INSERT INTO job_log(job_run_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, "WARNING", message, now),
        )
    return get_job(db, job_id) or {}


def is_cancel_requested(db: AppDatabase, job_id: int) -> bool:
    row = db.query_one("SELECT cancel_requested FROM job_run WHERE id=?", (job_id,))
    return bool(row and row["cancel_requested"])


def finish_cancelled_job(db: AppDatabase, job_id: int, exit_code: int = -15) -> None:
    message = "任务已按管理员请求中止"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE job_run SET status='CANCELLED',finished_at=?,exit_code=?,"
            "current_stage='已取消',message=? WHERE id=?",
            (utc_now(), exit_code, message, job_id),
        )
        conn.execute(
            "INSERT INTO job_log(job_run_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, "WARNING", message, utc_now()),
        )


def fail_interrupted_jobs(db: AppDatabase) -> None:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT id,cancel_requested FROM job_run WHERE status='RUNNING'"
        ).fetchall()
        for row in rows:
            if row["cancel_requested"]:
                conn.execute(
                    "UPDATE job_run SET status='CANCELLED',finished_at=?,exit_code=-15,"
                    "current_stage='已取消',message=? WHERE id=?",
                    (utc_now(), "Worker 重启时确认任务已中止", row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE job_run SET status='FAILED',finished_at=?,exit_code=-1,message=? WHERE id=?",
                    (utc_now(), "Worker 重启，上一次任务已中断", row["id"]),
                )


def get_job(db: AppDatabase, job_id: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM job_run WHERE id=?", (job_id,))


def latest_job(db: AppDatabase, job_type: str | None = None) -> dict[str, Any] | None:
    if job_type:
        return db.query_one(
            "SELECT * FROM job_run WHERE job_type=? ORDER BY id DESC LIMIT 1", (job_type,)
        )
    return db.query_one("SELECT * FROM job_run ORDER BY id DESC LIMIT 1")
