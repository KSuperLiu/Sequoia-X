"""独立应用数据库：认证、信号、计划、组合、报告和回测。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sequoia_x.app.domain import RuleConfig

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_rule
ON rule_version(is_active) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS admin_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES admin_user(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    succeeded INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempt_ip_time
ON login_attempt(ip_address, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_profile (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    industry TEXT,
    is_st INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshot (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    turnover REAL,
    pe_ttm REAL,
    pb_mrq REAL,
    trade_status INTEGER NOT NULL DEFAULT 1,
    is_st INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON market_snapshot(date);

CREATE TABLE IF NOT EXISTS pipeline_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    rule_version_id INTEGER NOT NULL REFERENCES rule_version(id),
    status TEXT NOT NULL,
    data_fresh INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(trade_date, rule_version_id)
);

CREATE TABLE IF NOT EXISTS strategy_signal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES pipeline_run(id) ON DELETE CASCADE,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    raw_selected INTEGER NOT NULL,
    filtered_selected INTEGER NOT NULL,
    dropped_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, symbol, strategy_name)
);

CREATE TABLE IF NOT EXISTS candidate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES pipeline_run(id) ON DELETE CASCADE,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    industry TEXT,
    strategies_json TEXT NOT NULL,
    consensus_count INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    drawdown_60 REAL,
    rebound_60 REAL,
    ma10 REAL,
    ma20 REAL,
    volume_ratio REAL,
    atr14 REAL,
    score_drawdown INTEGER NOT NULL,
    score_rebound INTEGER NOT NULL,
    score_ma INTEGER NOT NULL,
    score_volume INTEGER NOT NULL,
    total_score INTEGER NOT NULL,
    real_close REAL,
    pe_ttm REAL,
    pb_mrq REAL,
    entry_low REAL,
    entry_high REAL,
    stop_price REAL,
    zone TEXT NOT NULL,
    veto_reason TEXT,
    rationale TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'NEW',
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    mfe REAL,
    mae REAL,
    hit_entry INTEGER NOT NULL DEFAULT 0,
    hit_stop INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_candidate_date_score
ON candidate(trade_date, total_score DESC);

CREATE TABLE IF NOT EXISTS trade_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL UNIQUE REFERENCES candidate(id) ON DELETE CASCADE,
    account_id INTEGER REFERENCES account(id),
    original_entry_low REAL,
    original_entry_high REAL,
    original_stop_price REAL,
    original_zone TEXT NOT NULL,
    current_entry_low REAL,
    current_entry_high REAL,
    current_stop_price REAL,
    current_zone TEXT NOT NULL,
    suggested_quantity INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_plan_revision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES trade_plan(id) ON DELETE CASCADE,
    entry_low REAL,
    entry_high REAL,
    stop_price REAL,
    zone TEXT NOT NULL,
    note TEXT,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    commission_rate REAL NOT NULL,
    minimum_commission REAL NOT NULL,
    stamp_duty_rate REAL NOT NULL,
    transfer_fee_rate REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    amount REAL NOT NULL,
    trade_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_fill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    plan_id INTEGER REFERENCES trade_plan(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL,
    stamp_duty REAL NOT NULL,
    transfer_fee REAL NOT NULL,
    trade_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fill_account_symbol_date
ON trade_fill(account_id, symbol, trade_date, id);

CREATE TABLE IF NOT EXISTS daily_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE REFERENCES pipeline_run(id) ON DELETE CASCADE,
    trade_date TEXT NOT NULL,
    title TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    html TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    rule_version_id INTEGER NOT NULL REFERENCES rule_version(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    fee_json TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_run_id INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    stop_price REAL NOT NULL,
    exit_date TEXT NOT NULL,
    exit_price REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    fees REAL NOT NULL,
    pnl REAL NOT NULL,
    return_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_equity (
    backtest_run_id INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    equity REAL NOT NULL,
    benchmark REAL,
    PRIMARY KEY(backtest_run_id, date)
);

CREATE TABLE IF NOT EXISTS watchlist_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    group_name TEXT NOT NULL DEFAULT '默认分组',
    note TEXT NOT NULL DEFAULT '',
    target_price REAL,
    watch_price REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_snapshot (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    equity REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    drawdown REAL NOT NULL,
    position_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(account_id, date)
);

CREATE TABLE IF NOT EXISTS position_risk (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    stop_price REAL,
    note TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, symbol)
);

CREATE TABLE IF NOT EXISTS trade_fill_reversal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id INTEGER NOT NULL UNIQUE REFERENCES trade_fill(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_event_reversal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cash_event_id INTEGER NOT NULL UNIQUE REFERENCES cash_event(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    current_stage TEXT,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    exit_code INTEGER,
    retry_of INTEGER REFERENCES job_run(id)
);
CREATE INDEX IF NOT EXISTS idx_job_run_status_id ON job_run(status, id);

CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_run_id INTEGER NOT NULL REFERENCES job_run(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_log_run_id ON job_log(job_run_id, id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AppDatabase:
    """轻量 SQLite 存储，所有连接使用 WAL、外键和忙等待。"""

    def __init__(self, path: str) -> None:
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migration(version,name,applied_at) VALUES (1,?,?)",
                ("professional_workbench", utc_now()),
            )
            conn.commit()
        self.ensure_default_rule()
        self.set_default("daily_run_time", "18:30")
        self.set_default("min_market_cap", "5000000000")

    def ensure_default_rule(self) -> int:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM rule_version WHERE is_active=1"
            ).fetchone()
            if row:
                return int(row["id"])
            cursor = conn.execute(
                "INSERT INTO rule_version(name, config_json, is_active, created_at) "
                "VALUES (?, ?, 1, ?)",
                ("四维评分 v1", json.dumps(RuleConfig().to_dict()), utc_now()),
            )
            return int(cursor.lastrowid)

    def active_rule(self) -> tuple[int, RuleConfig]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, config_json FROM rule_version WHERE is_active=1"
            ).fetchone()
        if row is None:
            rule_id = self.ensure_default_rule()
            return rule_id, RuleConfig()
        return int(row["id"]), RuleConfig.from_dict(json.loads(row["config_json"]))

    def set_default(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO app_setting(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, utc_now()),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO app_setting(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, utc_now()),
            )

    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def audit(
        self,
        actor: str,
        action: str,
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log(actor, action, entity_type, entity_id, detail_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    actor,
                    action,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    json.dumps(detail or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
