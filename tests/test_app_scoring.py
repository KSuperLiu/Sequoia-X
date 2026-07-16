"""四维评分、真实价换算与风险仓位测试。"""

import pandas as pd

from sequoia_x.app.domain import PositionZone, RuleConfig
from sequoia_x.app.scoring import score_candidate, suggested_quantity


def _frame() -> pd.DataFrame:
    rows = []
    for index in range(60):
        close = 65 + index * 0.07
        rows.append(
            {
                "symbol": "600001",
                "date": f"2026-01-{index + 1:02d}",
                "open": close - 0.2,
                "high": 100 if index == 0 else close + 0.5,
                "low": 65 if index == 0 else close - 0.5,
                "close": close,
                "volume": 1_000_000,
                "turnover": 200_000_000,
            }
        )
    rows[-1]["close"] = 70.0
    rows[-1]["high"] = 70.5
    rows[-1]["open"] = 69.0
    rows[-1]["volume"] = 2_000_000
    return pd.DataFrame(rows)


def test_four_dimension_score_and_real_price_conversion() -> None:
    result = score_candidate(
        _frame(),
        {"close": 35.0, "trade_status": 1, "is_st": 0},
        consensus_count=2,
        rule=RuleConfig(),
    )
    assert result.score_drawdown == 3
    assert result.score_rebound == 2
    assert result.score_volume == 1
    assert result.total_score >= 7
    assert result.entry_low is not None and result.entry_low < 35
    assert result.entry_high is not None and result.entry_high > result.entry_low
    assert result.stop_price is not None and result.stop_price < result.entry_low


def test_missing_real_quote_is_veto() -> None:
    result = score_candidate(_frame(), None, consensus_count=1, rule=RuleConfig())
    assert result.zone == PositionZone.VETO
    assert result.veto_reason == "真实行情缺失"


def test_suggested_quantity_respects_all_caps_and_lot_size() -> None:
    quantity = suggested_quantity(
        equity=1_000_000,
        cash=500_000,
        current_market_value=300_000,
        open_positions=2,
        symbol_market_value=0,
        entry_price=50,
        stop_price=45,
        zone=PositionZone.LEFT,
        rule=RuleConfig(),
    )
    assert quantity == 2000
    assert quantity % 100 == 0

    middle = suggested_quantity(
        equity=1_000_000,
        cash=500_000,
        current_market_value=300_000,
        open_positions=2,
        symbol_market_value=0,
        entry_price=50,
        stop_price=45,
        zone=PositionZone.MIDDLE,
        rule=RuleConfig(),
    )
    assert middle == 1000
