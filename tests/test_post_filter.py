"""策略结果后处理测试。"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.post_filter import StrategyPostFilter


def _make_engine(tmp_path: Path) -> DataEngine:
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings)


def _seed_symbol(engine: DataEngine, symbol: str, turnover: float, close_boost: float = 0) -> None:
    rows = []
    start = date(2024, 1, 1)
    for i in range(25):
        close = 10 + i * 0.1 + close_boost
        rows.append(
            {
                "symbol": symbol,
                "date": str(start + timedelta(days=i)),
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.3,
                "close": close,
                "volume": 1_000_000,
                "turnover": turnover,
            }
        )
    with sqlite3.connect(engine.db_path) as conn:
        pd.DataFrame(rows).to_sql("stock_daily", conn, if_exists="append", index=False)


def test_post_filter_applies_turnover_limit_and_top_n(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    _seed_symbol(engine, "600001", 200_000_000, close_boost=2)
    _seed_symbol(engine, "600002", 50_000_000, close_boost=5)
    _seed_symbol(engine, "600003", 300_000_000, close_boost=1)

    post_filter = StrategyPostFilter(engine)
    post_filter.strategy_limits = {"RpsBreakoutStrategy": 1}

    decisions = post_filter.filter_all(
        {
            "RpsBreakoutStrategy": ["600001", "600002", "600003"],
            "TurtleTradeStrategy": ["600001"],
        }
    )

    decision = decisions["RpsBreakoutStrategy"]
    assert decision.dropped_low_turnover == 1
    assert decision.truncated == 1
    assert decision.selected == ["600001"]
