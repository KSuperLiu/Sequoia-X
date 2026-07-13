"""数据引擎属性测试。"""

import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return engine, settings


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        row = {
            "symbol": symbol, "date": str(trade_date),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 1000.0, "turnover": 10500.0,
        }
        df = pd.DataFrame([row])
        with closing(sqlite3.connect(engine.db_path)) as conn:
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            try:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            except sqlite3.IntegrityError:
                pass
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
                (symbol, str(trade_date)),
            ).fetchone()[0]
        assert count == 1


class _FakeLoginResult:
    def __init__(self, error_code: str = "0", error_msg: str = "success") -> None:
        self.error_code = error_code
        self.error_msg = error_msg


class _FakeResultSet:
    def __init__(self, rows: list[list[str]], error_code: str = "0", error_msg: str = "success") -> None:
        self._rows = rows
        self._index = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._index]


class _FakeBaoStock:
    def __init__(self, responses: dict[str, list[object]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self.login_calls = 0
        self.logout_calls = 0
        self.query_calls: list[tuple[str, str, str]] = []

    def login(self) -> _FakeLoginResult:
        self.login_calls += 1
        return _FakeLoginResult()

    def logout(self) -> _FakeLoginResult:
        self.logout_calls += 1
        return _FakeLoginResult()

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> _FakeResultSet:
        del fields, frequency, adjustflag
        assert start_date is not None
        assert end_date is not None
        self.query_calls.append((code, start_date, end_date))
        queue = self.responses.get(code, [])
        if queue:
            response = queue.pop(0)
        else:
            response = []
        if isinstance(response, Exception):
            raise response
        return _FakeResultSet(response)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "gbk"

    def raise_for_status(self) -> None:
        return None


class _FakeRequests:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        self.calls += 1
        return _FakeResponse(self.text)


def _seed_symbol(engine: DataEngine, symbol: str, trade_date: str) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "date": trade_date,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "turnover": 10500.0,
            }
        ]
    )
    with closing(sqlite3.connect(engine.db_path)) as conn:
        df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")


def _seed_market_caps(engine: DataEngine, rows: list[tuple[str, float]]) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "name": symbol,
                "market_cap": market_cap,
                "updated_at": date.today().isoformat(),
            }
            for symbol, market_cap in rows
        ]
    )
    with closing(sqlite3.connect(engine.db_path)) as conn:
        df.to_sql("stock_market_cap", conn, if_exists="append", index=False, method="multi")


def test_sync_today_bulk_writes_incremental_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """日常增量同步应写入新交易日数据。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        today = date.today()
        last_trade_date = (today - timedelta(days=1)).isoformat()
        target_date = today.isoformat()
        _seed_symbol(engine, "000001", last_trade_date)
        _seed_symbol(engine, "600000", last_trade_date)
        _seed_market_caps(engine, [("000001", 6_000_000_000), ("600000", 60_000_000_000)])

        fake_bs = _FakeBaoStock(
            {
                "sz.000001": [[[target_date, "10", "11", "9", "10.5", "1000", "10500"]]],
                "sh.600000": [[[target_date, "20", "21", "19", "20.5", "2000", "40500"]]],
            }
        )
        monkeypatch.setitem(sys.modules, "baostock", fake_bs)

        count = engine.sync_today_bulk()

        assert count == 2
        assert fake_bs.login_calls == 1
        with closing(sqlite3.connect(engine.db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol, date, close FROM stock_daily WHERE date = ? ORDER BY symbol",
                (target_date,),
            ).fetchall()
        assert rows == [("000001", target_date, 10.5), ("600000", target_date, 20.5)]


def test_sync_today_bulk_retries_failed_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """单只股票首次查询失败后，应重试并继续写入。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        today = date.today()
        last_trade_date = (today - timedelta(days=1)).isoformat()
        target_date = today.isoformat()
        _seed_symbol(engine, "000001", last_trade_date)
        _seed_market_caps(engine, [("000001", 6_000_000_000)])

        fake_bs = _FakeBaoStock(
            {
                "sz.000001": [
                    RuntimeError("temporary socket error"),
                    [[target_date, "10", "11", "9", "10.5", "1000", "10500"]],
                ]
            }
        )
        monkeypatch.setitem(sys.modules, "baostock", fake_bs)
        monkeypatch.setattr("time.sleep", lambda _: None)

        count = engine.sync_today_bulk()

        assert count == 1
        assert fake_bs.login_calls >= 2
        assert fake_bs.query_calls == [
            ("sz.000001", target_date, target_date),
            ("sz.000001", target_date, target_date),
        ]


