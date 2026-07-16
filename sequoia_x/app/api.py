"""Sequoia-X FastAPI 服务。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from sequoia_x.app.auth import AuthService, require_csrf, require_user
from sequoia_x.app.backtest import SUPPORTED_STRATEGIES, BacktestService
from sequoia_x.app.daily_job import DailyJobBusyError, get_daily_job, request_daily_job
from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.domain import PositionZone, RuleConfig
from sequoia_x.app.ledger import LedgerError, LedgerService
from sequoia_x.app.scoring import suggested_quantity
from sequoia_x.core.config import Settings, get_settings
from sequoia_x.data.engine import DataEngine


class LoginInput(BaseModel):
    username: str
    password: str


class AccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    account_type: Literal["PAPER", "REAL_LEDGER"]
    initial_cash: float = Field(gt=0)
    commission_rate: float = Field(ge=0)
    minimum_commission: float = Field(ge=0)
    stamp_duty_rate: float = Field(ge=0)
    transfer_fee_rate: float = Field(ge=0)


class FillInput(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    trade_date: str
    plan_id: int | None = None
    note: str = ""


class CashEventInput(BaseModel):
    event_type: Literal["DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FEE", "ADJUSTMENT"]
    amount: float
    trade_date: str
    note: str = ""


class PlanRevisionInput(BaseModel):
    entry_low: float = Field(gt=0)
    entry_high: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    zone: Literal["LEFT", "MIDDLE", "RIGHT", "VETO"]
    note: str = ""
    reason: str = Field(min_length=2, max_length=500)


class CandidateStatusInput(BaseModel):
    status: Literal["NEW", "WATCHING", "PLANNED", "BOUGHT", "DROPPED", "EXPIRED"]


class BacktestInput(BaseModel):
    strategy_name: str
    start_date: str
    end_date: str
    initial_cash: float = Field(gt=0)
    commission_rate: float = Field(ge=0)
    minimum_commission: float = Field(ge=0)
    stamp_duty_rate: float = Field(ge=0)
    transfer_fee_rate: float = Field(ge=0)


class RuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, float | int]


class SettingsInput(BaseModel):
    daily_run_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    min_market_cap: float = Field(ge=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app_db = AppDatabase(settings.app_db_path)
    engine = DataEngine(settings)
    auth = AuthService(app_db, settings.cookie_secure)
    auth.bootstrap(settings.admin_username, settings.admin_password)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="Sequoia-X API",
        version="2.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )
    app.state.settings = settings
    app.state.db = app_db
    app.state.engine = engine
    app.state.auth = auth
    app.state.ledger = LedgerService(app_db)
    app.state.backtest = BacktestService(app_db, engine)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "market_latest": engine.get_latest_trade_date(),
            "app_db": settings.app_db_path,
        }

    @app.post("/api/v1/auth/login")
    def login(payload: LoginInput, request: Request, response: Response) -> dict[str, Any]:
        return auth.login(payload.username, payload.password, request, response)

    @app.post("/api/v1/auth/logout")
    def logout(
        request: Request,
        response: Response,
        _: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, bool]:
        auth.logout(request, response)
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def me(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return {"username": user["username"], "csrf_token": user["csrf_token"]}

    @app.get("/api/v1/dashboard")
    def dashboard(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        run = app_db.query_one("SELECT * FROM pipeline_run ORDER BY trade_date DESC,id DESC LIMIT 1")
        if run is None:
            return {"run": None, "summary": None, "accounts": [], "data_stale": True}
        report = app_db.query_one("SELECT summary_json FROM daily_report WHERE run_id=?", (run["id"],))
        accounts = app_db.query_all("SELECT * FROM account ORDER BY id")
        portfolios = [app.state.ledger.portfolio(int(account["id"])) for account in accounts]
        return {
            "run": run,
            "summary": json.loads(report["summary_json"]) if report else None,
            "accounts": [{**account, "portfolio": portfolio} for account, portfolio in zip(accounts, portfolios)],
            "data_stale": not bool(run["data_fresh"]),
        }

    @app.get("/api/v1/candidates")
    def candidates(
        trade_date: str | None = None,
        zone: str | None = None,
        min_score: int = Query(0, ge=0, le=8),
        strategy: str | None = None,
        lifecycle: str | None = None,
        account_id: int | None = None,
        _: dict[str, Any] = Depends(require_user),
    ) -> list[dict[str, Any]]:
        if trade_date is None:
            latest = app_db.query_one("SELECT MAX(trade_date) trade_date FROM candidate")
            trade_date = latest["trade_date"] if latest else None
        if trade_date is None:
            return []
        clauses = ["c.trade_date=?", "c.total_score>=?"]
        params: list[Any] = [trade_date, min_score]
        if zone:
            clauses.append("c.zone=?")
            params.append(zone)
        if lifecycle:
            clauses.append("c.lifecycle_status=?")
            params.append(lifecycle)
        if strategy:
            clauses.append("c.strategies_json LIKE ?")
            params.append(f'%"{strategy}"%')
        rows = app_db.query_all(
            "SELECT c.*,p.id plan_id,p.status plan_status,p.current_entry_low,p.current_entry_high,"
            "p.current_stop_price,p.current_zone,r.data_fresh FROM candidate c "
            "JOIN trade_plan p ON p.candidate_id=c.id JOIN pipeline_run r ON r.id=c.run_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.total_score DESC,c.consensus_count DESC,c.symbol",
            tuple(params),
        )
        for row in rows:
            row["strategies"] = json.loads(row.pop("strategies_json"))
        if account_id is not None:
            portfolio_data = app.state.ledger.portfolio(account_id)
            _, active_rule = app_db.active_rule()
            held = {
                position["symbol"]: position for position in portfolio_data["positions"]
            }
            for row in rows:
                entry_low = row.get("current_entry_low")
                entry_high = row.get("current_entry_high")
                entry_price = (
                    (float(entry_low) + float(entry_high)) / 2
                    if entry_low is not None and entry_high is not None
                    else None
                )
                current = held.get(row["symbol"])
                row["suggested_quantity"] = (
                    suggested_quantity(
                        equity=float(portfolio_data["equity"]),
                        cash=float(portfolio_data["cash"]),
                        current_market_value=float(portfolio_data["market_value"]),
                        open_positions=len(portfolio_data["positions"]),
                        symbol_market_value=float(current["market_value"]) if current else 0,
                        entry_price=entry_price,
                        stop_price=row.get("current_stop_price"),
                        zone=PositionZone(row["current_zone"]),
                        rule=active_rule,
                    )
                    if row["data_fresh"]
                    else 0
                )
        return rows

    @app.get("/api/v1/candidates/{candidate_id}")
    def candidate_detail(candidate_id: int, _: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        row = app_db.query_one(
            "SELECT c.*,p.id plan_id,p.status plan_status,p.current_entry_low,p.current_entry_high,"
            "p.current_stop_price,p.current_zone,p.note FROM candidate c "
            "JOIN trade_plan p ON p.candidate_id=c.id WHERE c.id=?",
            (candidate_id,),
        )
        if row is None:
            raise HTTPException(404, "候选不存在")
        row["strategies"] = json.loads(row.pop("strategies_json"))
        row["revisions"] = app_db.query_all(
            "SELECT * FROM trade_plan_revision WHERE plan_id=? ORDER BY id DESC", (row["plan_id"],)
        )
        return row

    @app.put("/api/v1/candidates/{candidate_id}/status")
    def update_candidate_status(
        candidate_id: int,
        payload: CandidateStatusInput,
        user: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, bool]:
        with app_db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE candidate SET lifecycle_status=?,updated_at=? WHERE id=?",
                (payload.status, utc_now(), candidate_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "候选不存在")
        app_db.audit(user["username"], "UPDATE_CANDIDATE_STATUS", "candidate", candidate_id)
        return {"ok": True}

    @app.put("/api/v1/plans/{plan_id}")
    def revise_plan(
        plan_id: int,
        payload: PlanRevisionInput,
        user: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, bool]:
        if payload.stop_price >= payload.entry_low or payload.entry_low >= payload.entry_high:
            raise HTTPException(422, "必须满足止损价 < 买入下沿 < 买入上沿")
        with app_db.transaction() as conn:
            exists = conn.execute("SELECT id FROM trade_plan WHERE id=?", (plan_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "计划不存在")
            conn.execute(
                "INSERT INTO trade_plan_revision(plan_id,entry_low,entry_high,stop_price,zone,note,reason,"
                "actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    plan_id, payload.entry_low, payload.entry_high, payload.stop_price,
                    payload.zone, payload.note, payload.reason, user["username"], utc_now(),
                ),
            )
            conn.execute(
                "UPDATE trade_plan SET current_entry_low=?,current_entry_high=?,current_stop_price=?,"
                "current_zone=?,note=?,updated_at=? WHERE id=?",
                (
                    payload.entry_low, payload.entry_high, payload.stop_price, payload.zone,
                    payload.note, utc_now(), plan_id,
                ),
            )
        app_db.audit(user["username"], "REVISE_PLAN", "trade_plan", plan_id)
        return {"ok": True}

    @app.get("/api/v1/stocks/{symbol}")
    def stock_detail(symbol: str, _: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        with sqlite3.connect(settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
                "WHERE symbol=? ORDER BY date DESC LIMIT 260",
                (symbol,),
            ).fetchall()
        bars = [dict(row) for row in reversed(rows)]
        snapshot = app_db.query_one(
            "SELECT * FROM market_snapshot WHERE symbol=? ORDER BY date DESC LIMIT 1", (symbol,)
        )
        profile = app_db.query_one("SELECT * FROM stock_profile WHERE symbol=?", (symbol,))
        factor = 1.0
        if bars and snapshot:
            match = next((bar for bar in bars if bar["date"] == snapshot["date"]), None)
            if match and match["close"]:
                factor = float(snapshot["close"]) / float(match["close"])
        for bar in bars:
            for field in ("open", "high", "low", "close"):
                bar[field] = round(float(bar[field]) * factor, 3)
        return {"symbol": symbol, "profile": profile, "snapshot": snapshot, "bars": bars}

    @app.get("/api/v1/accounts")
    def accounts(_: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        rows = app_db.query_all("SELECT * FROM account ORDER BY id")
        return [{**row, "portfolio": app.state.ledger.portfolio(int(row["id"]))} for row in rows]

    @app.post("/api/v1/accounts")
    def create_account(
        payload: AccountInput, user: dict[str, Any] = Depends(require_csrf)
    ) -> dict[str, int]:
        try:
            account_id = app.state.ledger.create_account(**payload.model_dump())
        except (LedgerError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, str(exc)) from exc
        app_db.audit(user["username"], "CREATE_ACCOUNT_API", "account", account_id)
        return {"id": account_id}

    @app.post("/api/v1/accounts/{account_id}/fills")
    def add_fill(
        account_id: int,
        payload: FillInput,
        _: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, Any]:
        try:
            return app.state.ledger.add_fill(account_id=account_id, **payload.model_dump())
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/accounts/{account_id}/cash-events")
    def add_cash_event(
        account_id: int,
        payload: CashEventInput,
        _: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, int]:
        try:
            event_id = app.state.ledger.add_cash_event(account_id, **payload.model_dump())
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": event_id}

    @app.get("/api/v1/accounts/{account_id}/portfolio")
    def portfolio(account_id: int, _: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return app.state.ledger.portfolio(account_id)

    @app.get("/api/v1/reports")
    def reports(_: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        rows = app_db.query_all(
            "SELECT id,run_id,trade_date,title,summary_json,created_at FROM daily_report ORDER BY trade_date DESC"
        )
        for row in rows:
            row["summary"] = json.loads(row.pop("summary_json"))
        return rows

    @app.get("/api/v1/reports/{report_id}/html", response_class=HTMLResponse)
    def report_html(report_id: int, _: dict[str, Any] = Depends(require_user)) -> str:
        row = app_db.query_one("SELECT html FROM daily_report WHERE id=?", (report_id,))
        if row is None:
            raise HTTPException(404, "日报不存在")
        return str(row["html"])

    @app.get("/api/v1/runs")
    def runs(_: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        return app_db.query_all("SELECT * FROM pipeline_run ORDER BY trade_date DESC,id DESC LIMIT 100")

    @app.get("/api/v1/runs/status")
    def daily_run_status(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return get_daily_job(app_db)

    @app.post("/api/v1/runs/trigger", status_code=202)
    def trigger_daily_run(user: dict[str, Any] = Depends(require_csrf)) -> dict[str, Any]:
        try:
            job = request_daily_job(app_db, user["username"])
        except DailyJobBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        app_db.audit(user["username"], "TRIGGER_DAILY_RUN", detail=job)
        return job

    @app.get("/api/v1/rules")
    def rules(_: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        rows = app_db.query_all("SELECT * FROM rule_version ORDER BY id DESC")
        for row in rows:
            row["config"] = json.loads(row.pop("config_json"))
        return rows

    @app.post("/api/v1/rules")
    def create_rule(
        payload: RuleInput, user: dict[str, Any] = Depends(require_csrf)
    ) -> dict[str, int]:
        try:
            config = RuleConfig.from_dict(payload.config).to_dict()
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"规则配置无效：{exc}") from exc
        with app_db.transaction() as conn:
            conn.execute("UPDATE rule_version SET is_active=0 WHERE is_active=1")
            cursor = conn.execute(
                "INSERT INTO rule_version(name,config_json,is_active,created_at) VALUES (?,?,1,?)",
                (payload.name, json.dumps(config), utc_now()),
            )
            rule_id = int(cursor.lastrowid)
        app_db.audit(user["username"], "CREATE_RULE", "rule_version", rule_id)
        return {"id": rule_id}

    @app.get("/api/v1/settings")
    def read_settings(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return {
            "daily_run_time": app_db.get_setting("daily_run_time", settings.daily_run_time),
            "min_market_cap": float(app_db.get_setting("min_market_cap", str(settings.min_market_cap))),
            "public_base_url": settings.public_base_url,
            "supported_strategies": sorted(SUPPORTED_STRATEGIES),
        }

    @app.put("/api/v1/settings")
    def update_settings(
        payload: SettingsInput, user: dict[str, Any] = Depends(require_csrf)
    ) -> dict[str, bool]:
        app_db.set_setting("daily_run_time", payload.daily_run_time)
        app_db.set_setting("min_market_cap", str(payload.min_market_cap))
        app_db.audit(user["username"], "UPDATE_SETTINGS", detail=payload.model_dump())
        return {"ok": True}

    @app.post("/api/v1/backtests")
    def create_backtest(
        payload: BacktestInput,
        tasks: BackgroundTasks,
        _: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, int]:
        fee = {
            "commission_rate": payload.commission_rate,
            "minimum_commission": payload.minimum_commission,
            "stamp_duty_rate": payload.stamp_duty_rate,
            "transfer_fee_rate": payload.transfer_fee_rate,
        }
        try:
            run_id = app.state.backtest.create_run(
                strategy_name=payload.strategy_name,
                start_date=payload.start_date,
                end_date=payload.end_date,
                initial_cash=payload.initial_cash,
                fee=fee,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        tasks.add_task(app.state.backtest.execute, run_id)
        return {"id": run_id}

    @app.get("/api/v1/backtests")
    def backtests(_: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        rows = app_db.query_all("SELECT * FROM backtest_run ORDER BY id DESC LIMIT 100")
        for row in rows:
            row["metrics"] = json.loads(row["metrics_json"]) if row.get("metrics_json") else None
        return rows

    @app.get("/api/v1/backtests/{run_id}")
    def backtest_detail(run_id: int, _: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        run = app_db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
        if run is None:
            raise HTTPException(404, "回测不存在")
        run["metrics"] = json.loads(run["metrics_json"]) if run.get("metrics_json") else None
        run["trades"] = app_db.query_all(
            "SELECT * FROM backtest_trade WHERE backtest_run_id=? ORDER BY entry_date,symbol", (run_id,)
        )
        run["equity"] = app_db.query_all(
            "SELECT date,equity,benchmark FROM backtest_equity WHERE backtest_run_id=? ORDER BY date", (run_id,)
        )
        return run

    return app


app = create_app()
