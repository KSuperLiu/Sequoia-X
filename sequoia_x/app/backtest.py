"""基于日线的基础组合回测，实时与回测共享四维评分规则。"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

import pandas as pd

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.domain import PositionZone, RuleConfig, RunStatus
from sequoia_x.app.scoring import score_candidate, suggested_quantity
from sequoia_x.data.engine import DataEngine

SUPPORTED_STRATEGIES = {
    "MaVolumeStrategy",
    "TurtleTradeStrategy",
    "HighTightFlagStrategy",
    "LimitUpShakeoutStrategy",
    "RpsBreakoutStrategy",
}


class BacktestCancellationError(RuntimeError):
    """回测不能中止，或当前状态不允许中止。"""


class BacktestCancelled(RuntimeError):
    """回测在安全检查点收到中止请求。"""


@dataclass
class OpenPosition:
    symbol: str
    signal_date: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_price: float
    entry_fees: float
    holding_days: int = 0


class BacktestService:
    def __init__(self, app_db: AppDatabase, engine: DataEngine) -> None:
        self.app_db = app_db
        self.engine = engine
        self._recover_interrupted_runs()

    def _recover_interrupted_runs(self) -> None:
        """API 重启后，结束已失去后台执行线程的遗留任务。"""
        with self.app_db.transaction() as conn:
            rows = conn.execute(
                "SELECT id,status,cancel_requested FROM backtest_run "
                "WHERE status IN ('PENDING','RUNNING')"
            ).fetchall()
            now = utc_now()
            for row in rows:
                cancelled = bool(row["cancel_requested"])
                status = "CANCELLED" if cancelled else "FAILED"
                stage = "已取消" if cancelled else "服务中断"
                message = (
                    "API 服务重启前已收到中止请求，回测已清理"
                    if cancelled
                    else "API 服务曾重启，原后台回测已中断，请重新创建回测"
                )
                conn.execute(
                    "UPDATE backtest_run SET status=?,current_stage=?,finished_at=?,"
                    "error_message=? WHERE id=?",
                    (status, stage, now, None if cancelled else message, row["id"]),
                )
                conn.execute(
                    "INSERT INTO backtest_log(backtest_run_id,level,message,created_at) "
                    "VALUES (?,?,?,?)",
                    (row["id"], "WARNING", message, now),
                )

    def create_run(
        self,
        *,
        strategy_name: str,
        start_date: str,
        end_date: str,
        initial_cash: float,
        fee: dict[str, float],
    ) -> int:
        if strategy_name not in SUPPORTED_STRATEGIES:
            raise ValueError("该策略暂不支持历史回测")
        if start_date >= end_date or initial_cash <= 0:
            raise ValueError("回测日期或初始资金无效")
        rule_id, _ = self.app_db.active_rule()
        with self.app_db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO backtest_run(strategy_name,rule_version_id,start_date,end_date,initial_cash,"
                "fee_json,status,created_at,current_stage) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    strategy_name, rule_id, start_date, end_date, initial_cash,
                    json.dumps(fee), RunStatus.PENDING.value, utc_now(), "等待执行",
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO backtest_log(backtest_run_id,level,message,created_at) VALUES (?,?,?,?)",
                (run_id, "INFO", "回测任务已创建，等待后台执行", utc_now()),
            )
            return run_id

    def request_cancel(self, run_id: int, actor: str) -> dict[str, Any]:
        """请求协作式中止；等待中的任务立即取消，运行中的任务在检查点退出。"""
        now = utc_now()
        with self.app_db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_run WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise BacktestCancellationError("回测任务不存在")
            if row["status"] == "PENDING":
                conn.execute(
                    "UPDATE backtest_run SET status='CANCELLED',finished_at=?,current_stage='已取消',"
                    "cancel_requested=1,cancel_requested_at=?,cancel_requested_by=? WHERE id=?",
                    (now, now, actor, run_id),
                )
                message = f"{actor} 已取消等待中的回测任务"
            elif row["status"] == "RUNNING":
                if row["cancel_requested"]:
                    raise BacktestCancellationError("回测任务正在中止，请稍候")
                conn.execute(
                    "UPDATE backtest_run SET cancel_requested=1,cancel_requested_at=?,"
                    "cancel_requested_by=?,current_stage='正在中止' WHERE id=?",
                    (now, actor, run_id),
                )
                message = f"{actor} 请求中止回测，任务将在下一个安全检查点退出"
            else:
                raise BacktestCancellationError("当前状态的回测任务不能中止")
            conn.execute(
                "INSERT INTO backtest_log(backtest_run_id,level,message,created_at) VALUES (?,?,?,?)",
                (run_id, "WARNING", message, now),
            )
            return dict(
                conn.execute("SELECT * FROM backtest_run WHERE id=?", (run_id,)).fetchone()
            )

    def _log(self, run_id: int, message: str, level: str = "INFO") -> None:
        with self.app_db.transaction() as conn:
            conn.execute(
                "INSERT INTO backtest_log(backtest_run_id,level,message,created_at) VALUES (?,?,?,?)",
                (run_id, level, message, utc_now()),
            )

    def _checkpoint(
        self,
        run_id: int,
        stage: str,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> None:
        row = self.app_db.query_one(
            "SELECT status,cancel_requested FROM backtest_run WHERE id=?", (run_id,)
        )
        if row is None or row["status"] == "CANCELLED" or row["cancel_requested"]:
            raise BacktestCancelled("回测已由用户中止")
        assignments = ["current_stage=?"]
        values: list[Any] = [stage]
        if progress_current is not None:
            assignments.append("progress_current=?")
            values.append(progress_current)
        if progress_total is not None:
            assignments.append("progress_total=?")
            values.append(progress_total)
        values.append(run_id)
        with self.app_db.transaction() as conn:
            conn.execute(
                f"UPDATE backtest_run SET {','.join(assignments)} WHERE id=?", values
            )

    def execute(self, run_id: int) -> None:
        run = self.app_db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
        if run is None:
            raise ValueError("回测任务不存在")
        with self.app_db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE backtest_run SET status='RUNNING',started_at=?,current_stage='准备数据' "
                "WHERE id=? AND status='PENDING' AND cancel_requested=0",
                (utc_now(), run_id),
            )
            if cursor.rowcount == 0:
                return
            conn.execute("DELETE FROM backtest_trade WHERE backtest_run_id=?", (run_id,))
            conn.execute("DELETE FROM backtest_equity WHERE backtest_run_id=?", (run_id,))
        self._log(run_id, "回测开始执行")
        run = self.app_db.query_one("SELECT * FROM backtest_run WHERE id=?", (run_id,))
        try:
            self._execute(run)
        except BacktestCancelled:
            with self.app_db.transaction() as conn:
                conn.execute(
                    "UPDATE backtest_run SET status='CANCELLED',finished_at=?,"
                    "current_stage='已取消' WHERE id=?",
                    (utc_now(), run_id),
                )
            self._log(run_id, "回测已在安全检查点停止，未写入未完成的结果", "WARNING")
        except Exception as exc:
            with self.app_db.transaction() as conn:
                conn.execute(
                    "UPDATE backtest_run SET status='FAILED',error_message=?,finished_at=?,"
                    "current_stage='失败' WHERE id=?",
                    (str(exc), utc_now(), run_id),
                )
            self._log(run_id, f"回测失败：{exc}", "ERROR")

    def _execute(self, run: dict[str, Any]) -> None:
        run_id = int(run["id"])
        fee = json.loads(run["fee_json"])
        rule_row = self.app_db.query_one(
            "SELECT config_json FROM rule_version WHERE id=?", (run["rule_version_id"],)
        )
        rule = RuleConfig.from_dict(json.loads(rule_row["config_json"])) if rule_row else RuleConfig()
        self._checkpoint(run_id, "加载历史行情")
        self._log(run_id, "正在加载回测区间及指标预热所需的历史行情")
        data = self._load_data(run["start_date"], run["end_date"])
        if data.empty:
            raise ValueError("回测区间没有行情数据")
        self._checkpoint(run_id, "计算策略信号")
        self._log(run_id, f"历史行情加载完成，共 {len(data)} 条，开始计算策略信号")
        signals = self._strategy_signals(data, run["strategy_name"])
        signals = signals[(signals["date"] >= run["start_date"]) & (signals["date"] <= run["end_date"])]
        self._checkpoint(run_id, "构建交易日历")
        self._log(run_id, f"策略信号计算完成，共发现 {len(signals)} 条原始信号")

        frames = {
            symbol: frame.sort_values("date").reset_index(drop=True)
            for symbol, frame in data.groupby("symbol", sort=False)
        }
        row_lookup = {
            (row.symbol, row.date): row
            for row in data.itertuples(index=False)
            if run["start_date"] <= row.date <= run["end_date"]
        }
        trade_dates = sorted({row.date for row in data.itertuples(index=False) if run["start_date"] <= row.date <= run["end_date"]})
        self._checkpoint(run_id, "模拟交易", 0, len(trade_dates))
        self._log(run_id, f"开始逐日模拟，共 {len(trade_dates)} 个交易日")
        next_date = {trade_dates[i]: trade_dates[i + 1] for i in range(len(trade_dates) - 1)}
        pending: dict[str, list[dict[str, Any]]] = {}
        open_positions: dict[str, OpenPosition] = {}
        cash = float(run["initial_cash"])
        equity_curve: list[tuple[str, float]] = []
        trades: list[dict[str, Any]] = []

        signal_by_date = {date: group for date, group in signals.groupby("date")}
        log_interval = max(1, len(trade_dates) // 10)
        for day_index, trade_date in enumerate(trade_dates, start=1):
            if day_index == 1 or day_index % 5 == 0 or day_index == len(trade_dates):
                self._checkpoint(run_id, "模拟交易", day_index, len(trade_dates))
            if day_index % log_interval == 0 or day_index == len(trade_dates):
                self._log(
                    run_id,
                    f"模拟进度 {day_index}/{len(trade_dates)}，当前交易日 {trade_date}",
                )
            # 先处理止损/到期退出，禁止用当日收盘后信号影响当日成交。
            for symbol, position in list(open_positions.items()):
                bar = row_lookup.get((symbol, trade_date))
                if bar is None:
                    continue
                if trade_date > position.entry_date:
                    position.holding_days += 1
                exit_price: float | None = None
                exit_reason = ""
                if float(bar.open) <= position.stop_price:
                    exit_price, exit_reason = float(bar.open), "GAP_STOP"
                elif float(bar.low) <= position.stop_price:
                    exit_price, exit_reason = position.stop_price, "STOP"
                elif position.holding_days >= rule.max_holding_days:
                    exit_price, exit_reason = float(bar.close), "TIME_EXIT"
                if exit_price is not None:
                    exit_fee = self._fee(position.quantity, exit_price, "SELL", fee)
                    proceeds = position.quantity * exit_price - exit_fee
                    cash += proceeds
                    pnl = proceeds - position.quantity * position.entry_price - position.entry_fees
                    trades.append(
                        {
                            "symbol": symbol, "signal_date": position.signal_date,
                            "entry_date": position.entry_date, "entry_price": position.entry_price,
                            "quantity": position.quantity, "stop_price": position.stop_price,
                            "exit_date": trade_date, "exit_price": exit_price,
                            "exit_reason": exit_reason, "fees": position.entry_fees + exit_fee,
                            "pnl": pnl,
                            "return_pct": pnl / (position.quantity * position.entry_price + position.entry_fees),
                        }
                    )
                    del open_positions[symbol]

            for order in pending.pop(trade_date, []):
                if order["symbol"] in open_positions or len(open_positions) >= rule.max_positions:
                    continue
                bar = row_lookup.get((order["symbol"], trade_date))
                if bar is None:
                    continue
                open_price = float(bar.open)
                if open_price > order["entry_high"] or open_price <= order["stop_price"]:
                    continue
                market_value = 0.0
                for held_symbol, held in open_positions.items():
                    held_bar = row_lookup.get((held_symbol, trade_date))
                    market_value += held.quantity * (
                        float(held_bar.close) if held_bar else held.entry_price
                    )
                equity = cash + market_value
                quantity = suggested_quantity(
                    equity=equity,
                    cash=cash,
                    current_market_value=market_value,
                    open_positions=len(open_positions),
                    symbol_market_value=0,
                    entry_price=open_price,
                    stop_price=order["stop_price"],
                    zone=PositionZone(order["zone"]),
                    rule=rule,
                )
                entry_fee = self._fee(quantity, open_price, "BUY", fee)
                while quantity >= 100 and quantity * open_price + entry_fee > cash:
                    quantity -= 100
                    entry_fee = self._fee(quantity, open_price, "BUY", fee)
                if quantity < 100:
                    continue
                cash -= quantity * open_price + entry_fee
                open_positions[order["symbol"]] = OpenPosition(
                    symbol=order["symbol"], signal_date=order["signal_date"],
                    entry_date=trade_date, entry_price=open_price, quantity=quantity,
                    stop_price=order["stop_price"], entry_fees=entry_fee,
                )

            # 收盘后根据当天信号生成下一交易日订单。
            if trade_date in signal_by_date and trade_date in next_date:
                candidates: list[dict[str, Any]] = []
                group = signal_by_date[trade_date].sort_values("turnover", ascending=False)
                for signal in group.itertuples(index=False):
                    frame = frames[str(signal.symbol)]
                    history = frame[frame["date"] <= trade_date]
                    if len(history) < 60:
                        continue
                    quote = {
                        "close": float(signal.close), "trade_status": 1, "is_st": 0,
                    }
                    scored = score_candidate(history, quote, 1, rule)
                    if scored.zone not in {PositionZone.LEFT, PositionZone.MIDDLE}:
                        continue
                    candidates.append(
                        {
                            "symbol": str(signal.symbol), "signal_date": trade_date,
                            "entry_high": scored.entry_high, "stop_price": scored.stop_price,
                            "zone": scored.zone.value, "score": scored.total_score,
                            "turnover": float(signal.turnover),
                        }
                    )
                candidates.sort(key=lambda item: (item["score"], item["turnover"]), reverse=True)
                pending[next_date[trade_date]] = candidates[:20]

            market_value = 0.0
            for symbol, position in open_positions.items():
                bar = row_lookup.get((symbol, trade_date))
                market_value += position.quantity * (float(bar.close) if bar else position.entry_price)
            equity_curve.append((trade_date, cash + market_value))

        if trade_dates:
            last_date = trade_dates[-1]
            for symbol, position in list(open_positions.items()):
                bar = row_lookup.get((symbol, last_date))
                if bar is None:
                    continue
                exit_price = float(bar.close)
                exit_fee = self._fee(position.quantity, exit_price, "SELL", fee)
                proceeds = position.quantity * exit_price - exit_fee
                cash += proceeds
                pnl = proceeds - position.quantity * position.entry_price - position.entry_fees
                trades.append(
                    {
                        "symbol": symbol, "signal_date": position.signal_date,
                        "entry_date": position.entry_date, "entry_price": position.entry_price,
                        "quantity": position.quantity, "stop_price": position.stop_price,
                        "exit_date": last_date, "exit_price": exit_price, "exit_reason": "END_DATE",
                        "fees": position.entry_fees + exit_fee, "pnl": pnl,
                        "return_pct": pnl / (position.quantity * position.entry_price + position.entry_fees),
                    }
                )
            if equity_curve:
                equity_curve[-1] = (last_date, cash)

        self._checkpoint(run_id, "计算绩效", len(trade_dates), len(trade_dates))
        self._log(run_id, f"交易模拟完成，共生成 {len(trades)} 笔交易，正在计算绩效")
        metrics = self._metrics(float(run["initial_cash"]), equity_curve, trades)
        self._checkpoint(run_id, "加载基准", len(trade_dates), len(trade_dates))
        benchmark = self._benchmark_series(
            run["start_date"], run["end_date"], float(run["initial_cash"])
        )
        if benchmark:
            first = next(iter(benchmark.values()))
            last = list(benchmark.values())[-1]
            metrics["benchmark_return"] = last / first - 1 if first else None
        self._checkpoint(run_id, "保存结果", len(trade_dates), len(trade_dates))
        with self.app_db.transaction() as conn:
            state = conn.execute(
                "SELECT cancel_requested FROM backtest_run WHERE id=?", (run_id,)
            ).fetchone()
            if state is None or state["cancel_requested"]:
                raise BacktestCancelled("回测已由用户中止")
            for trade in trades:
                conn.execute(
                    "INSERT INTO backtest_trade(backtest_run_id,symbol,signal_date,entry_date,entry_price,"
                    "quantity,stop_price,exit_date,exit_price,exit_reason,fees,pnl,return_pct) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run["id"], *trade.values()),
                )
            for date_value, equity in equity_curve:
                conn.execute(
                    "INSERT INTO backtest_equity(backtest_run_id,date,equity,benchmark) VALUES (?,?,?,?)",
                    (run["id"], date_value, equity, benchmark.get(date_value)),
                )
            conn.execute(
                "UPDATE backtest_run SET status='SUCCEEDED',metrics_json=?,finished_at=?,"
                "current_stage='完成',progress_current=?,progress_total=? WHERE id=?",
                (
                    json.dumps(metrics, ensure_ascii=False), utc_now(), len(trade_dates),
                    len(trade_dates), run_id,
                ),
            )
        self._log(run_id, "回测成功完成，交易明细和权益曲线已保存")

    def _load_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        with sqlite3.connect(self.engine.db_path) as conn:
            # 从起始日前加载全部已有数据，由滚动指标自然丢弃过早部分。
            data = pd.read_sql(
                "SELECT symbol,date,open,high,low,close,volume,turnover FROM stock_daily "
                "WHERE date<=? ORDER BY symbol,date",
                conn,
                params=(end_date,),
            )
        for column in ("open", "high", "low", "close", "volume", "turnover"):
            data[column] = pd.to_numeric(data[column], errors="coerce")
        return data.dropna(subset=["close", "open", "high", "low"])

    @staticmethod
    def _strategy_signals(data: pd.DataFrame, strategy: str) -> pd.DataFrame:
        df = data.copy().sort_values(["symbol", "date"])
        group = df.groupby("symbol", group_keys=False)
        prev_close = group["close"].shift(1)
        prev2_close = group["close"].shift(2)
        prev_volume = group["volume"].shift(1)
        ma5 = group["close"].transform(lambda s: s.rolling(5).mean())
        ma20 = group["close"].transform(lambda s: s.rolling(20).mean())
        vol20 = group["volume"].transform(lambda s: s.rolling(20).mean())
        if strategy == "MaVolumeStrategy":
            previous_ma5 = ma5.groupby(df["symbol"]).shift(1)
            previous_ma20 = ma20.groupby(df["symbol"]).shift(1)
            condition = (previous_ma5 < previous_ma20) & (ma5 > ma20) & (df["volume"] > vol20 * 1.5)
        elif strategy == "TurtleTradeStrategy":
            high20 = group["high"].transform(lambda s: s.shift(1).rolling(20).max())
            condition = (
                (df["close"] > high20) & (df["turnover"] > 100_000_000)
                & (df["close"] > df["open"]) & (df["close"] > prev_close)
            )
        elif strategy == "HighTightFlagStrategy":
            high40 = group["high"].transform(lambda s: s.rolling(40).max())
            low40 = group["low"].transform(lambda s: s.rolling(40).min())
            high10 = group["high"].transform(lambda s: s.rolling(10).max())
            low10 = group["low"].transform(lambda s: s.rolling(10).min())
            prev_vol20 = group["volume"].transform(lambda s: s.shift(1).rolling(20).mean())
            condition = (
                (high40 / low40 > 1.6) & (high10 / low10 < 1.15)
                & (low10 >= high40 * 0.8) & (df["volume"] < prev_vol20 * 0.6)
            )
        elif strategy == "LimitUpShakeoutStrategy":
            condition = (
                (prev_close >= prev2_close * 1.095) & (df["close"] < df["open"])
                & (df["volume"] > prev_volume * 2) & (df["low"] >= prev_close)
            )
        elif strategy == "RpsBreakoutStrategy":
            close120 = group["close"].shift(120)
            pct = df["close"] / close120 - 1
            rps = pct.groupby(df["date"]).rank(pct=True) * 100
            high120 = group["high"].transform(lambda s: s.rolling(120, min_periods=60).max())
            condition = (rps >= 90) & (df["close"] >= high120 * 0.9)
        else:
            raise ValueError("不支持的回测策略")
        selected = df[condition.fillna(False)].copy()
        return selected[selected["turnover"] >= 100_000_000]

    @staticmethod
    def _fee(quantity: int, price: float, side: str, fee: dict[str, float]) -> float:
        if quantity <= 0:
            return 0.0
        value = quantity * price
        commission = max(float(fee.get("minimum_commission", 0)), value * float(fee.get("commission_rate", 0)))
        transfer = value * float(fee.get("transfer_fee_rate", 0))
        stamp = value * float(fee.get("stamp_duty_rate", 0)) if side == "SELL" else 0
        return commission + transfer + stamp

    @staticmethod
    def _benchmark_series(
        start_date: str, end_date: str, initial_cash: float
    ) -> dict[str, float]:
        """获取沪深300基准；数据源不可用时保留空基准，不影响策略结果。"""
        try:
            import baostock as bs

            login = bs.login()
            if login.error_code != "0":
                return {}
            try:
                result = bs.query_history_k_data_plus(
                    "sh.000300",
                    "date,close",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )
                rows: list[tuple[str, float]] = []
                while result.error_code == "0" and result.next():
                    row = result.get_row_data()
                    rows.append((row[0], float(row[1])))
            finally:
                bs.logout()
        except Exception:
            return {}
        if not rows or rows[0][1] <= 0:
            return {}
        base = rows[0][1]
        return {day: initial_cash * close / base for day, close in rows}

    @staticmethod
    def _metrics(initial_cash: float, curve: list[tuple[str, float]], trades: list[dict[str, Any]]) -> dict[str, Any]:
        if not curve:
            return {"trade_count": 0, "total_return": 0, "bias_warning": True}
        series = pd.Series([equity for _, equity in curve], dtype=float)
        returns = series.pct_change().dropna()
        total_return = float(series.iloc[-1] / initial_cash - 1)
        years = max(len(series) / 252, 1 / 252)
        annualized = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1
        drawdown = series / series.cummax() - 1
        wins = [trade for trade in trades if trade["pnl"] > 0]
        losses = [trade for trade in trades if trade["pnl"] < 0]
        gross_profit = sum(trade["pnl"] for trade in wins)
        gross_loss = abs(sum(trade["pnl"] for trade in losses))
        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0
        sharpe = (
            float(returns.mean() / returns.std() * math.sqrt(252))
            if len(returns) > 1 and returns.std() > 0 else 0
        )
        return {
            "trade_count": len(trades), "total_return": total_return,
            "annualized_return": annualized, "max_drawdown": float(drawdown.min()),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "payoff_ratio": avg_win / avg_loss if avg_loss else None,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "sharpe": sharpe, "bias_warning": True,
            "warning": "当前股票池与市值快照存在幸存者偏差及历史口径偏差。",
        }
