"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。"""

import sqlite3
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""

_CREATE_MARKET_CAP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_market_cap (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    market_cap REAL    NOT NULL,
    updated_at TEXT    NOT NULL
);
"""

_CREATE_MARKET_CAP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_market_cap ON stock_market_cap (market_cap);
"""

_MIN_MARKET_CAP = 5_000_000_000
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
_TENCENT_BATCH_SIZE = 100
_TENCENT_MARKET_CAP_FIELD_INDEX = 44
_TENCENT_MARKET_CAP_YUAN = 100_000_000
_TENCENT_HEADERS = {
    "Referer": "https://gu.qq.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 baostock 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.execute(_CREATE_MARKET_CAP_TABLE_SQL)
            conn.execute(_CREATE_MARKET_CAP_INDEX_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    def _get_last_date(self, symbol: str) -> str | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        with closing(sqlite3.connect(self.db_path)) as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        """将纯数字代码转为 baostock 格式：6/9开头 -> sh，其余 -> sz。"""
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"

    @staticmethod
    def _login_baostock(bs) -> bool:
        """登录 baostock，失败时记录日志。"""
        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return False
        return True

    @staticmethod
    def _query_incremental_rows(
        bs,
        bs_code: str,
        start_date: str,
        end_date: str,
    ) -> list[list[str]]:
        """查询单只股票区间日线，返回原始行列表。"""
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="1",  # 后复权
        )

        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg)

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows

    @staticmethod
    def _normalize_symbol(symbol: object) -> str:
        """将行情源代码统一成 6 位纯数字股票代码。"""
        return str(symbol).strip().split(".")[-1].zfill(6)

    @staticmethod
    def _to_tencent_code(bs_code: str) -> str:
        """将 baostock 代码转成腾讯行情代码，如 sh.600000 -> sh600000。"""
        return bs_code.replace(".", "")

    def _get_listed_stock_rows(self) -> list[tuple[str, str, str]]:
        """通过 baostock 获取上市 A 股列表，返回 symbol/name/tencent_code。"""
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return []

        try:
            rs = bs.query_stock_basic(code_name="", code="")
            stocks: list[tuple[str, str, str]] = []
            while rs.next():
                row = rs.get_row_data()
                code = row[0]
                name = row[1]
                status = row[4]
                stock_type = row[5]
                if status == "1" and stock_type == "1":
                    stocks.append(
                        (
                            self._normalize_symbol(code),
                            name,
                            self._to_tencent_code(code),
                        )
                    )
            logger.info(f"获取上市 A 股列表完成，共 {len(stocks)} 只")
            return stocks
        except Exception as exc:
            logger.error(f"获取上市 A 股列表失败: {exc}")
            return []
        finally:
            bs.logout()

    @staticmethod
    def _parse_tencent_quote(text: str) -> list[dict[str, object]]:
        """解析腾讯行情接口返回的 ~ 分隔数据。"""
        rows: list[dict[str, object]] = []
        for line in text.split(";"):
            if not line.strip() or '="' not in line:
                continue

            payload = line.split('="', 1)[1].rstrip('"')
            fields = payload.split("~")
            if len(fields) <= _TENCENT_MARKET_CAP_FIELD_INDEX:
                continue

            market_cap_yi = pd.to_numeric(
                fields[_TENCENT_MARKET_CAP_FIELD_INDEX],
                errors="coerce",
            )
            if pd.isna(market_cap_yi) or market_cap_yi <= 0:
                continue

            rows.append(
                {
                    "代码": fields[2],
                    "名称": fields[1],
                    "总市值": float(market_cap_yi) * _TENCENT_MARKET_CAP_YUAN,
                }
            )
        return rows

    def _fetch_tencent_market_caps(
        self,
        stocks: list[tuple[str, str, str]],
    ) -> pd.DataFrame:
        """从腾讯行情接口批量获取股票总市值。"""
        import requests

        rows: list[dict[str, object]] = []
        for start in range(0, len(stocks), _TENCENT_BATCH_SIZE):
            batch = stocks[start : start + _TENCENT_BATCH_SIZE]
            codes = ",".join(stock[2] for stock in batch)
            response = requests.get(
                _TENCENT_QUOTE_URL.format(codes=codes),
                headers=_TENCENT_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            response.encoding = "gbk"
            rows.extend(self._parse_tencent_quote(response.text))
            time.sleep(0.1)

        return pd.DataFrame(rows)

    def refresh_market_cap_table(self) -> int:
        """联网刷新本地 A 股市值表，供后续采集离线过滤使用。"""
        stocks = self._get_listed_stock_rows()
        if not stocks:
            return 0

        try:
            df = self._fetch_tencent_market_caps(stocks)
        except Exception as exc:
            logger.error(f"腾讯市值接口失败，本地市值表未更新: {exc}")
            return 0

        required_columns = {"代码", "总市值"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            logger.error(f"市值数据缺少字段 {sorted(missing_columns)}，本地市值表未更新")
            return 0

        name_series = df["名称"] if "名称" in df.columns else pd.Series([""] * len(df))
        market_cap_df = pd.DataFrame(
            {
                "symbol": df["代码"].map(self._normalize_symbol),
                "name": name_series.fillna("").astype(str),
                "market_cap": pd.to_numeric(df["总市值"], errors="coerce"),
                "updated_at": date.today().isoformat(),
            }
        )
        market_cap_df = market_cap_df.dropna(subset=["market_cap"])
        market_cap_df = market_cap_df[market_cap_df["market_cap"] > 0]
        market_cap_df = market_cap_df.drop_duplicates(subset=["symbol"], keep="last")

        if market_cap_df.empty:
            logger.error("市值数据为空，本地市值表未更新")
            return 0

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM stock_market_cap")
            market_cap_df.to_sql(
                "stock_market_cap",
                conn,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=500,
            )
            conn.commit()

        logger.info(f"本地市值表刷新完成，共 {len(market_cap_df)} 只股票")
        return len(market_cap_df)

    def _get_large_cap_symbols(self) -> set[str] | None:
        """从本地市值表获取总市值不低于 50 亿的 A 股代码集合。"""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol FROM stock_market_cap WHERE market_cap >= ?",
                (_MIN_MARKET_CAP,),
            ).fetchall()

        if not rows:
            logger.error("本地市值表为空或无符合条件股票，请先执行 --refresh-market-cap")
            return None

        large_cap_symbols = {row[0] for row in rows}
        logger.info(
            f"本地市值过滤完成：保留总市值 >= 50 亿股票 {len(large_cap_symbols)} 只"
        )
        return large_cap_symbols

    # ── 数据同步 ──

    def sync_today_bulk(self) -> int:
        """稳妥串行地拉取增量数据（后复权），写入 SQLite。"""
        import baostock as bs

        max_retries = 3
        reconnect_interval = 200
        today_str = date.today().strftime("%Y-%m-%d")

        tasks = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"
            ).fetchall()

        if not rows:
            logger.warning("本地无股票数据，请先执行 --backfill")
            return 0

        large_cap_symbols = self._get_large_cap_symbols()
        if large_cap_symbols is None:
            return 0

        for symbol, last_date in rows:
            if symbol not in large_cap_symbols:
                continue
            if last_date and last_date >= today_str:
                continue
            start = today_str
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        if not tasks:
            logger.info("所有大市值股票已是最新，无需更新")
            return 0

        logger.info(f"需要更新 {len(tasks)} 只大市值股票，启动稳妥增量同步...")

        if not self._login_baostock(bs):
            return 0

        all_rows: list[list[str]] = []
        success = 0
        skipped = 0
        failed = 0
        since_reconnect = 0

        try:
            for i, (symbol, bs_code, start, end) in enumerate(tasks, start=1):
                since_reconnect += 1
                if since_reconnect >= reconnect_interval:
                    try:
                        bs.logout()
                    except Exception:
                        pass
                    time.sleep(1)
                    if not self._login_baostock(bs):
                        logger.error("重连失败，终止增量同步")
                        return 0
                    since_reconnect = 0

                rows: list[list[str]] = []
                query_ok = False
                for attempt in range(max_retries):
                    try:
                        rows = self._query_incremental_rows(bs, bs_code, start, end)
                        query_ok = True
                        break
                    except Exception as exc:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(
                                f"[{symbol}] 第{attempt + 1}次失败: {exc}，{wait}s 后重试"
                            )
                            time.sleep(wait)
                            try:
                                bs.logout()
                            except Exception:
                                pass
                            time.sleep(1)
                            if not self._login_baostock(bs):
                                logger.error("重连失败，终止增量同步")
                                return 0
                        else:
                            logger.warning(f"[{symbol}] {max_retries}次重试均失败，跳过")

                if not query_ok:
                    failed += 1
                    continue

                if not rows:
                    skipped += 1
                else:
                    all_rows.extend([symbol] + row for row in rows)
                    success += 1

                if i % 500 == 0:
                    logger.info(
                        f"已处理 {i}/{len(tasks)}，"
                        f"成功 {success} 跳过 {skipped} 失败 {failed}"
                    )
        finally:
            try:
                bs.logout()
            except Exception:
                pass

        if not all_rows:
            logger.info("无新数据（可能非交易日）")
            return 0

        df = pd.DataFrame(all_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        count = len(df)
        with closing(sqlite3.connect(self.db_path)) as conn:
            for d in df["date"].unique().tolist():
                conn.execute("DELETE FROM stock_daily WHERE date = ?", (d,))
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi", chunksize=500)
            conn.commit()

        logger.info(
            f"sync_today_bulk: 写入 {count} 条数据 | 成功 {success} | 跳过 {skipped} | 失败 {failed}"
        )
        return count

    def backfill(self, symbols: list[str]) -> None:
        """通过 baostock 批量回填历史日 K 线数据（后复权）。

        容错机制：
        - 单只股票失败自动重试 3 次，间隔递增（2s/4s/8s）
        - 每 200 只股票自动重连 baostock（防止长连接超时）
        - 已入库的自动 skip，中断后可重跑续传
        """
        import baostock as bs

        today_str = date.today().strftime("%Y-%m-%d")
        max_retries = 3
        reconnect_interval = 200  # 每处理 N 只股票重连一次

        if not self._login_baostock(bs):
            return

        success = 0
        skipped = 0
        failed = 0
        since_reconnect = 0

        try:
            for i, symbol in enumerate(symbols):
                last_date = self._get_last_date(symbol)
                if last_date and last_date >= today_str:
                    skipped += 1
                    if (i + 1) % 500 == 0:
                        logger.info(
                            f"已处理 {i + 1}/{len(symbols)}，"
                            f"成功 {success} 跳过 {skipped} 失败 {failed}"
                        )
                    continue

                # 定期重连，防止长连接超时
                since_reconnect += 1
                if since_reconnect >= reconnect_interval:
                    try:
                        bs.logout()
                    except Exception:
                        pass
                    time.sleep(1)
                    if not self._login_baostock(bs):
                        logger.error("重连失败，终止回填")
                        return
                    since_reconnect = 0

                start = last_date or self.start_date
                if last_date:
                    start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")

                bs_code = self._to_baostock_code(symbol)

                # 带重试的查询
                rows = []
                query_ok = False
                for attempt in range(max_retries):
                    try:
                        rows = self._query_incremental_rows(
                            bs, bs_code, start, today_str
                        )
                        query_ok = True
                        break

                    except Exception as exc:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(
                                f"[{symbol}] 第{attempt + 1}次失败: {exc}，{wait}s 后重试"
                            )
                            time.sleep(wait)
                            # 重连 baostock
                            try:
                                bs.logout()
                            except Exception:
                                pass
                            time.sleep(1)
                            self._login_baostock(bs)
                        else:
                            logger.warning(f"[{symbol}] {max_retries}次重试均失败，跳过")

                if not query_ok:
                    failed += 1
                    continue

                if not rows:
                    skipped += 1
                    continue

                df = pd.DataFrame(
                    rows,
                    columns=["date", "open", "high", "low", "close", "volume", "amount"],
                )
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                df = df[df["volume"] > 0]

                if df.empty:
                    skipped += 1
                    continue

                df["symbol"] = symbol
                df = df.rename(columns={"amount": "turnover"})
                df = df[["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]]

                try:
                    with closing(sqlite3.connect(self.db_path)) as conn:
                        df.to_sql(
                            "stock_daily", conn, if_exists="append",
                            index=False, method="multi", chunksize=500,
                        )
                except sqlite3.IntegrityError:
                    pass

                success += 1

                if (i + 1) % 500 == 0:
                    logger.info(
                        f"已处理 {i + 1}/{len(symbols)}，"
                        f"成功 {success} 跳过 {skipped} 失败 {failed}"
                    )

        finally:
            bs.logout()

        logger.info(f"回填完成 — 成功: {success} | 跳过: {skipped} | 失败: {failed}")

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """通过 baostock 获取符合市值条件的 A 股代码列表。"""
        import baostock as bs

        large_cap_symbols = self._get_large_cap_symbols()
        if large_cap_symbols is None:
            return []

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return []

        try:
            rs = bs.query_stock_basic(code_name="", code="")
            symbols = []
            while rs.next():
                row = rs.get_row_data()
                code = row[0]           # "sh.600000" or "sz.000001"
                status = row[4]         # "1" = 上市
                stock_type = row[5]     # "1" = 股票
                symbol = self._normalize_symbol(code)
                if status == "1" and stock_type == "1" and symbol in large_cap_symbols:
                    symbols.append(symbol)
            logger.info(f"获取大市值股票列表完成，共 {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []
        finally:
            bs.logout()

    def get_local_symbols(self) -> list[str]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        return [row[0] for row in rows]
