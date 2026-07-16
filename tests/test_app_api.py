"""FastAPI 登录、CSRF 与账户接口测试。"""

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
