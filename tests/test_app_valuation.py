"""季度财务、估值版本和历史百分位测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from sequoia_x.app.api import create_app
from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.valuation import ValuationService, percentile_rank, quantile
from sequoia_x.core.config import Settings


class _Engine:
    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)


class _Provider:
    def __init__(self) -> None:
        self.history_calls = 0
        self.reopens = 0

    def open(self) -> object:
        return object()

    def close(self, _client: object) -> None:
        return None

    def reopen(self, _client: object) -> bool:
        self.reopens += 1
        return True

    def quarterly(
        self, _client: object, symbol: str, _periods: list[tuple[int, int]]
    ) -> list[dict[str, object]]:
        return [
            {
                "symbol": symbol,
                "report_period": "2025-12-31",
                "announcement_date": "2026-04-17",
                "fiscal_year": 2025,
                "fiscal_quarter": 4,
                "revenue": 20_000_000_000,
                "net_profit": 5_000_000_000,
                "eps_ttm": 5,
                "total_shares": 1_000_000_000,
                "roe": 0.2,
                "gross_margin": 0.9,
                "net_margin": 0.5,
                "yoy_net_profit": 0.2,
                "source": "baostock_quarterly",
                "source_payload_json": "{}",
            }
        ]

    def valuation_history(
        self,
        _client: object,
        _symbol: str,
        _start_date: str,
        _end_date: str,
    ) -> list[dict[str, object]]:
        self.history_calls += 1
        if self.history_calls == 1:
            raise RuntimeError("用户未登录")
        return [{"date": "2026-08-05", "close": 100, "pe_ttm": 30, "pb_mrq": 3}]


def _service(tmp_path: Path) -> tuple[AppDatabase, ValuationService]:
    market_db = tmp_path / "market.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            "CREATE TABLE stock_market_cap("
            "symbol TEXT PRIMARY KEY,name TEXT,market_cap REAL,updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO stock_market_cap VALUES ('600519','贵州茅台',100000000000,'2026-08-05')"
        )
    db = AppDatabase(str(tmp_path / "app.db"))
    service = ValuationService(db, _Engine(market_db))  # type: ignore[arg-type]
    return db, service


def _insert_fundamental(
    db: AppDatabase,
    period: str,
    announcement: str,
    quarter: int,
    revenue: float,
    profit: float,
    revenue_ttm: float | None = None,
    profit_ttm: float | None = None,
) -> None:
    now = utc_now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO fundamental_snapshot(symbol,report_period,announcement_date,fiscal_year,"
            "fiscal_quarter,revenue,net_profit,revenue_ttm,net_profit_ttm,eps_ttm,total_shares,"
            "yoy_net_profit,source,source_payload_json,created_at,updated_at) "
            "VALUES ('600519',?,?,?,?,?,?,?,?,?,?,0.2,'baostock_quarterly','{}',?,?)",
            (
                period,
                announcement,
                int(period[:4]),
                quarter,
                revenue,
                profit,
                revenue_ttm,
                profit_ttm,
                5.0,
                1_000_000_000,
                now,
                now,
            ),
        )


def test_percentile_rank_handles_invalid_values() -> None:
    assert percentile_rank([10, 20, 30], 20) == 66.7
    assert percentile_rank([0, -1], 20) is None
    assert percentile_rank([10], None) is None
    assert quantile([10, 20, 30, 40], 0.5) == 25


def test_valuation_case_is_versioned_and_reproducible(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    now = utc_now()
    with db.transaction() as conn:
        for date_value, close, pe, pb in [
            ("2026-08-01", 90, 10, 1),
            ("2026-08-04", 95, 20, 2),
            ("2026-08-05", 100, 30, 3),
        ]:
            conn.execute(
                "INSERT INTO market_snapshot(symbol,date,close,pe_ttm,pb_mrq,source,updated_at) "
                "VALUES ('600519',?,?,?,?, 'test',?)",
                (date_value, close, pe, pb, now),
            )
    _insert_fundamental(
        db,
        "2025-12-31",
        "2026-04-17",
        4,
        20_000_000_000,
        5_000_000_000,
        20_000_000_000,
        5_000_000_000,
    )
    _insert_fundamental(db, "2026-06-30", "2026-08-20", 2, 12_000_000_000, 3_000_000_000)

    payload = {
        "method": "PE",
        "forecast_period": "2026E",
        "forecast_value": 5,
        "bear_multiple": 15,
        "base_multiple": 20,
        "bull_multiple": 25,
        "thesis": "稳定增长",
        "catalysts": ["提价"],
        "risks": ["需求下降"],
        "source_note": "管理层指引",
    }
    detail = service.create_case("600519", payload, "admin")
    assert detail["active_case"]["version"] == 1
    assert detail["result"]["base_target"] == 100
    assert detail["result"]["valuation_zone"] == "FAIR_LOW"
    assert detail["result"]["pe_percentile"] == 100
    assert detail["fundamental"]["report_period"] == "2025-12-31"
    assert detail["fundamental"]["ps_ttm"] == 5
    assert detail["fundamental"]["peg"] == 1.5

    second = service.create_case(
        "600519", {**payload, "forecast_value": 6, "source_note": "新预测"}, "admin"
    )
    assert second["active_case"]["version"] == 2
    assert second["result"]["base_target"] == 120
    versions = db.query_all(
        "SELECT version,is_active FROM valuation_case WHERE symbol='600519' ORDER BY version"
    )
    assert versions == [{"version": 1, "is_active": 0}, {"version": 2, "is_active": 1}]
    assert db.query_one("SELECT version FROM schema_migration WHERE version=6") == {"version": 6}


def test_ttm_rebuild_uses_only_comparable_cumulative_periods(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    _insert_fundamental(db, "2024-03-31", "2024-04-20", 1, 20, 2)
    _insert_fundamental(db, "2024-12-31", "2025-04-20", 4, 100, 10)
    _insert_fundamental(db, "2025-03-31", "2025-04-20", 1, 30, 3)
    service._rebuild_ttm("600519")
    latest = db.query_one(
        "SELECT revenue_ttm,net_profit_ttm FROM fundamental_snapshot "
        "WHERE symbol='600519' AND report_period='2025-03-31'"
    )
    assert latest == {"revenue_ttm": 110.0, "net_profit_ttm": 11.0}


def test_refresh_tracked_collects_watchlist_financials_and_history(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    now = utc_now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO watchlist_item(symbol,group_name,note,created_at,updated_at) "
            "VALUES ('600519','默认分组','',?,?)",
            (now, now),
        )
    provider = _Provider()
    service.provider = provider  # type: ignore[assignment]
    progress: list[tuple[int, int, str]] = []

    result = service.refresh_tracked(
        progress=lambda current, total, message: progress.append((current, total, message)),
        today=date(2026, 8, 6),
    )

    assert result == {
        "symbols": 1,
        "fundamentals": 1,
        "history_rows": 1,
        "auto_cases": 0,
        "failed": 0,
    }
    assert provider.reopens == 1
    assert provider.history_calls == 2
    assert progress == [(1, 1, "财务数据 1 / 1：600519")]
    assert db.query_one(
        "SELECT revenue_ttm,net_profit_ttm FROM fundamental_snapshot WHERE symbol='600519'"
    ) == {"revenue_ttm": 20_000_000_000.0, "net_profit_ttm": 5_000_000_000.0}
    assert db.query_one("SELECT pe_ttm,pb_mrq FROM market_snapshot WHERE symbol='600519'") == {
        "pe_ttm": 30.0,
        "pb_mrq": 3.0,
    }


def test_reference_case_is_generated_and_does_not_override_manual_case(tmp_path: Path) -> None:
    db, service = _service(tmp_path)
    now = utc_now()
    with db.transaction() as conn:
        for index in range(60):
            conn.execute(
                "INSERT INTO market_snapshot(symbol,date,close,pe_ttm,pb_mrq,source,updated_at) "
                "VALUES ('600519',date('2026-08-05',?),100,?,3,'test',?)",
                (f"-{59 - index} day", 10 + index, now),
            )
    _insert_fundamental(
        db,
        "2026-06-30",
        "2026-07-30",
        2,
        20_000_000_000,
        5_000_000_000,
        20_000_000_000,
        5_000_000_000,
    )

    assert service.refresh_reference_cases(["600519"]) == 1
    generated = service.detail("600519")
    assert generated["active_case"]["created_by"] == "system:auto"
    assert generated["active_case"]["forecast_value"] == 6
    assert generated["active_case"]["bear_multiple"] == 24.75
    assert generated["active_case"]["base_multiple"] == 39.5
    assert generated["active_case"]["bull_multiple"] == 54.25
    assert service.refresh_reference_cases(["600519"]) == 0

    manual_payload = {
        "method": "PE",
        "forecast_period": "2027E",
        "forecast_value": 5,
        "bear_multiple": 15,
        "base_multiple": 20,
        "bull_multiple": 25,
        "thesis": "人工判断",
        "catalysts": [],
        "risks": [],
        "source_note": "研究员预测",
    }
    service.create_case("600519", manual_payload, "admin")
    assert service.refresh_reference_cases(["600519"]) == 0
    assert service.detail("600519")["active_case"]["created_by"] == "admin"


def test_valuation_api_requires_admin_and_writes_audit(tmp_path: Path) -> None:
    market_db = tmp_path / "market.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            "CREATE TABLE stock_market_cap("
            "symbol TEXT PRIMARY KEY,name TEXT,market_cap REAL,updated_at TEXT)"
        )
        conn.execute("INSERT INTO stock_market_cap VALUES ('600519','贵州茅台',100,'2026-08-05')")
        conn.execute(
            "CREATE TABLE stock_daily(id INTEGER PRIMARY KEY,symbol TEXT,date TEXT,open REAL,"
            "high REAL,low REAL,close REAL,volume REAL,turnover REAL)"
        )
    settings = Settings(
        db_path=str(market_db),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    app = create_app(settings)
    client = TestClient(app)
    payload = {
        "method": "PE",
        "forecast_period": "2026E",
        "forecast_value": 5,
        "bear_multiple": 15,
        "base_multiple": 20,
        "bull_multiple": 25,
        "thesis": "",
        "catalysts": [],
        "risks": [],
        "source_note": "",
    }
    assert client.post("/api/v1/stocks/600519/valuation-cases", json=payload).status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}
    ).json()
    headers = {"X-CSRF-Token": login["csrf_token"]}
    response = client.post("/api/v1/stocks/600519/valuation-cases", json=payload, headers=headers)
    assert response.status_code == 201
    assert response.json()["active_case"]["version"] == 1
    audit = app.state.db.query_one(
        "SELECT action,detail_json FROM audit_log WHERE action='CREATE_VALUATION_CASE'"
    )
    assert audit and audit["action"] == "CREATE_VALUATION_CASE"
    assert json.loads(audit["detail_json"])["symbol"] == "600519"
