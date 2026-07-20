"""回测任务日志、进度和协作式中止测试。"""

from pathlib import Path

import pytest

from sequoia_x.app.backtest import (
    BacktestCancellationError,
    BacktestCancelled,
    BacktestService,
)
from sequoia_x.app.db import AppDatabase


class _Engine:
    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)


def _service(tmp_path: Path) -> tuple[AppDatabase, BacktestService]:
    db = AppDatabase(str(tmp_path / "app.db"))
    return db, BacktestService(db, _Engine(tmp_path / "market.db"))  # type: ignore[arg-type]


def _create(service: BacktestService) -> int:
    return service.create_run(
        strategy_name="TurtleTradeStrategy",
        start_date="2025-01-01",
        end_date="2025-12-31",
        initial_cash=100_000,
        fee={
            "commission_rate": 0.0003,
            "minimum_commission": 5,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
        },
    )


def test_pending_backtest_can_be_cancelled_and_is_logged(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    run_id = _create(service)

    cancelled = service.request_cancel(run_id, "admin")

    assert cancelled["status"] == "CANCELLED"
    assert cancelled["current_stage"] == "已取消"
    assert cancelled["cancel_requested_by"] == "admin"
    logs = db.query_all(
        "SELECT level,message FROM backtest_log WHERE backtest_run_id=? ORDER BY id", (run_id,)
    )
    assert [row["level"] for row in logs] == ["INFO", "WARNING"]
    assert "admin" in logs[-1]["message"]
    with pytest.raises(BacktestCancellationError):
        service.request_cancel(run_id, "admin")


def test_running_backtest_stops_at_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, service = _service(tmp_path)
    run_id = _create(service)

    def stop_at_checkpoint(run: dict[str, object]) -> None:
        service.request_cancel(int(run["id"]), "admin")
        service._checkpoint(int(run["id"]), "模拟交易", 5, 20)

    monkeypatch.setattr(service, "_execute", stop_at_checkpoint)
    service.execute(run_id)

    row = db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
    assert row is not None
    assert row["status"] == "CANCELLED"
    assert row["current_stage"] == "已取消"
    logs = db.query_all(
        "SELECT message FROM backtest_log WHERE backtest_run_id=? ORDER BY id", (run_id,)
    )
    assert any("中止回测" in item["message"] for item in logs)
    assert "安全检查点停止" in logs[-1]["message"]


def test_checkpoint_reports_progress(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    run_id = _create(service)
    with db.transaction() as conn:
        conn.execute("UPDATE backtest_run SET status='RUNNING' WHERE id=?", (run_id,))

    service._checkpoint(run_id, "模拟交易", 8, 20)

    row = db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
    assert row is not None
    assert row["current_stage"] == "模拟交易"
    assert row["progress_current"] == 8
    assert row["progress_total"] == 20


def test_checkpoint_raises_after_cancel_request(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    run_id = _create(service)
    with db.transaction() as conn:
        conn.execute("UPDATE backtest_run SET status='RUNNING' WHERE id=?", (run_id,))
    service.request_cancel(run_id, "admin")

    with pytest.raises(BacktestCancelled):
        service._checkpoint(run_id, "模拟交易", 1, 10)


def test_service_restart_marks_orphaned_run_failed(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    run_id = _create(service)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE backtest_run SET status='RUNNING',current_stage='模拟交易' WHERE id=?",
            (run_id,),
        )

    BacktestService(db, _Engine(tmp_path / "market.db"))  # type: ignore[arg-type]

    row = db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["current_stage"] == "服务中断"
    assert "请重新创建回测" in row["error_message"]
