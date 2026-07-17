"""串行任务取消状态与 Worker 中止轮询测试。"""

from io import StringIO
from pathlib import Path

from sequoia_x.app import worker
from sequoia_x.app.db import AppDatabase
from sequoia_x.app.jobs import (
    claim_next_job,
    enqueue_job,
    finish_cancelled_job,
    request_job_cancel,
)


def test_running_job_cancellation_is_confirmed_by_worker(tmp_path: Path) -> None:
    db = AppDatabase(str(tmp_path / "app.db"))
    queued = enqueue_job(db, "DAILY_UPDATE", "admin")
    running = claim_next_job(db)
    assert running and running["id"] == queued["id"]

    requested = request_job_cancel(db, queued["id"], "admin")
    assert requested["status"] == "RUNNING"
    assert requested["cancel_requested"] == 1
    assert requested["current_stage"] == "正在中止"

    finish_cancelled_job(db, queued["id"], -15)
    finished = db.query_one("SELECT * FROM job_run WHERE id=?", (queued["id"],))
    assert finished and finished["status"] == "CANCELLED"
    assert finished["exit_code"] == -15


def test_worker_stops_process_when_cancel_is_requested(tmp_path: Path, monkeypatch) -> None:
    db = AppDatabase(str(tmp_path / "app.db"))
    queued = enqueue_job(db, "DAILY_UPDATE", "admin")
    running = claim_next_job(db)
    assert running is not None
    request_job_cancel(db, queued["id"], "admin")

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = StringIO("任务已经启动\n")
            self.returncode: int | None = None
            self.pid = 12345

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is None
            return self.returncode if self.returncode is not None else 0

    process = FakeProcess()
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: process)
    terminated: list[int] = []

    def terminate(fake: FakeProcess) -> None:
        terminated.append(fake.pid)
        fake.returncode = -15

    monkeypatch.setattr(worker, "_terminate_process_tree", terminate)
    assert worker.run_job(db, running) == -15
    assert terminated == [12345]
