from pathlib import Path

import pytest

from sequoia_x.app.daily_job import (
    DailyJobBusyError,
    claim_daily_job,
    finish_daily_job,
    get_daily_job,
    request_daily_job,
)
from sequoia_x.app.db import AppDatabase


def test_daily_job_lifecycle_and_lock(tmp_path: Path) -> None:
    app_db = AppDatabase(str(tmp_path / "app.db"))

    assert get_daily_job(app_db)["status"] == "IDLE"
    pending = request_daily_job(app_db, "admin")
    assert pending["status"] == "PENDING"
    assert pending["requested_by"] == "admin"
    with pytest.raises(DailyJobBusyError):
        request_daily_job(app_db, "admin")

    assert claim_daily_job(app_db, "MANUAL") is True
    assert claim_daily_job(app_db, "SCHEDULED") is False
    assert get_daily_job(app_db)["status"] == "RUNNING"

    finish_daily_job(app_db, 0)
    completed = get_daily_job(app_db)
    assert completed["status"] == "SUCCEEDED"
    assert completed["result_code"] == 0
    assert completed["finished_at"] is not None
