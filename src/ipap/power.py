"""Section 7 power management.

The RFC table labels the >60% band "CRITICAL", which collides with the
CRITICAL message priority; this implementation calls that band FULL but keeps
the behaviour of every row unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .constants import MessageType


class PowerLevel(StrEnum):
    FULL = "FULL"  # > 60%: LLM allowed, all features
    NORMAL = "NORMAL"  # 30-60%: LLM allowed, shortened timeouts
    LOW = "LOW"  # 10-30%: LLM not allowed, reply DEFER
    EMERGENCY = "EMERGENCY"  # < 10%: reject everything except ABORT


@dataclass(frozen=True)
class PowerThresholds:
    full_above: float = 60.0
    normal_from: float = 30.0
    low_from: float = 10.0
    normal_timeout_factor: float = 0.5

    def classify(self, percent: float) -> PowerLevel:
        if percent > self.full_above:
            return PowerLevel.FULL
        if percent >= self.normal_from:
            return PowerLevel.NORMAL
        if percent >= self.low_from:
            return PowerLevel.LOW
        return PowerLevel.EMERGENCY

    def llm_allowed(self, level: PowerLevel) -> bool:
        return level in (PowerLevel.FULL, PowerLevel.NORMAL)

    def timeout_factor(self, level: PowerLevel) -> float:
        return self.normal_timeout_factor if level is PowerLevel.NORMAL else 1.0

    def accepts(self, msg_type: MessageType, level: PowerLevel) -> bool:
        return level is not PowerLevel.EMERGENCY or msg_type is MessageType.ABORT


class Battery:
    """A simple state-of-charge model in percent."""

    def __init__(self, percent: float = 100.0) -> None:
        self._percent = 0.0
        self.percent = percent

    @property
    def percent(self) -> float:
        return self._percent

    @percent.setter
    def percent(self, value: float) -> None:
        self._percent = min(100.0, max(0.0, float(value)))

    def consume(self, percent: float) -> None:
        self.percent = self._percent - percent
