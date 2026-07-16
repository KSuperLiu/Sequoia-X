import sqlite3

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略"""

    webhook_key: str = "rps"
    rps_period: int = 120
    rps_threshold: int = 90

    def _load_recent_window(
        self,
        conn: sqlite3.Connection,
        as_of_date: str | None,
    ) -> tuple[pd.DataFrame, str | None]:
        """只读取截至指定日期最近 ``rps_period`` 个交易日的数据。"""
        date_where = ""
        date_params: tuple[str, ...] = ()
        if as_of_date is not None:
            date_where = "WHERE date<=?"
            date_params = (as_of_date,)

        trade_dates = [
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT date FROM stock_daily {date_where} "
                "ORDER BY date DESC LIMIT ?",
                (*date_params, self.rps_period),
            ).fetchall()
        ]
        if len(trade_dates) < self.rps_period:
            return pd.DataFrame(), None

        latest_date = trade_dates[0]
        earliest_date = trade_dates[-1]
        frame = pd.read_sql(
            "SELECT symbol,date,close,high FROM stock_daily "
            "WHERE date>=? AND date<=? ORDER BY symbol,date",
            conn,
            params=(earliest_date, latest_date),
        )
        return frame, latest_date

    def run(self, as_of_date: str | None = None) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df, latest_date = self._load_recent_window(conn, as_of_date)
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty or latest_date is None:
            return []

        df = df.dropna(subset=["symbol", "date", "close", "high"])
        stats = (
            df.groupby("symbol", sort=False)
            .agg(
                observations=("date", "size"),
                first_close=("close", "first"),
                latest_close=("close", "last"),
                latest_symbol_date=("date", "last"),
                window_high=("high", "max"),
            )
            .reset_index()
        )
        stats = stats[
            (stats["observations"] == self.rps_period)
            & (stats["latest_symbol_date"] == latest_date)
            & (stats["first_close"] > 0)
        ].copy()
        if stats.empty:
            return []

        # 120 个交易日窗口使用窗口首尾收盘价计算相对涨幅。
        stats["pct_change"] = (
            stats["latest_close"] - stats["first_close"]
        ) / stats["first_close"]
        stats["rps"] = stats["pct_change"].rank(pct=True) * 100
        selected = stats[
            (stats["rps"] >= self.rps_threshold)
            & (stats["latest_close"] >= stats["window_high"] * 0.90)
        ]

        logger.info(
            f"RpsBreakoutStrategy 仅加载最近 {self.rps_period} 个交易日 "
            f"({len(df)} 行)，选出 {len(selected)} 只股票"
        )
        return selected["symbol"].tolist()
