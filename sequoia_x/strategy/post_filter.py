"""策略结果后处理：流动性过滤、共振加分、评分排序与推送限量。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)


@dataclass(frozen=True)
class FilterDecision:
    """单个策略后处理结果。"""

    selected: list[str]
    dropped_low_turnover: int
    truncated: int


class StrategyPostFilter:
    """对策略原始选股结果做统一二次筛选。

    规则：
    - 技术策略默认要求最近一日成交额不低于 1 亿。
    - 多策略共振的股票优先。
    - 按流动性、突破质量、收盘强度、短期动量和共振得分排序。
    - 每个策略只保留 Top N，降低飞书噪音。
    """

    min_turnover: float = 100_000_000
    default_limit: int = 10
    strategy_limits: dict[str, int] = {
        "MaVolumeStrategy": 10,
        "TurtleTradeStrategy": 15,
        "HighTightFlagStrategy": 10,
        "LimitUpShakeoutStrategy": 10,
        "UptrendLimitDownStrategy": 10,
        "RpsBreakoutStrategy": 20,
        "PrivatePlacementStrategy": 10,
    }
    event_strategies: set[str] = {"PrivatePlacementStrategy"}

    def __init__(self, engine: DataEngine) -> None:
        self.engine = engine

    def filter_all(
        self,
        raw_results: dict[str, list[str]],
        as_of_date: str | None = None,
    ) -> dict[str, FilterDecision]:
        """批量过滤所有策略结果，利用全局共振信息排序。"""
        consensus = self._build_consensus(raw_results)
        return {
            strategy_name: self.filter_one(strategy_name, symbols, consensus, as_of_date)
            for strategy_name, symbols in raw_results.items()
        }

    def filter_one(
        self,
        strategy_name: str,
        symbols: list[str],
        consensus: dict[str, int] | None = None,
        as_of_date: str | None = None,
    ) -> FilterDecision:
        """过滤并排序单个策略结果。"""
        unique_symbols = list(dict.fromkeys(symbols))
        limit = self.strategy_limits.get(strategy_name, self.default_limit)

        if not unique_symbols:
            return FilterDecision(selected=[], dropped_low_turnover=0, truncated=0)

        if strategy_name in self.event_strategies:
            selected = unique_symbols[:limit]
            return FilterDecision(
                selected=selected,
                dropped_low_turnover=0,
                truncated=max(0, len(unique_symbols) - len(selected)),
            )

        consensus = consensus or {}
        scored: list[tuple[str, float, float]] = []
        dropped_low_turnover = 0

        for symbol in unique_symbols:
            metrics = self._latest_metrics(symbol, as_of_date)
            if metrics is None:
                dropped_low_turnover += 1
                continue

            turnover = metrics["turnover"]
            if turnover < self.min_turnover:
                dropped_low_turnover += 1
                continue

            score = self._score(metrics, consensus.get(symbol, 1))
            scored.append((symbol, score, turnover))

        scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
        selected = [symbol for symbol, _, _ in scored[:limit]]

        return FilterDecision(
            selected=selected,
            dropped_low_turnover=dropped_low_turnover,
            truncated=max(0, len(scored) - len(selected)),
        )

    @staticmethod
    def _build_consensus(raw_results: dict[str, list[str]]) -> dict[str, int]:
        consensus: dict[str, int] = {}
        for symbols in raw_results.values():
            for symbol in set(symbols):
                consensus[symbol] = consensus.get(symbol, 0) + 1
        return consensus

    def _latest_metrics(
        self, symbol: str, as_of_date: str | None = None
    ) -> dict[str, float] | None:
        try:
            df = self.engine.get_ohlcv(symbol, as_of_date=as_of_date)
        except Exception as exc:
            logger.warning(f"[{symbol}] 后处理读取行情失败：{exc}")
            return None

        if df.empty:
            return None

        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "turnover"])
        if df.empty:
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])
        turnover = float(last["turnover"])

        high_20 = float(df["high"].shift(1).rolling(20).max().iloc[-1]) if len(df) >= 21 else high
        close_20 = float(df["close"].iloc[-21]) if len(df) >= 21 else close
        day_range = max(high - low, 1e-9)

        return {
            "turnover": turnover,
            "breakout": close / high_20 - 1 if high_20 > 0 else 0,
            "close_position": (close - low) / day_range,
            "momentum_20": close / close_20 - 1 if close_20 > 0 else 0,
        }

    @staticmethod
    def _score(metrics: dict[str, float], consensus_count: int) -> float:
        turnover_score = min(metrics["turnover"] / 1_000_000_000, 1.0)
        breakout_score = max(min(metrics["breakout"] / 0.1, 1.0), -1.0)
        close_position_score = max(min(metrics["close_position"], 1.0), 0.0)
        momentum_score = max(min(metrics["momentum_20"] / 0.3, 1.0), -1.0)
        consensus_score = min(max(consensus_count - 1, 0) / 2, 1.0)

        return (
            turnover_score * 0.30
            + breakout_score * 0.20
            + close_position_score * 0.20
            + momentum_score * 0.15
            + consensus_score * 0.15
        )
