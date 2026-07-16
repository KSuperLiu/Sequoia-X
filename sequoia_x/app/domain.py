"""应用层公共枚举与默认规则。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class AccountType(_StringEnum):
    PAPER = "PAPER"
    REAL_LEDGER = "REAL_LEDGER"


class PositionZone(_StringEnum):
    LEFT = "LEFT"
    MIDDLE = "MIDDLE"
    RIGHT = "RIGHT"
    VETO = "VETO"


class PlanStatus(_StringEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class RunStatus(_StringEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CandidateStatus(_StringEnum):
    NEW = "NEW"
    WATCHING = "WATCHING"
    PLANNED = "PLANNED"
    BOUGHT = "BOUGHT"
    DROPPED = "DROPPED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class RuleConfig:
    """四维评分、交易计划与组合风控的版本化默认值。"""

    drawdown_score3_min: float = 0.25
    drawdown_score3_max: float = 0.40
    drawdown_score2_min: float = 0.15
    drawdown_score1_min: float = 0.05
    rebound_score2_min: float = 0.05
    rebound_score2_max: float = 0.15
    rebound_score1_max: float = 0.25
    ma_near_pct: float = 0.05
    volume_ratio_min: float = 1.0
    entry_low_factor: float = 0.99
    entry_high_factor: float = 1.02
    middle_extension: float = 0.05
    atr_stop_multiple: float = 1.5
    max_stop_pct: float = 0.08
    min_left_score: int = 6
    min_middle_score: int = 5
    risk_per_trade: float = 0.01
    max_symbol_weight: float = 0.20
    max_total_weight: float = 0.60
    max_positions: int = 5
    middle_size_factor: float = 0.50
    max_holding_days: int = 20

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> RuleConfig:
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in raw.items() if key in allowed})
