"""模拟组合与人工实盘账本。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.domain import AccountType


class LedgerError(ValueError):
    pass


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    last_buy_date: str | None = None


class LedgerService:
    def __init__(self, db: AppDatabase) -> None:
        self.db = db

    def create_account(
        self,
        *,
        name: str,
        account_type: str,
        initial_cash: float,
        commission_rate: float,
        minimum_commission: float,
        stamp_duty_rate: float,
        transfer_fee_rate: float,
        owner_user_id: int | None = None,
        actor: str = "admin",
    ) -> int:
        if account_type not in {item.value for item in AccountType}:
            raise LedgerError("账户类型无效")
        values = [initial_cash, commission_rate, minimum_commission, stamp_duty_rate, transfer_fee_rate]
        if initial_cash <= 0 or any(value < 0 for value in values):
            raise LedgerError("初始资金必须大于 0，费率不得为负")
        now = utc_now()
        duplicate = self.db.query_one(
            "SELECT id FROM account WHERE owner_user_id=? AND display_name=?",
            (owner_user_id, name),
        )
        if duplicate:
            raise LedgerError("当前账号下已存在同名组合")
        internal_name = f"user-{owner_user_id}-{now}-{name}"
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO account(name,display_name,owner_user_id,account_type,initial_cash,"
                "commission_rate,minimum_commission,stamp_duty_rate,transfer_fee_rate,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    internal_name, name, owner_user_id, account_type, initial_cash,
                    commission_rate, minimum_commission, stamp_duty_rate, transfer_fee_rate,
                    now, now,
                ),
            )
            account_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO cash_event(account_id,event_type,amount,trade_date,note,created_at) "
                "VALUES (?, 'DEPOSIT', ?, date('now'), '初始资金', ?)",
                (account_id, initial_cash, now),
            )
        self.db.audit(actor, "CREATE_ACCOUNT", "account", account_id)
        self.capture_snapshot(account_id)
        return account_id

    def add_cash_event(
        self, account_id: int, event_type: str, amount: float, trade_date: str, note: str = ""
    ) -> int:
        allowed = {"DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FEE", "ADJUSTMENT"}
        if event_type not in allowed or amount == 0:
            raise LedgerError("现金事件无效")
        signed = amount
        if event_type in {"WITHDRAWAL", "FEE"}:
            signed = -abs(amount)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO cash_event(account_id,event_type,amount,trade_date,note,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (account_id, event_type, signed, trade_date, note, utc_now()),
            )
        event_id = int(cursor.lastrowid)
        self.capture_snapshot(account_id, trade_date)
        return event_id

    def add_fill(
        self,
        *,
        account_id: int,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        trade_date: str,
        plan_id: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        account = self.db.query_one("SELECT * FROM account WHERE id=?", (account_id,))
        if account is None:
            raise LedgerError("账户不存在")
        side = side.upper()
        if side not in {"BUY", "SELL"} or quantity <= 0 or price <= 0:
            raise LedgerError("成交方向、数量或价格无效")

        positions = self.positions(account_id)
        position = positions.get(symbol, Position(symbol))
        warnings: list[str] = []
        is_paper = account["account_type"] == AccountType.PAPER.value
        if side == "BUY" and quantity % 100 != 0:
            if is_paper:
                raise LedgerError("模拟账户买入数量必须为 100 股整数倍")
            warnings.append("实盘买入数量不是 100 股整数倍")
        if side == "SELL":
            if quantity > position.quantity:
                if is_paper:
                    raise LedgerError("卖出数量超过可用持仓")
                warnings.append("实盘卖出数量超过账面持仓")
            if is_paper and position.last_buy_date == trade_date:
                bought_today = self._bought_on(account_id, symbol, trade_date)
                if quantity > max(0, position.quantity - bought_today):
                    raise LedgerError("模拟账户执行 T+1，当日买入部分不可卖出")

        commission = max(float(account["minimum_commission"]), quantity * price * float(account["commission_rate"]))
        stamp = quantity * price * float(account["stamp_duty_rate"]) if side == "SELL" else 0.0
        transfer = quantity * price * float(account["transfer_fee_rate"])
        total_fee = commission + stamp + transfer
        if side == "BUY" and is_paper and self.cash_balance(account_id) < quantity * price + total_fee:
            raise LedgerError("模拟账户可用现金不足")

        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO trade_fill(account_id,plan_id,symbol,side,quantity,price,commission,"
                "stamp_duty,transfer_fee,trade_date,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    account_id, plan_id, symbol, side, quantity, price, commission, stamp,
                    transfer, trade_date, note, utc_now(),
                ),
            )
            fill_id = int(cursor.lastrowid)
            if plan_id:
                conn.execute(
                    "UPDATE trade_plan SET status='EXECUTED', updated_at=? WHERE id=?",
                    (utc_now(), plan_id),
                )
                conn.execute(
                    "UPDATE candidate SET lifecycle_status='BOUGHT', updated_at=? "
                    "WHERE id=(SELECT candidate_id FROM trade_plan WHERE id=?)",
                    (utc_now(), plan_id),
                )
        self.db.audit("admin", "ADD_FILL", "trade_fill", fill_id, {"warnings": warnings})
        self.capture_snapshot(account_id, trade_date)
        return {"id": fill_id, "fees": round(total_fee, 2), "warnings": warnings}

    def cash_balance(self, account_id: int) -> float:
        events = self.db.query_one(
            "SELECT COALESCE(SUM(e.amount),0) total FROM cash_event e "
            "WHERE e.account_id=? AND NOT EXISTS "
            "(SELECT 1 FROM cash_event_reversal r WHERE r.cash_event_id=e.id)",
            (account_id,),
        )
        fills = self.db.query_one(
            "SELECT COALESCE(SUM(CASE WHEN side='SELL' THEN quantity*price-commission-stamp_duty-transfer_fee "
            "ELSE -(quantity*price+commission+stamp_duty+transfer_fee) END),0) total "
            "FROM trade_fill f WHERE account_id=? AND NOT EXISTS "
            "(SELECT 1 FROM trade_fill_reversal r WHERE r.fill_id=f.id)",
            (account_id,),
        )
        return float(events["total"] if events else 0) + float(fills["total"] if fills else 0)

    def positions(self, account_id: int) -> dict[str, Position]:
        fills = self.db.query_all(
            "SELECT f.* FROM trade_fill f WHERE f.account_id=? AND NOT EXISTS "
            "(SELECT 1 FROM trade_fill_reversal r WHERE r.fill_id=f.id) "
            "ORDER BY f.trade_date,f.id", (account_id,)
        )
        positions: dict[str, Position] = {}
        for fill in fills:
            position = positions.setdefault(fill["symbol"], Position(fill["symbol"]))
            quantity = int(fill["quantity"])
            fees = float(fill["commission"] + fill["stamp_duty"] + fill["transfer_fee"])
            if fill["side"] == "BUY":
                old_cost = position.average_cost * position.quantity
                position.quantity += quantity
                position.average_cost = (old_cost + quantity * float(fill["price"]) + fees) / position.quantity
                position.last_buy_date = str(fill["trade_date"])
            else:
                sold = min(quantity, max(position.quantity, 0))
                proceeds = sold * float(fill["price"]) - fees
                position.realized_pnl += proceeds - sold * position.average_cost
                position.quantity -= quantity
                if position.quantity <= 0:
                    position.quantity = 0
                    position.average_cost = 0.0
        return positions

    def portfolio(self, account_id: int) -> dict[str, Any]:
        positions = self.positions(account_id)
        rows: list[dict[str, Any]] = []
        market_value = 0.0
        unrealized = 0.0
        realized = 0.0
        for position in positions.values():
            realized += position.realized_pnl
            if position.quantity <= 0:
                continue
            quote = self.db.query_one(
                "SELECT close,date FROM market_snapshot WHERE symbol=? ORDER BY date DESC LIMIT 1",
                (position.symbol,),
            )
            last_price = float(quote["close"]) if quote and quote["close"] else position.average_cost
            risk = self.db.query_one(
                "SELECT stop_price,note FROM position_risk WHERE account_id=? AND symbol=?",
                (account_id, position.symbol),
            )
            if risk is None:
                risk = self.db.query_one(
                    "SELECT p.current_stop_price stop_price,p.note FROM trade_fill f "
                    "JOIN trade_plan p ON p.id=f.plan_id "
                    "WHERE f.account_id=? AND f.symbol=? AND f.plan_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM trade_fill_reversal r WHERE r.fill_id=f.id) "
                    "ORDER BY f.trade_date DESC,f.id DESC LIMIT 1",
                    (account_id, position.symbol),
                )
            value = position.quantity * last_price
            pnl = value - position.quantity * position.average_cost
            market_value += value
            unrealized += pnl
            rows.append(
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_cost": round(position.average_cost, 4),
                    "last_price": last_price,
                    "market_value": round(value, 2),
                    "unrealized_pnl": round(pnl, 2),
                    "return_pct": pnl / (position.quantity * position.average_cost) if position.average_cost else 0,
                    "quote_date": quote["date"] if quote else None,
                    "stop_price": float(risk["stop_price"]) if risk and risk["stop_price"] else None,
                    "stop_distance": (
                        (last_price - float(risk["stop_price"])) / last_price
                        if risk and risk["stop_price"] and last_price else None
                    ),
                    "risk_amount": (
                        max(0.0, last_price - float(risk["stop_price"])) * position.quantity
                        if risk and risk["stop_price"] else None
                    ),
                }
            )
        cash = self.cash_balance(account_id)
        equity = cash + market_value
        return {
            "account_id": account_id,
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "equity": round(equity, 2),
            "total_weight": market_value / equity if equity > 0 else 0,
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(realized, 2),
            "positions": rows,
        }

    def list_fills(self, account_id: int) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT f.*,r.id reversal_id,r.reason reversal_reason,r.created_at reversed_at "
            "FROM trade_fill f LEFT JOIN trade_fill_reversal r ON r.fill_id=f.id "
            "WHERE f.account_id=? ORDER BY f.trade_date DESC,f.id DESC",
            (account_id,),
        )

    def list_cash_events(self, account_id: int) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT e.*,r.id reversal_id,r.reason reversal_reason,r.created_at reversed_at "
            "FROM cash_event e LEFT JOIN cash_event_reversal r ON r.cash_event_id=e.id "
            "WHERE e.account_id=? ORDER BY e.trade_date DESC,e.id DESC",
            (account_id,),
        )

    def reverse_fill(self, account_id: int, fill_id: int, reason: str, actor: str) -> int:
        fill = self.db.query_one(
            "SELECT * FROM trade_fill WHERE id=? AND account_id=?", (fill_id, account_id)
        )
        if fill is None:
            raise LedgerError("成交不存在")
        if len(reason.strip()) < 2:
            raise LedgerError("请填写冲正原因")
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO trade_fill_reversal(fill_id,reason,actor,created_at) VALUES (?,?,?,?)",
                    (fill_id, reason.strip(), actor, utc_now()),
                )
                if fill["plan_id"]:
                    remaining = conn.execute(
                        "SELECT COUNT(*) count FROM trade_fill f WHERE f.plan_id=? AND f.id<>? "
                        "AND NOT EXISTS (SELECT 1 FROM trade_fill_reversal r WHERE r.fill_id=f.id)",
                        (fill["plan_id"], fill_id),
                    ).fetchone()
                    if remaining and int(remaining["count"]) == 0:
                        conn.execute(
                            "UPDATE trade_plan SET status='READY',updated_at=? WHERE id=?",
                            (utc_now(), fill["plan_id"]),
                        )
                        conn.execute(
                            "UPDATE candidate SET lifecycle_status='PLANNED',updated_at=? "
                            "WHERE id=(SELECT candidate_id FROM trade_plan WHERE id=?)",
                            (utc_now(), fill["plan_id"]),
                        )
                reversal_id = int(cursor.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise LedgerError("该成交已经冲正") from exc
            raise
        account = self.db.query_one("SELECT account_type FROM account WHERE id=?", (account_id,))
        if account and account["account_type"] == AccountType.PAPER.value:
            if self.cash_balance(account_id) < -0.01:
                with self.db.transaction() as conn:
                    conn.execute("DELETE FROM trade_fill_reversal WHERE id=?", (reversal_id,))
                raise LedgerError("冲正后模拟账户现金为负，不能执行")
            if any(position.quantity < 0 for position in self.positions(account_id).values()):
                with self.db.transaction() as conn:
                    conn.execute("DELETE FROM trade_fill_reversal WHERE id=?", (reversal_id,))
                raise LedgerError("冲正后模拟账户出现超卖，不能执行")
        self.db.audit(actor, "REVERSE_FILL", "trade_fill", fill_id, {"reason": reason})
        self.capture_snapshot(account_id)
        return reversal_id

    def reverse_cash_event(self, account_id: int, event_id: int, reason: str, actor: str) -> int:
        event = self.db.query_one(
            "SELECT * FROM cash_event WHERE id=? AND account_id=?", (event_id, account_id)
        )
        if event is None:
            raise LedgerError("资金流水不存在")
        if len(reason.strip()) < 2:
            raise LedgerError("请填写冲正原因")
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO cash_event_reversal(cash_event_id,reason,actor,created_at) VALUES (?,?,?,?)",
                    (event_id, reason.strip(), actor, utc_now()),
                )
                reversal_id = int(cursor.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise LedgerError("该资金流水已经冲正") from exc
            raise
        account = self.db.query_one("SELECT account_type FROM account WHERE id=?", (account_id,))
        if account and account["account_type"] == AccountType.PAPER.value and self.cash_balance(account_id) < -0.01:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM cash_event_reversal WHERE id=?", (reversal_id,))
            raise LedgerError("冲正后模拟账户现金为负，不能执行")
        self.db.audit(actor, "REVERSE_CASH_EVENT", "cash_event", event_id, {"reason": reason})
        self.capture_snapshot(account_id)
        return reversal_id

    def set_position_risk(
        self, account_id: int, symbol: str, stop_price: float | None, note: str, actor: str
    ) -> None:
        if stop_price is not None and stop_price <= 0:
            raise LedgerError("止损价必须大于 0")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO position_risk(account_id,symbol,stop_price,note,updated_by,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(account_id,symbol) DO UPDATE SET "
                "stop_price=excluded.stop_price,note=excluded.note,updated_by=excluded.updated_by,"
                "updated_at=excluded.updated_at",
                (account_id, symbol, stop_price, note, actor, utc_now()),
            )
        self.db.audit(actor, "UPDATE_POSITION_RISK", "position", f"{account_id}:{symbol}")

    def capture_snapshot(self, account_id: int, snapshot_date: str | None = None) -> None:
        portfolio = self.portfolio(account_id)
        snapshot_date = snapshot_date or date.today().isoformat()
        peak = self.db.query_one(
            "SELECT MAX(equity) peak FROM account_snapshot WHERE account_id=?", (account_id,)
        )
        peak_equity = max(float(peak["peak"] or 0) if peak else 0, float(portfolio["equity"]))
        drawdown = float(portfolio["equity"]) / peak_equity - 1 if peak_equity > 0 else 0.0
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO account_snapshot(account_id,date,cash,market_value,equity,unrealized_pnl,"
                "realized_pnl,drawdown,position_count,created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id,date) DO UPDATE SET cash=excluded.cash,"
                "market_value=excluded.market_value,equity=excluded.equity,"
                "unrealized_pnl=excluded.unrealized_pnl,realized_pnl=excluded.realized_pnl,"
                "drawdown=excluded.drawdown,position_count=excluded.position_count,created_at=excluded.created_at",
                (
                    account_id, snapshot_date, portfolio["cash"], portfolio["market_value"],
                    portfolio["equity"], portfolio["unrealized_pnl"], portfolio["realized_pnl"],
                    drawdown, len(portfolio["positions"]), utc_now(),
                ),
            )

    def _bought_on(self, account_id: int, symbol: str, trade_date: str) -> int:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(quantity),0) quantity FROM trade_fill "
            "WHERE account_id=? AND symbol=? AND side='BUY' AND trade_date=? "
            "AND NOT EXISTS (SELECT 1 FROM trade_fill_reversal r WHERE r.fill_id=trade_fill.id)",
            (account_id, symbol, trade_date),
        )
        return int(row["quantity"] if row else 0)
