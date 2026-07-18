"""Sequoia-X FastAPI 服务。"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from sequoia_x.app.auth import (
    AuthService,
    optional_user,
    require_admin,
    require_admin_csrf,
    require_csrf,
    require_user,
)
from sequoia_x.app.backtest import SUPPORTED_STRATEGIES, BacktestService
from sequoia_x.app.backup import backup_sqlite
from sequoia_x.app.daily_job import get_daily_job
from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.domain import PositionZone, RuleConfig
from sequoia_x.app.jobs import (
    JobBusyError,
    JobCancellationError,
    enqueue_job,
    get_job,
    latest_job,
    request_job_cancel,
)
from sequoia_x.app.ledger import LedgerError, LedgerService
from sequoia_x.app.scoring import suggested_quantity
from sequoia_x.core.config import Settings, get_settings
from sequoia_x.data.engine import DataEngine


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=200)


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=200)


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


class WatchlistInput(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    group_name: str = Field(default="默认分组", min_length=1, max_length=40)
    note: str = Field(default="", max_length=500)
    target_price: float | None = Field(default=None, gt=0)
    watch_price: float | None = Field(default=None, gt=0)


class WatchlistPatch(BaseModel):
    group_name: str = Field(default="默认分组", min_length=1, max_length=40)
    note: str = Field(default="", max_length=500)
    target_price: float | None = Field(default=None, gt=0)
    watch_price: float | None = Field(default=None, gt=0)


class PlanStatusInput(BaseModel):
    status: Literal["DRAFT", "READY", "CANCELLED", "EXPIRED"]
    account_id: int | None = None


class ReversalInput(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class PositionRiskInput(BaseModel):
    stop_price: float | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


class JobInput(BaseModel):
    job_type: Literal["DAILY_UPDATE", "REFRESH_MARKET_CAP", "BACKFILL"]
    confirmation: str = ""


class PasswordInput(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


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
        version="2.2.0",
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

    def account_for_user(account_id: int, user: dict[str, Any]) -> dict[str, Any]:
        account = app_db.query_one("SELECT * FROM account WHERE id=?", (account_id,))
        if account is None:
            raise HTTPException(404, "账户不存在")
        if user.get("role") != "ADMIN" and account.get("owner_user_id") != user.get("user_id"):
            raise HTTPException(404, "账户不存在")
        return account

    def visible_accounts(user: dict[str, Any] | None) -> list[dict[str, Any]]:
        if user is None:
            return []
        if user.get("role") == "ADMIN":
            return app_db.query_all("SELECT * FROM account ORDER BY id")
        return app_db.query_all(
            "SELECT * FROM account WHERE owner_user_id=? ORDER BY id", (user["user_id"],)
        )

    def public_account(account: dict[str, Any]) -> dict[str, Any]:
        return {**account, "name": account.get("display_name") or account["name"]}

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

    @app.post("/api/v1/auth/register", status_code=201)
    def register(
        payload: RegisterInput, request: Request, response: Response
    ) -> dict[str, Any]:
        auth.register(payload.username, payload.password)
        return auth.login(payload.username.strip(), payload.password, request, response)

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
        return {
            "username": user["username"],
            "role": user["role"],
            "csrf_token": user["csrf_token"],
        }

    @app.get("/api/v1/dashboard")
    def dashboard(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
        run = app_db.query_one("SELECT * FROM pipeline_run ORDER BY trade_date DESC,id DESC LIMIT 1")
        report = (
            app_db.query_one("SELECT summary_json FROM daily_report WHERE run_id=?", (run["id"],))
            if run else None
        )
        accounts = visible_accounts(user)
        portfolios = [app.state.ledger.portfolio(int(account["id"])) for account in accounts]
        top_candidates = app_db.query_all(
            "SELECT c.id,c.symbol,c.name,c.industry,c.total_score,c.confidence,c.consensus_count,"
            "c.real_close,c.zone,p.id plan_id,p.status plan_status,p.current_entry_low,"
            "p.current_entry_high,p.current_stop_price,p.current_zone "
            "FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            "WHERE c.trade_date=(SELECT MAX(trade_date) FROM candidate) "
            "ORDER BY CASE WHEN p.current_zone='LEFT' THEN 0 WHEN p.current_zone='MIDDLE' THEN 1 ELSE 2 END,"
            "c.total_score DESC,c.consensus_count DESC LIMIT 8"
        )
        risks: list[dict[str, Any]] = []
        for account, portfolio_data in zip(accounts, portfolios):
            if float(portfolio_data["total_weight"]) > 0.60:
                account_name = account.get("display_name") or account["name"]
                risks.append({"level": "HIGH", "message": f"{account_name} 总仓位超过 60%", "to": "/portfolio"})
            for position in portfolio_data["positions"]:
                distance = position.get("stop_distance")
                if distance is not None and float(distance) <= 0.03:
                    risks.append({
                        "level": "HIGH" if float(distance) <= 0 else "MEDIUM",
                        "message": f"{position['symbol']} 距止损仅 {float(distance) * 100:.1f}%",
                        "to": f"/stocks/{position['symbol']}",
                    })
        if run is None or not bool(run["data_fresh"]):
            risks.insert(0, {"level": "HIGH", "message": "行情数据未通过新鲜度校验，仓位建议已关闭", "to": "/tasks"})
        is_admin = bool(user and user.get("role") == "ADMIN")
        recent_activity = (
            app_db.query_all(
                "SELECT actor,action,entity_type,entity_id,created_at FROM audit_log "
                "ORDER BY id DESC LIMIT 10"
            )
            if is_admin else []
        )
        return {
            "run": run,
            "summary": json.loads(report["summary_json"]) if report else None,
            "accounts": [
                {**public_account(account), "portfolio": portfolio}
                for account, portfolio in zip(accounts, portfolios)
            ],
            "data_stale": run is None or not bool(run["data_fresh"]),
            "top_candidates": top_candidates,
            "risk_alerts": risks[:10],
            "recent_activity": recent_activity,
            "latest_job": latest_job(app_db) if is_admin else None,
            "ready_plan_count": int((app_db.query_one("SELECT COUNT(*) count FROM trade_plan WHERE status='READY'") or {"count": 0})["count"]),
        }

    @app.get("/api/v1/candidates")
    def candidates(
        trade_date: str | None = None,
        zone: str | None = None,
        min_score: int = Query(0, ge=0, le=8),
        strategy: str | None = None,
        lifecycle: str | None = None,
        account_id: int | None = None,
        user: dict[str, Any] | None = Depends(optional_user),
    ) -> list[dict[str, Any]]:
        if trade_date is None:
            latest = app_db.query_one("SELECT MAX(trade_date) trade_date FROM candidate")
            trade_date = latest["trade_date"] if latest else None
        if trade_date is None:
            return []
        clauses = ["c.trade_date=?", "c.total_score>=?"]
        params: list[Any] = [trade_date, min_score]
        if zone:
            zones = [value.strip() for value in zone.split(",") if value.strip()]
            if not zones or any(value not in PositionZone._value2member_map_ for value in zones):
                raise HTTPException(422, "无效的位置筛选")
            clauses.append(f"c.zone IN ({','.join('?' for _ in zones)})")
            params.extend(zones)
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
            if user is None:
                raise HTTPException(401, "登录后才可按组合计算仓位")
            account_for_user(account_id, user)
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

    @app.get("/api/v1/candidates/search")
    def candidate_search(
        trade_date: str | None = None,
        q: str | None = None,
        zone: str | None = None,
        min_score: int = Query(0, ge=0, le=8),
        strategy: str | None = None,
        lifecycle: str | None = None,
        confidence: str | None = None,
        plan_status: str | None = None,
        account_id: int | None = None,
        sort: str = "score_desc",
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        user: dict[str, Any] | None = Depends(optional_user),
    ) -> dict[str, Any]:
        if trade_date is None:
            latest = app_db.query_one("SELECT MAX(trade_date) trade_date FROM candidate")
            trade_date = latest["trade_date"] if latest else None
        if trade_date is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "facets": {}}
        clauses = ["c.trade_date=?", "c.total_score>=?"]
        params: list[Any] = [trade_date, min_score]
        if zone:
            zones = [value.strip() for value in zone.split(",") if value.strip()]
            if not zones or any(value not in PositionZone._value2member_map_ for value in zones):
                raise HTTPException(422, "无效的位置筛选")
            clauses.append(f"p.current_zone IN ({','.join('?' for _ in zones)})")
            params.extend(zones)
        filters = {
            "lifecycle": ("c.lifecycle_status=?", lifecycle),
            "confidence": ("c.confidence=?", confidence),
            "plan_status": ("p.status=?", plan_status),
        }
        for _, (sql, value) in filters.items():
            if value:
                clauses.append(sql)
                params.append(value)
        if strategy:
            clauses.append("c.strategies_json LIKE ?")
            params.append(f'%"{strategy}"%')
        if q:
            clauses.append("(c.symbol LIKE ? OR c.name LIKE ?)")
            params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
        where = " AND ".join(clauses)
        order_by = {
            "score_desc": "c.total_score DESC,c.consensus_count DESC,c.symbol",
            "score_asc": "c.total_score,c.symbol",
            "price_desc": "c.real_close DESC,c.symbol",
            "date_desc": "c.trade_date DESC,c.total_score DESC",
        }.get(sort, "c.total_score DESC,c.consensus_count DESC,c.symbol")
        total_row = app_db.query_one(
            "SELECT COUNT(*) count FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            f"WHERE {where}", tuple(params),
        )
        rows = app_db.query_all(
            "SELECT c.*,p.id plan_id,p.account_id,p.status plan_status,p.current_entry_low,"
            "p.current_entry_high,p.current_stop_price,p.current_zone,r.data_fresh "
            "FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            "JOIN pipeline_run r ON r.id=c.run_id "
            f"WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            tuple([*params, page_size, (page - 1) * page_size]),
        )
        if account_id:
            if user is None:
                raise HTTPException(401, "登录后才可按组合计算仓位")
            account_for_user(account_id, user)
        portfolio_data = app.state.ledger.portfolio(account_id) if account_id else None
        _, active_rule = app_db.active_rule()
        held = {item["symbol"]: item for item in portfolio_data["positions"]} if portfolio_data else {}
        for row in rows:
            row["strategies"] = json.loads(row.pop("strategies_json"))
            if portfolio_data:
                low, high = row.get("current_entry_low"), row.get("current_entry_high")
                entry = (float(low) + float(high)) / 2 if low and high else None
                current = held.get(row["symbol"])
                row["suggested_quantity"] = suggested_quantity(
                    equity=float(portfolio_data["equity"]), cash=float(portfolio_data["cash"]),
                    current_market_value=float(portfolio_data["market_value"]),
                    open_positions=len(portfolio_data["positions"]),
                    symbol_market_value=float(current["market_value"]) if current else 0,
                    entry_price=entry, stop_price=row.get("current_stop_price"),
                    zone=PositionZone(row["current_zone"]), rule=active_rule,
                ) if row["data_fresh"] else 0
        facet_rows = app_db.query_all(
            "SELECT p.current_zone zone,COUNT(*) count FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            "WHERE c.trade_date=? GROUP BY p.current_zone", (trade_date,),
        )
        return {
            "items": rows, "total": int(total_row["count"] if total_row else 0),
            "page": page, "page_size": page_size,
            "facets": {"zones": {item["zone"]: item["count"] for item in facet_rows}, "trade_date": trade_date},
        }

    @app.get("/api/v1/candidates/export.csv")
    def export_candidates(
        trade_date: str | None = None,
        zone: str | None = None,
        min_score: int = Query(0, ge=0, le=8),
    ) -> Response:
        result = candidate_search(trade_date=trade_date, zone=zone, min_score=min_score, page=1, page_size=100)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["日期", "代码", "名称", "行业", "分数", "共振数", "位置", "现价", "买入下沿", "买入上沿", "止损", "状态"])
        for row in result["items"]:
            writer.writerow([row["trade_date"], row["symbol"], row.get("name"), row.get("industry"), row["total_score"], row["consensus_count"], row["current_zone"], row.get("real_close"), row.get("current_entry_low"), row.get("current_entry_high"), row.get("current_stop_price"), row["lifecycle_status"]])
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=candidates.csv"})

    @app.get("/api/v1/candidates/{candidate_id}")
    def candidate_detail(candidate_id: int) -> dict[str, Any]:
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
        user: dict[str, Any] = Depends(require_admin_csrf),
    ) -> dict[str, bool]:
        current = app_db.query_one("SELECT lifecycle_status FROM candidate WHERE id=?", (candidate_id,))
        if current is None:
            raise HTTPException(404, "候选不存在")
        allowed = {
            "NEW": {"WATCHING", "DROPPED"},
            "WATCHING": {"PLANNED", "DROPPED", "EXPIRED"},
            "PLANNED": {"WATCHING", "DROPPED", "EXPIRED"},
            "DROPPED": {"WATCHING"},
            "EXPIRED": {"WATCHING"},
            "BOUGHT": set(),
        }
        if payload.status == "BOUGHT":
            raise HTTPException(422, "BOUGHT 状态只能由有效买入成交产生")
        if payload.status != current["lifecycle_status"] and payload.status not in allowed.get(str(current["lifecycle_status"]), set()):
            raise HTTPException(422, f"不允许从 {current['lifecycle_status']} 变更为 {payload.status}")
        with app_db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE candidate SET lifecycle_status=?,updated_at=? WHERE id=?",
                (payload.status, utc_now(), candidate_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "候选不存在")
            if payload.status == "PLANNED":
                conn.execute(
                    "UPDATE trade_plan SET status='READY',updated_at=? WHERE candidate_id=? AND status='DRAFT'",
                    (utc_now(), candidate_id),
                )
        app_db.audit(user["username"], "UPDATE_CANDIDATE_STATUS", "candidate", candidate_id)
        return {"ok": True}

    @app.put("/api/v1/plans/{plan_id}")
    def revise_plan(
        plan_id: int,
        payload: PlanRevisionInput,
        user: dict[str, Any] = Depends(require_admin_csrf),
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

    @app.get("/api/v1/plans")
    def plans(
        status: str | None = None,
        q: str | None = None,
        zone: str | None = None,
        account_id: int | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        user: dict[str, Any] | None = Depends(optional_user),
    ) -> dict[str, Any]:
        if account_id:
            if user is None:
                raise HTTPException(401, "登录后才可筛选个人组合")
            account_for_user(account_id, user)
        clauses = ["1=1"]
        params: list[Any] = []
        for sql, value in (("p.status=?", status), ("p.current_zone=?", zone), ("p.account_id=?", account_id)):
            if value is not None and value != "":
                clauses.append(sql)
                params.append(value)
        if q:
            clauses.append("(c.symbol LIKE ? OR c.name LIKE ?)")
            params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
        where = " AND ".join(clauses)
        total = app_db.query_one(
            "SELECT COUNT(*) count FROM trade_plan p JOIN candidate c ON c.id=p.candidate_id "
            f"WHERE {where}", tuple(params),
        )
        rows = app_db.query_all(
            "SELECT p.*,c.symbol,c.name,c.industry,c.trade_date,c.total_score,c.confidence,"
            "c.consensus_count,c.real_close,c.lifecycle_status,r.data_fresh,"
            "COALESCE(a.display_name,a.name) account_name "
            "FROM trade_plan p JOIN candidate c ON c.id=p.candidate_id "
            "JOIN pipeline_run r ON r.id=c.run_id LEFT JOIN account a ON a.id=p.account_id "
            f"WHERE {where} ORDER BY c.trade_date DESC,c.total_score DESC,p.id DESC LIMIT ? OFFSET ?",
            tuple([*params, page_size, (page - 1) * page_size]),
        )
        _, active_rule = app_db.active_rule()
        portfolio_cache: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not user or user.get("role") != "ADMIN":
                row["account_id"] = None
                row["account_name"] = None
                row["suggested_quantity"] = 0
                continue
            assigned = int(row["account_id"]) if row.get("account_id") else None
            if assigned and row["data_fresh"]:
                portfolio_data = portfolio_cache.setdefault(assigned, app.state.ledger.portfolio(assigned))
                held = next((item for item in portfolio_data["positions"] if item["symbol"] == row["symbol"]), None)
                low, high = row.get("current_entry_low"), row.get("current_entry_high")
                entry = (float(low) + float(high)) / 2 if low and high else None
                row["suggested_quantity"] = suggested_quantity(
                    equity=float(portfolio_data["equity"]), cash=float(portfolio_data["cash"]),
                    current_market_value=float(portfolio_data["market_value"]),
                    open_positions=len(portfolio_data["positions"]),
                    symbol_market_value=float(held["market_value"]) if held else 0,
                    entry_price=entry, stop_price=row.get("current_stop_price"),
                    zone=PositionZone(row["current_zone"]), rule=active_rule,
                )
        return {"items": rows, "total": int(total["count"] if total else 0), "page": page, "page_size": page_size}

    @app.get("/api/v1/plans/{plan_id}")
    def plan_detail(
        plan_id: int, user: dict[str, Any] | None = Depends(optional_user)
    ) -> dict[str, Any]:
        row = app_db.query_one(
            "SELECT p.*,c.symbol,c.name,c.industry,c.trade_date,c.total_score,c.confidence,"
            "c.consensus_count,c.real_close,c.lifecycle_status,r.data_fresh,"
            "COALESCE(a.display_name,a.name) account_name "
            "FROM trade_plan p JOIN candidate c ON c.id=p.candidate_id "
            "JOIN pipeline_run r ON r.id=c.run_id LEFT JOIN account a ON a.id=p.account_id WHERE p.id=?",
            (plan_id,),
        )
        if row is None:
            raise HTTPException(404, "计划不存在")
        if not user or user.get("role") != "ADMIN":
            row["account_id"] = None
            row["account_name"] = None
            row["suggested_quantity"] = 0
        row["revisions"] = app_db.query_all(
            "SELECT * FROM trade_plan_revision WHERE plan_id=? ORDER BY id DESC", (plan_id,)
        )
        row["fills"] = app_db.query_all(
            "SELECT f.*,rv.id reversal_id FROM trade_fill f LEFT JOIN trade_fill_reversal rv ON rv.fill_id=f.id "
            "WHERE f.plan_id=? ORDER BY f.id DESC", (plan_id,),
        )
        return row

    @app.put("/api/v1/plans/{plan_id}/status")
    def update_plan_status(
        plan_id: int,
        payload: PlanStatusInput,
        user: dict[str, Any] = Depends(require_admin_csrf),
    ) -> dict[str, bool]:
        plan = app_db.query_one("SELECT * FROM trade_plan WHERE id=?", (plan_id,))
        if plan is None:
            raise HTTPException(404, "计划不存在")
        transitions = {
            "DRAFT": {"READY", "CANCELLED", "EXPIRED"},
            "READY": {"DRAFT", "CANCELLED", "EXPIRED"},
            "CANCELLED": {"DRAFT"},
            "EXPIRED": {"DRAFT"},
            "EXECUTED": set(),
        }
        if payload.status not in transitions.get(str(plan["status"]), set()) and payload.status != plan["status"]:
            raise HTTPException(422, f"不允许从 {plan['status']} 变更为 {payload.status}")
        with app_db.transaction() as conn:
            conn.execute(
                "UPDATE trade_plan SET status=?,account_id=COALESCE(?,account_id),updated_at=? WHERE id=?",
                (payload.status, payload.account_id, utc_now(), plan_id),
            )
            lifecycle = "PLANNED" if payload.status == "READY" else ("EXPIRED" if payload.status == "EXPIRED" else None)
            if lifecycle:
                conn.execute(
                    "UPDATE candidate SET lifecycle_status=?,updated_at=? WHERE id=?",
                    (lifecycle, utc_now(), plan["candidate_id"]),
                )
        app_db.audit(user["username"], "UPDATE_PLAN_STATUS", "trade_plan", plan_id, payload.model_dump())
        return {"ok": True}

    @app.get("/api/v1/stocks/search")
    def stock_search(
        q: str = Query(min_length=1, max_length=30),
        limit: int = Query(12, ge=1, le=50),
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol,name,market_cap,updated_at FROM stock_market_cap "
                "WHERE symbol LIKE ? OR name LIKE ? ORDER BY CASE WHEN symbol=? THEN 0 ELSE 1 END,market_cap DESC LIMIT ?",
                (f"%{q.strip()}%", f"%{q.strip()}%", q.strip(), limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            profile = app_db.query_one("SELECT industry,is_st FROM stock_profile WHERE symbol=?", (row["symbol"],))
            quote = app_db.query_one("SELECT close,date FROM market_snapshot WHERE symbol=? ORDER BY date DESC LIMIT 1", (row["symbol"],))
            row.update(profile or {})
            row["snapshot"] = quote
        return result

    @app.get("/api/v1/watchlist")
    def watchlist(
        user: dict[str, Any] | None = Depends(optional_user),
    ) -> list[dict[str, Any]]:
        if user is None:
            return []
        rows = app_db.query_all(
            "SELECT w.*,COALESCE(p.name,w.symbol) name,p.industry,s.close,s.date quote_date "
            "FROM watchlist_item w LEFT JOIN stock_profile p ON p.symbol=w.symbol "
            "LEFT JOIN market_snapshot s ON s.symbol=w.symbol AND s.date=(SELECT MAX(s2.date) FROM market_snapshot s2 WHERE s2.symbol=w.symbol) "
            "WHERE w.owner_user_id=? ORDER BY w.group_name,w.updated_at DESC",
            (user["user_id"],),
        )
        for row in rows:
            close = row.get("close")
            row["alert"] = (
                "TARGET" if close and row.get("target_price") and float(close) >= float(row["target_price"])
                else "WATCH" if close and row.get("watch_price") and float(close) <= float(row["watch_price"])
                else None
            )
        return rows

    @app.post("/api/v1/watchlist")
    def add_watchlist(payload: WatchlistInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, int]:
        now = utc_now()
        try:
            with app_db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO watchlist_item(owner_user_id,symbol,group_name,note,target_price,"
                    "watch_price,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        user["user_id"], payload.symbol, payload.group_name, payload.note,
                        payload.target_price, payload.watch_price, now, now,
                    ),
                )
                item_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "该股票已在自选股中") from exc
        app_db.audit(user["username"], "ADD_WATCHLIST", "watchlist_item", item_id, payload.model_dump())
        return {"id": item_id}

    @app.patch("/api/v1/watchlist/{item_id}")
    def edit_watchlist(item_id: int, payload: WatchlistPatch, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
        with app_db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE watchlist_item SET group_name=?,note=?,target_price=?,watch_price=?,"
                "updated_at=? WHERE id=? AND owner_user_id=?",
                (
                    payload.group_name, payload.note, payload.target_price,
                    payload.watch_price, utc_now(), item_id, user["user_id"],
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "自选股不存在")
        app_db.audit(user["username"], "UPDATE_WATCHLIST", "watchlist_item", item_id, payload.model_dump())
        return {"ok": True}

    @app.delete("/api/v1/watchlist/{item_id}", status_code=204)
    def delete_watchlist(item_id: int, user: dict[str, Any] = Depends(require_csrf)) -> Response:
        with app_db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM watchlist_item WHERE id=? AND owner_user_id=?",
                (item_id, user["user_id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "自选股不存在")
        app_db.audit(user["username"], "DELETE_WATCHLIST", "watchlist_item", item_id)
        return Response(status_code=204)

    @app.get("/api/v1/stocks/{symbol}")
    def stock_detail(
        symbol: str, user: dict[str, Any] | None = Depends(optional_user)
    ) -> dict[str, Any]:
        with sqlite3.connect(settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
                "WHERE symbol=? ORDER BY date DESC LIMIT 120",
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
        candidates_history = app_db.query_all(
            "SELECT c.*,p.id plan_id,p.status plan_status,p.current_entry_low,p.current_entry_high,"
            "p.current_stop_price,p.current_zone FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            "WHERE c.symbol=? ORDER BY c.trade_date DESC LIMIT 30", (symbol,),
        )
        for item in candidates_history:
            item["strategies"] = json.loads(item.pop("strategies_json"))
        user_accounts = visible_accounts(user)
        account_ids = [int(account["id"]) for account in user_accounts]
        fills: list[dict[str, Any]] = []
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            fills = app_db.query_all(
                "SELECT f.*,COALESCE(a.display_name,a.name) account_name,r.id reversal_id "
                "FROM trade_fill f JOIN account a ON a.id=f.account_id "
                "LEFT JOIN trade_fill_reversal r ON r.fill_id=f.id WHERE f.symbol=? "
                f"AND f.account_id IN ({placeholders}) "
                "ORDER BY f.trade_date DESC,f.id DESC LIMIT 50",
                tuple([symbol, *account_ids]),
            )
        watch = (
            app_db.query_one(
                "SELECT * FROM watchlist_item WHERE symbol=? AND owner_user_id=?",
                (symbol, user["user_id"]),
            )
            if user else None
        )
        positions = []
        for account in user_accounts:
            position = next((p for p in app.state.ledger.portfolio(int(account["id"]))["positions"] if p["symbol"] == symbol), None)
            if position:
                positions.append({
                    **position,
                    "account_id": account["id"],
                    "account_name": account.get("display_name") or account["name"],
                })
        return {"symbol": symbol, "profile": profile, "snapshot": snapshot, "bars": bars, "candidates": candidates_history, "fills": fills, "watchlist": watch, "positions": positions}

    @app.get("/api/v1/accounts")
    def accounts(
        user: dict[str, Any] | None = Depends(optional_user),
    ) -> list[dict[str, Any]]:
        rows = visible_accounts(user)
        return [
            {**public_account(row), "portfolio": app.state.ledger.portfolio(int(row["id"]))}
            for row in rows
        ]

    @app.post("/api/v1/accounts")
    def create_account(
        payload: AccountInput, user: dict[str, Any] = Depends(require_csrf)
    ) -> dict[str, int]:
        if user.get("role") != "ADMIN" and payload.account_type != "PAPER":
            raise HTTPException(403, "普通用户只能创建模拟组合")
        try:
            account_id = app.state.ledger.create_account(
                **payload.model_dump(),
                owner_user_id=int(user["user_id"]),
                actor=str(user["username"]),
            )
        except (LedgerError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, str(exc)) from exc
        app_db.audit(user["username"], "CREATE_ACCOUNT_API", "account", account_id)
        return {"id": account_id}

    @app.post("/api/v1/accounts/{account_id}/fills")
    def add_fill(
        account_id: int,
        payload: FillInput,
        user: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, Any]:
        account_for_user(account_id, user)
        if user.get("role") != "ADMIN" and payload.plan_id is not None:
            raise HTTPException(403, "普通用户请通过模拟组合手动录入成交")
        try:
            return app.state.ledger.add_fill(account_id=account_id, **payload.model_dump())
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/accounts/{account_id}/cash-events")
    def add_cash_event(
        account_id: int,
        payload: CashEventInput,
        user: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, int]:
        account_for_user(account_id, user)
        try:
            event_id = app.state.ledger.add_cash_event(account_id, **payload.model_dump())
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": event_id}

    @app.get("/api/v1/accounts/{account_id}/portfolio")
    def portfolio(
        account_id: int, user: dict[str, Any] = Depends(require_user)
    ) -> dict[str, Any]:
        account_for_user(account_id, user)
        return app.state.ledger.portfolio(account_id)

    @app.get("/api/v1/accounts/{account_id}/fills")
    def account_fills(
        account_id: int, user: dict[str, Any] = Depends(require_user)
    ) -> list[dict[str, Any]]:
        account_for_user(account_id, user)
        return app.state.ledger.list_fills(account_id)

    @app.get("/api/v1/accounts/{account_id}/cash-events")
    def account_cash_events(
        account_id: int, user: dict[str, Any] = Depends(require_user)
    ) -> list[dict[str, Any]]:
        account_for_user(account_id, user)
        return app.state.ledger.list_cash_events(account_id)

    @app.get("/api/v1/accounts/{account_id}/snapshots")
    def account_snapshots(
        account_id: int, user: dict[str, Any] = Depends(require_user)
    ) -> list[dict[str, Any]]:
        account_for_user(account_id, user)
        return app_db.query_all(
            "SELECT * FROM account_snapshot WHERE account_id=? ORDER BY date", (account_id,)
        )

    @app.post("/api/v1/accounts/{account_id}/fills/{fill_id}/reverse")
    def reverse_fill(account_id: int, fill_id: int, payload: ReversalInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, int]:
        account_for_user(account_id, user)
        try:
            reversal_id = app.state.ledger.reverse_fill(account_id, fill_id, payload.reason, user["username"])
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": reversal_id}

    @app.post("/api/v1/accounts/{account_id}/cash-events/{event_id}/reverse")
    def reverse_cash_event(account_id: int, event_id: int, payload: ReversalInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, int]:
        account_for_user(account_id, user)
        try:
            reversal_id = app.state.ledger.reverse_cash_event(account_id, event_id, payload.reason, user["username"])
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": reversal_id}

    @app.put("/api/v1/accounts/{account_id}/positions/{symbol}/risk")
    def update_position_risk(account_id: int, symbol: str, payload: PositionRiskInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
        account_for_user(account_id, user)
        try:
            app.state.ledger.set_position_risk(account_id, symbol, payload.stop_price, payload.note, user["username"])
        except LedgerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/v1/accounts/{account_id}/export/{kind}.csv")
    def export_account(
        account_id: int,
        kind: Literal["fills", "cash"],
        user: dict[str, Any] = Depends(require_user),
    ) -> Response:
        account_for_user(account_id, user)
        output = io.StringIO()
        writer = csv.writer(output)
        if kind == "fills":
            writer.writerow(["日期", "代码", "方向", "数量", "价格", "佣金", "印花税", "过户费", "备注", "是否冲正"])
            for row in app.state.ledger.list_fills(account_id):
                writer.writerow([row["trade_date"], row["symbol"], row["side"], row["quantity"], row["price"], row["commission"], row["stamp_duty"], row["transfer_fee"], row.get("note"), "是" if row.get("reversal_id") else "否"])
        else:
            writer.writerow(["日期", "类型", "金额", "备注", "是否冲正"])
            for row in app.state.ledger.list_cash_events(account_id):
                writer.writerow([row["trade_date"], row["event_type"], row["amount"], row.get("note"), "是" if row.get("reversal_id") else "否"])
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=account-{account_id}-{kind}.csv"})

    @app.get("/api/v1/reports")
    def reports() -> list[dict[str, Any]]:
        rows = app_db.query_all(
            "SELECT id,run_id,trade_date,title,summary_json,created_at FROM daily_report ORDER BY trade_date DESC"
        )
        for row in rows:
            row["summary"] = json.loads(row.pop("summary_json"))
        return rows

    @app.get("/api/v1/reports/{report_id}")
    def report_detail(report_id: int) -> dict[str, Any]:
        row = app_db.query_one("SELECT * FROM daily_report WHERE id=?", (report_id,))
        if row is None:
            raise HTTPException(404, "日报不存在")
        row["summary"] = json.loads(row.pop("summary_json"))
        row.pop("html", None)
        row["candidates"] = app_db.query_all(
            "SELECT c.id,c.symbol,c.name,c.industry,c.total_score,c.confidence,c.consensus_count,"
            "c.zone,c.return_1d,c.return_5d,c.return_20d,c.mfe,c.mae,c.hit_entry,c.hit_stop,"
            "p.status plan_status,p.current_zone FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id "
            "WHERE c.run_id=? ORDER BY c.total_score DESC,c.consensus_count DESC", (row["run_id"],),
        )
        return row

    @app.get("/api/v1/analytics/candidates")
    def candidate_analytics(
        group_by: Literal["strategy", "zone", "score", "confidence"] = "zone",
    ) -> list[dict[str, Any]]:
        expression = {
            "zone": "zone", "score": "CAST(total_score AS TEXT)", "confidence": "confidence",
            "strategy": "json_extract(strategies_json,'$[0]')",
        }[group_by]
        return app_db.query_all(
            f"SELECT {expression} label,COUNT(*) count,AVG(return_1d) avg_1d,AVG(return_5d) avg_5d,"
            "AVG(return_20d) avg_20d,AVG(mfe) avg_mfe,AVG(mae) avg_mae,AVG(hit_entry) entry_rate,"
            "AVG(hit_stop) stop_rate,AVG(CASE WHEN return_20d>0 THEN 1.0 ELSE 0.0 END) win_rate "
            "FROM candidate GROUP BY label ORDER BY count DESC"
        )

    @app.get("/api/v1/reports/{report_id}/html", response_class=HTMLResponse)
    def report_html(report_id: int) -> str:
        row = app_db.query_one("SELECT html FROM daily_report WHERE id=?", (report_id,))
        if row is None:
            raise HTTPException(404, "日报不存在")
        return str(row["html"])

    @app.get("/api/v1/runs")
    def runs(_: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
        return app_db.query_all("SELECT * FROM pipeline_run ORDER BY trade_date DESC,id DESC LIMIT 100")

    @app.get("/api/v1/runs/status")
    def daily_run_status(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        job = latest_job(app_db, "DAILY_UPDATE")
        return job or get_daily_job(app_db)

    @app.post("/api/v1/runs/trigger", status_code=202)
    def trigger_daily_run(user: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
        try:
            job = enqueue_job(app_db, "DAILY_UPDATE", user["username"])
        except JobBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        app_db.audit(user["username"], "TRIGGER_DAILY_RUN", detail=job)
        return job

    @app.get("/api/v1/jobs")
    def jobs(
        status: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        _: dict[str, Any] = Depends(require_admin),
    ) -> list[dict[str, Any]]:
        if status:
            return app_db.query_all("SELECT * FROM job_run WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return app_db.query_all("SELECT * FROM job_run ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/api/v1/jobs/{job_id}")
    def job_detail(job_id: int, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        job = get_job(app_db, job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        job["logs"] = app_db.query_all(
            "SELECT * FROM job_log WHERE job_run_id=? ORDER BY id DESC LIMIT 500", (job_id,)
        )
        return job

    @app.post("/api/v1/jobs", status_code=202)
    def create_job(payload: JobInput, user: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
        if payload.job_type == "BACKFILL" and payload.confirmation != "BACKFILL":
            raise HTTPException(422, "历史回填必须输入 BACKFILL 确认")
        try:
            job = enqueue_job(app_db, payload.job_type, user["username"])
        except JobBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        app_db.audit(user["username"], "CREATE_JOB", "job_run", job["id"], payload.model_dump())
        return job

    @app.post("/api/v1/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: int, user: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
        old = get_job(app_db, job_id)
        if old is None:
            raise HTTPException(404, "任务不存在")
        if old["status"] not in {"FAILED", "PARTIAL", "CANCELLED"}:
            raise HTTPException(422, "只有失败、部分成功或已取消的任务可以重试")
        try:
            return enqueue_job(app_db, str(old["job_type"]), user["username"], retry_of=job_id)
        except JobBusyError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: int, user: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
        if get_job(app_db, job_id) is None:
            raise HTTPException(404, "任务不存在")
        try:
            job = request_job_cancel(app_db, job_id, user["username"])
        except JobCancellationError as exc:
            raise HTTPException(422, str(exc)) from exc
        app_db.audit(
            user["username"], "CANCEL_JOB", "job_run", job_id,
            {"status": job.get("status"), "job_type": job.get("job_type")},
        )
        return job

    @app.get("/api/v1/rules")
    def rules(_: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
        rows = app_db.query_all("SELECT * FROM rule_version ORDER BY id DESC")
        for row in rows:
            row["config"] = json.loads(row.pop("config_json"))
        return rows

    @app.post("/api/v1/rules")
    def create_rule(
        payload: RuleInput, user: dict[str, Any] = Depends(require_admin_csrf)
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
    def read_settings(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        return {
            "daily_run_time": app_db.get_setting("daily_run_time", settings.daily_run_time),
            "min_market_cap": float(app_db.get_setting("min_market_cap", str(settings.min_market_cap))),
            "public_base_url": settings.public_base_url,
            "supported_strategies": sorted(SUPPORTED_STRATEGIES),
        }

    @app.put("/api/v1/settings")
    def update_settings(
        payload: SettingsInput, user: dict[str, Any] = Depends(require_admin_csrf)
    ) -> dict[str, bool]:
        app_db.set_setting("daily_run_time", payload.daily_run_time)
        app_db.set_setting("min_market_cap", str(payload.min_market_cap))
        app_db.audit(user["username"], "UPDATE_SETTINGS", detail=payload.model_dump())
        return {"ok": True}

    @app.get("/api/v1/system/health")
    def system_health(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        market_cap_date = None
        market_count = 0
        with sqlite3.connect(settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT MAX(updated_at) updated_at,COUNT(*) count FROM stock_market_cap").fetchone()
                market_cap_date = row["updated_at"]
                market_count = int(row["count"])
            except sqlite3.OperationalError:
                pass
        latest = engine.get_latest_trade_date()
        snapshot = app_db.query_one("SELECT MAX(date) date,MAX(updated_at) updated_at FROM market_snapshot")
        return {
            "market_latest": latest,
            "snapshot_latest": snapshot,
            "market_cap_updated_at": market_cap_date,
            "market_cap_count": market_count,
            "market_db_size": Path(settings.db_path).stat().st_size if Path(settings.db_path).exists() else 0,
            "app_db_size": Path(settings.app_db_path).stat().st_size if Path(settings.app_db_path).exists() else 0,
            "latest_job": latest_job(app_db),
            "watchlist_count": int((app_db.query_one("SELECT COUNT(*) count FROM watchlist_item") or {"count": 0})["count"]),
        }

    @app.get("/api/v1/audit-logs")
    def audit_logs(
        action: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        _: dict[str, Any] = Depends(require_admin),
    ) -> list[dict[str, Any]]:
        rows = app_db.query_all(
            "SELECT * FROM audit_log WHERE (? IS NULL OR action=?) ORDER BY id DESC LIMIT ?",
            (action, action, limit),
        )
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        return rows

    @app.get("/api/v1/backups")
    def backups(_: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for folder in (Path("backups/app"), Path("backups/market")):
            if not folder.exists():
                continue
            for item in folder.glob("*.db"):
                files.append({"name": item.name, "kind": folder.name, "size": item.stat().st_size, "created_at": item.stat().st_mtime})
        return sorted(files, key=lambda item: item["created_at"], reverse=True)

    @app.post("/api/v1/backups", status_code=201)
    def create_backup(user: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
        target = backup_sqlite(settings.app_db_path, "backups/app", "sequoia_app", 30)
        app_db.audit(user["username"], "CREATE_BACKUP", "backup", target.name)
        return {"name": target.name, "size": target.stat().st_size}

    @app.put("/api/v1/auth/password")
    def change_password(payload: PasswordInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
        auth.change_password(int(user["user_id"]), payload.current_password, payload.new_password)
        return {"ok": True}

    @app.post("/api/v1/backtests")
    def create_backtest(
        payload: BacktestInput,
        tasks: BackgroundTasks,
        _: dict[str, Any] = Depends(require_csrf),
    ) -> dict[str, int]:
        active_job = app_db.query_one(
            "SELECT id,job_type FROM job_run WHERE status IN ('PENDING','RUNNING') ORDER BY id LIMIT 1"
        )
        if active_job:
            raise HTTPException(409, f"数据维护任务 #{active_job['id']} 正在等待或执行，请完成后再运行回测")
        active_backtest = app_db.query_one(
            "SELECT id FROM backtest_run WHERE status IN ('PENDING','RUNNING') ORDER BY id LIMIT 1"
        )
        if active_backtest:
            raise HTTPException(409, f"已有回测任务 #{active_backtest['id']} 正在执行")
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
    def backtests() -> list[dict[str, Any]]:
        rows = app_db.query_all("SELECT * FROM backtest_run ORDER BY id DESC LIMIT 100")
        for row in rows:
            row["metrics"] = json.loads(row["metrics_json"]) if row.get("metrics_json") else None
        return rows

    @app.get("/api/v1/backtests/{run_id}")
    def backtest_detail(run_id: int) -> dict[str, Any]:
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

    @app.get("/api/v1/backtests/{run_id}/trades.csv")
    def export_backtest_trades(run_id: int) -> Response:
        if app_db.query_one("SELECT id FROM backtest_run WHERE id=?", (run_id,)) is None:
            raise HTTPException(404, "回测不存在")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["代码", "信号日", "买入日", "买入价", "数量", "止损价", "卖出日", "卖出价", "退出原因", "费用", "盈亏", "收益率"])
        for row in app_db.query_all("SELECT * FROM backtest_trade WHERE backtest_run_id=? ORDER BY entry_date,symbol", (run_id,)):
            writer.writerow([row["symbol"], row["signal_date"], row["entry_date"], row["entry_price"], row["quantity"], row["stop_price"], row["exit_date"], row["exit_price"], row["exit_reason"], row["fees"], row["pnl"], row["return_pct"]])
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=backtest-{run_id}.csv"})

    return app


app = create_app()
