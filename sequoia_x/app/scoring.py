"""四维评分、真实价换算、交易区间与仓位建议。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from sequoia_x.app.domain import PositionZone, RuleConfig


@dataclass(frozen=True)
class ScoreResult:
    drawdown_60: float
    rebound_60: float
    ma10: float
    ma20: float
    volume_ratio: float
    atr14: float
    score_drawdown: int
    score_rebound: int
    score_ma: int
    score_volume: int
    total_score: int
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    zone: PositionZone
    veto_reason: str | None
    rationale: str


def _drawdown_score(drawdown: float, rule: RuleConfig) -> int:
    if rule.drawdown_score3_min <= drawdown <= rule.drawdown_score3_max:
        return 3
    if rule.drawdown_score2_min <= drawdown < rule.drawdown_score3_min:
        return 2
    if rule.drawdown_score1_min <= drawdown < rule.drawdown_score2_min:
        return 1
    return 0


def _rebound_score(rebound: float, rule: RuleConfig) -> int:
    if rule.rebound_score2_min <= rebound <= rule.rebound_score2_max:
        return 2
    if 0 <= rebound < rule.rebound_score2_min:
        return 1
    if rule.rebound_score2_max < rebound <= rule.rebound_score1_max:
        return 1
    return 0


def _ma_score(close: float, ma10: float, rule: RuleConfig) -> int:
    if ma10 <= 0:
        return 0
    gap = close / ma10 - 1
    if 0 <= gap <= rule.ma_near_pct:
        return 2
    if -rule.ma_near_pct <= gap < 0:
        return 1
    return 0


def score_candidate(
    frame: pd.DataFrame,
    actual_quote: dict[str, Any] | None,
    consensus_count: int,
    rule: RuleConfig,
) -> ScoreResult:
    """对截至信号日的数据计算四维评分与真实价格交易计划。"""
    if len(frame) < 60:
        raise ValueError("至少需要 60 个交易日才能计算四维评分")

    df = frame.copy()
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(df) < 60:
        raise ValueError("有效行情不足 60 个交易日")

    latest = df.iloc[-1]
    previous = df.iloc[-2]
    close = float(latest["close"])
    high60 = float(df["high"].tail(60).max())
    low60 = float(df["low"].tail(60).min())
    ma10 = float(df["close"].tail(10).mean())
    ma20 = float(df["close"].tail(20).mean())
    vol20 = float(df["volume"].tail(20).mean())
    volume_ratio = float(latest["volume"]) / vol20 if vol20 > 0 else 0.0
    drawdown = max(0.0, 1 - close / high60) if high60 > 0 else 0.0
    rebound = max(0.0, close / low60 - 1) if low60 > 0 else 0.0

    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(true_range.tail(14).mean())

    score_drawdown = _drawdown_score(drawdown, rule)
    score_rebound = _rebound_score(rebound, rule)
    score_ma = _ma_score(close, ma10, rule)
    score_volume = int(
        volume_ratio >= rule.volume_ratio_min and close > float(previous["close"])
    )
    total = score_drawdown + score_rebound + score_ma + score_volume

    real_close = float(actual_quote["close"]) if actual_quote and actual_quote.get("close") else None
    veto_reason: str | None = None
    if actual_quote is None or real_close is None or real_close <= 0:
        veto_reason = "真实行情缺失"
    elif not int(actual_quote.get("trade_status", 1)):
        veto_reason = "停牌"
    elif int(actual_quote.get("is_st", 0)):
        veto_reason = "ST 股票"
    elif close >= float(previous["close"]) * 1.095:
        veto_reason = "最新交易日接近涨停，不追高"

    entry_low: float | None = None
    entry_high: float | None = None
    stop_price: float | None = None
    zone = PositionZone.VETO if veto_reason else PositionZone.RIGHT

    if real_close and close > 0:
        factor = real_close / close
        prior_high20 = float(df["high"].shift(1).tail(20).max())
        low10 = float(df["low"].tail(10).min())
        supports = [value for value in (ma10, ma20, prior_high20, low10) if 0 < value <= close]
        if supports:
            support = max(supports)
            entry_low = round(support * rule.entry_low_factor * factor, 2)
            entry_high = round(support * rule.entry_high_factor * factor, 2)
            atr_stop = (support - rule.atr_stop_multiple * atr14) * factor
            bounded_stop = entry_low * (1 - rule.max_stop_pct)
            stop_price = round(max(atr_stop, bounded_stop), 2)
            if stop_price >= entry_low:
                veto_reason = "波动过小，无法生成有效止损"
            elif real_close <= stop_price:
                veto_reason = "现价已跌破止损"

            if veto_reason:
                zone = PositionZone.VETO
            elif entry_low <= real_close <= entry_high and total >= rule.min_left_score:
                zone = PositionZone.LEFT
            elif (
                entry_high < real_close <= entry_high * (1 + rule.middle_extension)
                and total >= rule.min_middle_score
            ):
                zone = PositionZone.MIDDLE
            else:
                zone = PositionZone.RIGHT
        elif veto_reason is None:
            veto_reason = "未找到有效技术支撑"
            zone = PositionZone.VETO

    confidence = (
        "高" if total >= 7 and consensus_count >= 2 else "中" if total >= 5 else "低"
    )
    rationale = (
        f"60日回撤{drawdown:.1%}，低点反弹{rebound:.1%}，"
        f"现价相对MA10{close / ma10 - 1:+.1%}，量比{volume_ratio:.2f}；"
        f"{consensus_count}个策略共振，置信度{confidence}。"
    )
    if veto_reason:
        rationale += f" 否决：{veto_reason}。"

    return ScoreResult(
        drawdown_60=drawdown,
        rebound_60=rebound,
        ma10=ma10,
        ma20=ma20,
        volume_ratio=volume_ratio,
        atr14=atr14,
        score_drawdown=score_drawdown,
        score_rebound=score_rebound,
        score_ma=score_ma,
        score_volume=score_volume,
        total_score=total,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_price=stop_price,
        zone=zone,
        veto_reason=veto_reason,
        rationale=rationale,
    )


def suggested_quantity(
    *,
    equity: float,
    cash: float,
    current_market_value: float,
    open_positions: int,
    symbol_market_value: float,
    entry_price: float | None,
    stop_price: float | None,
    zone: PositionZone,
    rule: RuleConfig,
) -> int:
    """按风险、现金、单票与总仓位限制计算 100 股整数倍建议数量。"""
    if (
        equity <= 0
        or cash <= 0
        or not entry_price
        or not stop_price
        or entry_price <= stop_price
        or zone in {PositionZone.RIGHT, PositionZone.VETO}
        or open_positions >= rule.max_positions
    ):
        return 0
    risk_budget = equity * rule.risk_per_trade
    by_risk = risk_budget / (entry_price - stop_price)
    symbol_room = max(0.0, equity * rule.max_symbol_weight - symbol_market_value)
    total_room = max(0.0, equity * rule.max_total_weight - current_market_value)
    by_value = min(cash, symbol_room, total_room) / entry_price
    quantity = min(by_risk, by_value)
    if zone == PositionZone.MIDDLE:
        quantity *= rule.middle_size_factor
    return max(0, math.floor(quantity / 100) * 100)