def test_sync_today_bulk_skips_small_market_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """总市值小于 50 亿的股票不应进入日常采集任务。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        today = date.today()
        last_trade_date = (today - timedelta(days=1)).isoformat()
        target_date = today.isoformat()
        _seed_symbol(engine, "000001", last_trade_date)
        _seed_symbol(engine, "000002", last_trade_date)
        _seed_market_caps(
            engine,
            [("000001", 5_000_000_000), ("000002", 4_999_999_999)],
        )

        fake_bs = _FakeBaoStock(
            {
                "sz.000001": [[[target_date, "10", "11", "9", "10.5", "1000", "10500"]]],
                "sz.000002": [[[target_date, "20", "21", "19", "20.5", "2000", "40500"]]],
            }
        )
        monkeypatch.setitem(sys.modules, "baostock", fake_bs)

        count = engine.sync_today_bulk()

        assert count == 1
        assert fake_bs.query_calls == [("sz.000001", target_date, target_date)]


def _tencent_quote_line(code: str, name: str, market_cap_yi: float) -> str:
    fields = [""] * 88
    fields[1] = name
    fields[2] = code[-6:]
    fields[44] = str(market_cap_yi)
    return f'v_{code}="' + "~".join(fields) + '";'


def test_refresh_market_cap_table_uses_tencent_market_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刷新本地市值表时，应按腾讯第 45 个字段解析总市值（亿元）。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        fake_bs = _FakeBaoStock({})
        fake_bs.query_stock_basic = lambda code_name="", code="": _FakeResultSet(
            [
                ["sz.000001", "平安银行", "", "", "1", "1"],
                ["sz.000002", "万科A", "", "", "1", "1"],
            ]
        )
        fake_requests = _FakeRequests(
            _tencent_quote_line("sz000001", "平安银行", 600)
            + _tencent_quote_line("sz000002", "万科A", 49.99)
        )
        monkeypatch.setitem(sys.modules, "baostock", fake_bs)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)
        monkeypatch.setattr("time.sleep", lambda _: None)

        count = engine.refresh_market_cap_table()

        assert count == 2
        assert fake_requests.calls == 1
        with closing(sqlite3.connect(engine.db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol, market_cap FROM stock_market_cap ORDER BY symbol"
            ).fetchall()
        assert rows == [("000001", 60_000_000_000), ("000002", 4_999_000_000)]


def test_get_all_symbols_filters_small_market_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """回填股票列表应剔除总市值小于 50 亿的股票。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_market_caps(
            engine,
            [
                ("000001", 5_000_000_000),
                ("000002", 4_999_999_999),
                ("600000", 60_000_000_000),
            ],
        )
        fake_bs = _FakeBaoStock({})
        fake_bs.query_stock_basic = lambda code_name="", code="": _FakeResultSet(
            [
                ["sz.000001", "平安银行", "", "", "1", "1"],
                ["sz.000002", "万科A", "", "", "1", "1"],
                ["sh.600000", "浦发银行", "", "", "1", "1"],
            ]
        )
        monkeypatch.setitem(sys.modules, "baostock", fake_bs)

        assert engine.get_all_symbols() == ["000001", "600000"]
