"""模拟组合与人工实盘账本。"""

from __future__ import annotations

from dataclasses import dataclass
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
    ) -> int:
        if account_type not in {item.value for item in AccountType}:
            raise LedgerError("账户类型无效")
        values = [initial_cash, commission_rate, minimum_commission, stamp_duty_rate, transfer_fee_rate]
        if initial_cash <= 0 or any(value < 0 for value in values):
            raise LedgerError("初始资金必须大于 0，费率不得为负")
        now = utc_now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO account(name,account_type,initial_cash,commission_rate,minimum_commission,"
                "stamp_duty_rate,transfer_fee_rate,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    name, account_type, initial_cash, commission_rate, minimum_commission,
                    stamp_duty_rate, transfer_fee_rate, now, now,
                ),
            )
            account_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO cash_event(account_id,event_type,amount,trade_date,note,created_at) "
                "VALUES (?, 'DEPOSIT', ?, date('now'), '初始资金', ?)",
                (account_id, initial_cash, now),
            )
        self.db.audit("admin", "CREATE_ACCOUNT", "account", account_id)
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
        return int(cursor.lastrowid)

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
        return {"id": fill_id, "fees": round(total_fee, 2), "warnings": warnings}

    def cash_balance(self, account_id: int) -> float:
        events = self.db.query_one(
            "SELECT COALESCE(SUM(amount),0) total FROM cash_event WHERE account_id=?",
            (account_id,),
        )
        fills = self.db.query_one(
            "SELECT COALESCE(SUM(CASE WHEN side='SELL' THEN quantity*price-commission-stamp_duty-transfer_fee "
            "ELSE -(quantity*price+commission+stamp_duty+transfer_fee) END),0) total "
            "FROM trade_fill WHERE account_id=?",
            (account_id,),
        )
        return float(events["total"] if events else 0) + float(fills["total"] if fills else 0)

    def positions(self, account_id: int) -> dict[str, Position]:
        fills = self.db.query_all(
            "SELECT * FROM trade_fill WHERE account_id=? ORDER BY trade_date,id", (account_id,)
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

    def _bought_on(self, account_id: int, symbol: str, trade_date: str) -> int:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(quantity),0) quantity FROM trade_fill "
            "WHERE account_id=? AND symbol=? AND side='BUY' AND trade_date=?",
            (account_id, symbol, trade_date),
        )
        return int(row["quantity"] if row else 0)
