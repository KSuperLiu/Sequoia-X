"""日跑结果固化、日报和幂等测试。"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.pipeline import DailyTrackingService
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.post_filter import FilterDecision


def test_daily_pipeline_persists_candidate_report_and_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    start = date(2026, 1, 1)
    rows = []
    for index in range(60):
        close = 65 + index * 0.07
        rows.append(
            {
                "symbol": "600001",
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close - 0.2,
                "high": 100 if index == 0 else close + 0.5,
                "low": 65 if index == 0 else close - 0.5,
                "close": close,
                "volume": 1_000_000,
                "turnover": 200_000_000,
            }
        )
    rows[-1].update(close=70.0, open=69.0, high=70.5, volume=2_000_000)
    with sqlite3.connect(settings.db_path) as conn:
        pd.DataFrame(rows).to_sql("stock_daily", conn, if_exists="append", index=False)
        conn.execute(
            "INSERT INTO stock_market_cap(symbol,name,market_cap,updated_at) VALUES (?,?,?,?)",
            ("600001", "测试股份", 10_000_000_000, date.today().isoformat()),
        )

    app_db = AppDatabase(settings.app_db_path)
    with app_db.transaction() as conn:
        conn.execute(
            "INSERT INTO stock_profile(symbol,name,industry,updated_at) VALUES (?,?,?,?)",
            ("600001", "测试股份", "测试行业", utc_now()),
        )
    service = DailyTrackingService(app_db, engine)
    latest = rows[-1]["date"]
    monkeypatch.setattr(service, "_expected_trade_date", lambda: latest)
    monkeypatch.setattr(
        service.market,
        "fetch_quotes",
        lambda symbols, trade_date: {
            "600001": {
                "close": 35.0,
                "pe_ttm": 20.0,
                "pb_mrq": 2.0,
                "trade_status": 1,
                "is_st": 0,
            }
        },
    )
    monkeypatch.setattr(service.market, "refresh_profiles", lambda symbols: None)
    valuation_refreshes: list[bool] = []
    monkeypatch.setattr(
        service.valuation,
        "refresh_active_results",
        lambda: valuation_refreshes.append(True),
    )
    raw = {"TurtleTradeStrategy": ["600001"]}
    filtered = {
        "TurtleTradeStrategy": FilterDecision(
            selected=["600001"], dropped_low_turnover=0, truncated=0
        )
    }

    first = service.persist(raw, filtered)
    second = service.persist(raw, filtered)
    assert first["trade_date"] == latest
    assert second["run_id"] == first["run_id"]
    assert app_db.query_one("SELECT COUNT(*) count FROM pipeline_run")["count"] == 1
    assert app_db.query_one("SELECT COUNT(*) count FROM candidate")["count"] == 1
    assert len(valuation_refreshes) == 2
    report = app_db.query_one("SELECT html FROM daily_report")
    assert report and "测试股份" in report["html"]


def test_expected_trade_date_uses_previous_day_before_daily_cutoff(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        daily_run_time="18:30",
    )
    service = DailyTrackingService(AppDatabase(settings.app_db_path), DataEngine(settings))

    # 强制使用无网络回退路径，单独验证盘中/日跑后的日期边界。
    monkeypatch.setitem(__import__("sys").modules, "baostock", None)
    assert service._expected_trade_date(datetime(2026, 7, 16, 13, 50)) == "2026-07-15"
    assert service._expected_trade_date(datetime(2026, 7, 16, 18, 30)) == "2026-07-16"
