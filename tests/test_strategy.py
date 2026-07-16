"""策略引擎属性测试。"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy


# Feature: sequoia-x-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)

        with patch.object(engine, "get_all_symbols", return_value=symbols):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                strategy = MaVolumeStrategy(engine=engine, settings=settings)
                result = strategy.run()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def test_rps_only_reads_latest_120_trade_dates_and_respects_as_of_date(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "rps.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    dates = [item.strftime("%Y-%m-%d") for item in pd.bdate_range("2025-01-02", periods=122)]

    rows: list[tuple[str, str, float, float]] = []
    # 第 121 条旧数据和 as_of_date 之后的数据均设置极端高点；如果被错误读取，
    # 600001 将无法通过距离 120 日高点不超过 10% 的突破判定。
    rows.append(("600001", dates[0], 1.0, 1_000.0))
    for index, trade_date in enumerate(dates[1:121], start=0):
        close = 100.0 + index
        rows.append(("600001", trade_date, close, close))
        rows.append(("600002", trade_date, 100.0 + index * 0.1, 1_000.0))
    rows.append(("600001", dates[121], 50.0, 5_000.0))

    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily(symbol,date,close,high) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()

    strategy = RpsBreakoutStrategy(engine=engine, settings=settings)
    strategy.rps_threshold = 0
    with sqlite3.connect(engine.db_path) as conn:
        frame, latest_date = strategy._load_recent_window(conn, dates[120])

    assert latest_date == dates[120]
    assert frame.groupby("symbol").size().to_dict() == {"600001": 120, "600002": 120}
    assert frame["date"].min() == dates[1]
    assert frame["date"].max() == dates[120]
    assert strategy.run(as_of_date=dates[120]) == ["600001"]
