"""已下线策略不得再次出现在可执行入口中。"""

from sequoia_x.app.backtest import SUPPORTED_STRATEGIES
from sequoia_x.strategy.post_filter import StrategyPostFilter


def test_uptrend_limit_down_strategy_is_permanently_removed() -> None:
    strategy_name = "UptrendLimitDownStrategy"

    assert strategy_name not in SUPPORTED_STRATEGIES
    assert strategy_name not in StrategyPostFilter.strategy_limits
