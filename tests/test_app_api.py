"""FastAPI 登录、CSRF 与账户接口测试。"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sequoia_x.app.api import create_app
from sequoia_x.core.config import Settings


def test_auth_csrf_and_account_flow(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    client = TestClient(create_app(settings))
    assert client.get("/health").status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    assert client.get("/api/v1/auth/me").status_code == 200

    payload = {
        "name": "API模拟组合",
        "account_type": "PAPER",
        "initial_cash": 100_000,
        "commission_rate": 0.0003,
        "minimum_commission": 5,
        "stamp_duty_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
    }
    assert client.post("/api/v1/accounts", json=payload).status_code == 403
    created = client.post(
        "/api/v1/accounts", json=payload, headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 200
    accounts = client.get("/api/v1/accounts").json()
    assert accounts[0]["portfolio"]["cash"] == 100_000

    assert client.post("/api/v1/runs/trigger").status_code == 403
    triggered = client.post(
        "/api/v1/runs/trigger", headers={"X-CSRF-Token": csrf}
    )
    assert triggered.status_code == 202
    assert triggered.json()["status"] == "PENDING"
    assert client.get("/api/v1/runs/status").json()["source"] == "MANUAL"
    duplicate = client.post(
        "/api/v1/runs/trigger", headers={"X-CSRF-Token": csrf}
    )
    assert duplicate.status_code == 409
    assert client.post(f"/api/v1/jobs/{triggered.json()['id']}/cancel").status_code == 403
    cancelled = client.post(
        f"/api/v1/jobs/{triggered.json()['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["exit_code"] == -15
    assert client.get("/api/v1/runs/status").json()["status"] == "CANCELLED"


def test_guest_can_read_business_data_but_not_admin_endpoints(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    client = TestClient(create_app(settings))

    for path in (
        "/api/v1/dashboard",
        "/api/v1/candidates/search",
        "/api/v1/plans",
        "/api/v1/accounts",
        "/api/v1/watchlist",
        "/api/v1/reports",
        "/api/v1/backtests",
    ):
        assert client.get(path).status_code == 200

    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/settings").status_code == 401
    assert client.post("/api/v1/runs/trigger").status_code == 401


def test_backtest_cancel_api_and_detail_logs(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    app = create_app(settings)
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    csrf = login.json()["csrf_token"]
    run_id = app.state.backtest.create_run(
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

    assert client.post(f"/api/v1/backtests/{run_id}/cancel").status_code == 403
    response = client.post(
        f"/api/v1/backtests/{run_id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "CANCELLED"
    detail = client.get(f"/api/v1/backtests/{run_id}").json()
    assert detail["current_stage"] == "已取消"
    assert len(detail["logs"]) == 2
    assert client.post(
        f"/api/v1/backtests/{run_id}/cancel",
        headers={"X-CSRF-Token": csrf},
    ).status_code == 409


def test_member_registration_and_personal_account_isolation(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "market.db"),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    app = create_app(settings)
    alice = TestClient(app)
    registered = alice.post(
        "/api/v1/auth/register",
        json={"username": "alice_01", "password": "alice-password"},
    )
    assert registered.status_code == 201
    assert registered.json()["role"] == "MEMBER"
    alice_csrf = registered.json()["csrf_token"]
    alice_headers = {"X-CSRF-Token": alice_csrf}

    payload = {
        "name": "我的模拟组合",
        "account_type": "PAPER",
        "initial_cash": 100_000,
        "commission_rate": 0.0003,
        "minimum_commission": 5,
        "stamp_duty_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
    }
    created = alice.post("/api/v1/accounts", json=payload, headers=alice_headers)
    assert created.status_code == 200
    account_id = created.json()["id"]
    assert alice.get("/api/v1/accounts").json()[0]["name"] == "我的模拟组合"
    assert alice.get("/api/v1/settings").status_code == 403
    assert alice.get("/api/v1/jobs").status_code == 403

    real_payload = {**payload, "name": "实盘", "account_type": "REAL_LEDGER"}
    assert alice.post(
        "/api/v1/accounts", json=real_payload, headers=alice_headers
    ).status_code == 403

    bob = TestClient(app)
    bob_registered = bob.post(
        "/api/v1/auth/register",
        json={"username": "bob_02", "password": "bob-password"},
    )
    assert bob_registered.status_code == 201
    assert bob.get("/api/v1/accounts").json() == []
    assert bob.get(f"/api/v1/accounts/{account_id}/portfolio").status_code == 404

    duplicate = TestClient(app).post(
        "/api/v1/auth/register",
        json={"username": "ALICE_01", "password": "another-password"},
    )
    assert duplicate.status_code == 409


def test_watchlist_jobs_and_system_endpoints(tmp_path: Path) -> None:
    market_db = tmp_path / "market.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            "CREATE TABLE stock_market_cap(symbol TEXT PRIMARY KEY,name TEXT,market_cap REAL,updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO stock_market_cap VALUES ('600519','贵州茅台',2000000000000,'2026-07-17')"
        )
    settings = Settings(
        db_path=str(market_db),
        app_db_path=str(tmp_path / "app.db"),
        feishu_webhook_url="https://example.com/hook",
        admin_username="admin",
        admin_password="test-password-123",
        cookie_secure=False,
    )
    client = TestClient(create_app(settings))
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}
    )
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    search = client.get("/api/v1/stocks/search?q=茅台")
    assert search.status_code == 200
    assert search.json()[0]["symbol"] == "600519"
    created = client.post(
        "/api/v1/watchlist",
        headers=headers,
        json={
            "symbol": "600519", "group_name": "核心", "note": "等待回调",
            "target_price": 1600, "watch_price": 1400,
        },
    )
    assert created.status_code == 200
    assert client.get("/api/v1/watchlist").json()[0]["group_name"] == "核心"

    rejected = client.post(
        "/api/v1/jobs", headers=headers,
        json={"job_type": "BACKFILL", "confirmation": ""},
    )
    assert rejected.status_code == 422
    queued = client.post(
        "/api/v1/jobs", headers=headers,
        json={"job_type": "BACKFILL", "confirmation": "BACKFILL"},
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "PENDING"
    assert client.get("/api/v1/system/health").status_code == 200
    assert client.get("/api/v1/audit-logs").status_code == 200
