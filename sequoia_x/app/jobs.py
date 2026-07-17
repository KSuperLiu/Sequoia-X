"""持久化串行任务队列。"""

from __future__ import annotations

from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now


JOB_TYPES = {"DAILY_UPDATE", "REFRESH_MARKET_CAP", "BACKFILL"}
ACTIVE_STATUSES = {"PENDING", "RUNNING"}


class JobBusyError(RuntimeError):
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


def fail_interrupted_jobs(db: AppDatabase) -> None:
    with db.transaction() as conn:
        rows = conn.execute("SELECT id FROM job_run WHERE status='RUNNING'").fetchall()
        for row in rows:
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
