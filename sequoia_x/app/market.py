"""候选与持仓股票的真实行情、估值和行业补充。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)


class MarketEnricher:
    def __init__(self, app_db: AppDatabase, engine: DataEngine) -> None:
        self.app_db = app_db
        self.engine = engine

    def fetch_quotes(self, symbols: list[str], trade_date: str) -> dict[str, dict[str, Any]]:
        """只为候选/持仓补采不复权日行情；失败股票从缓存读取。"""
        unique = list(dict.fromkeys(symbols))
        if not unique:
            return {}
        try:
            import baostock as bs
        except ImportError:
            return self._cached(unique, trade_date)

        result: dict[str, dict[str, Any]] = {}
        if not self.engine._login_baostock(bs):
            return self._cached(unique, trade_date)
        try:
            for symbol in unique:
                try:
                    rs = bs.query_history_k_data_plus(
                        self.engine._to_baostock_code(symbol),
                        "date,open,high,low,close,volume,amount,peTTM,pbMRQ,tradestatus,isST",
                        start_date=trade_date,
                        end_date=trade_date,
                        frequency="d",
                        adjustflag="3",
                    )
                    if rs.error_code != "0" or not rs.next():
                        continue
                    row = rs.get_row_data()
                    values = dict(zip(rs.fields, row))
                    quote = {
                        "symbol": symbol,
                        "date": values.get("date", trade_date),
                        "open": _number(values.get("open")),
                        "high": _number(values.get("high")),
                        "low": _number(values.get("low")),
                        "close": _number(values.get("close")),
                        "volume": _number(values.get("volume")),
                        "turnover": _number(values.get("amount")),
                        "pe_ttm": _number(values.get("peTTM")),
                        "pb_mrq": _number(values.get("pbMRQ")),
                        "trade_status": int(_number(values.get("tradestatus"), 1)),
                        "is_st": int(_number(values.get("isST"), 0)),
                    }
                    if quote["close"] and quote["close"] > 0:
                        result[symbol] = quote
                except Exception as exc:
                    logger.warning(f"[{symbol}] 补采真实行情失败：{exc}")
        finally:
            try:
                bs.logout()
            except Exception:
                pass

        self._save_quotes(result)
        cached = self._cached([s for s in unique if s not in result], trade_date)
        result.update(cached)
        return result

    def refresh_profiles(self, symbols: list[str]) -> None:
        if not symbols:
            return
        names = self._market_cap_names(symbols)
        industries: dict[str, str] = {}
        try:
            import baostock as bs

            if self.engine._login_baostock(bs):
                try:
                    for symbol in symbols:
                        rs = bs.query_stock_industry(
                            code=self.engine._to_baostock_code(symbol), date=""
                        )
                        if rs.error_code == "0" and rs.next():
                            row = dict(zip(rs.fields, rs.get_row_data()))
                            industries[symbol] = str(row.get("industry", ""))
                finally:
                    bs.logout()
        except Exception as exc:
            logger.warning(f"行业分类补充失败：{exc}")

        with self.app_db.transaction() as conn:
            for symbol in symbols:
                conn.execute(
                    "INSERT INTO stock_profile(symbol, name, industry, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(symbol) DO UPDATE SET "
                    "name=COALESCE(excluded.name, stock_profile.name), "
                    "industry=CASE WHEN excluded.industry<>'' THEN excluded.industry ELSE stock_profile.industry END, "
                    "updated_at=excluded.updated_at",
                    (symbol, names.get(symbol), industries.get(symbol, ""), utc_now()),
                )

    def _market_cap_names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        import sqlite3

        with sqlite3.connect(self.engine.db_path) as conn:
            rows = conn.execute(
                f"SELECT symbol, name FROM stock_market_cap WHERE symbol IN ({placeholders})",
                tuple(symbols),
            ).fetchall()
        return {str(symbol): str(name or symbol) for symbol, name in rows}

    def _cached(self, symbols: list[str], trade_date: str) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        rows = self.app_db.query_all(
            f"SELECT * FROM market_snapshot WHERE date=? AND symbol IN ({placeholders})",
            (trade_date, *symbols),
        )
        return {str(row["symbol"]): row for row in rows}

    def _save_quotes(self, quotes: dict[str, dict[str, Any]]) -> None:
        if not quotes:
            return
        with self.app_db.transaction() as conn:
            for quote in quotes.values():
                conn.execute(
                    "INSERT INTO market_snapshot(symbol,date,open,high,low,close,volume,turnover,"
                    "pe_ttm,pb_mrq,trade_status,is_st,source,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(symbol,date) DO UPDATE SET open=excluded.open,high=excluded.high,"
                    "low=excluded.low,close=excluded.close,volume=excluded.volume,turnover=excluded.turnover,"
                    "pe_ttm=excluded.pe_ttm,pb_mrq=excluded.pb_mrq,trade_status=excluded.trade_status,"
                    "is_st=excluded.is_st,updated_at=excluded.updated_at",
                    (
                        quote["symbol"], quote["date"], quote["open"], quote["high"],
                        quote["low"], quote["close"], quote["volume"], quote["turnover"],
                        quote["pe_ttm"], quote["pb_mrq"], quote["trade_status"], quote["is_st"],
                        "baostock_unadjusted", utc_now(),
                    ),
                )


def _number(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else float(parsed)
