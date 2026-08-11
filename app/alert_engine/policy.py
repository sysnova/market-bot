"""Explicit configuration for deterministic alert aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app.contracts import AnalysisHorizon


@dataclass(frozen=True, slots=True)
class HorizonPolicy:
    horizon: AnalysisHorizon
    weight: Decimal
    max_age: timedelta

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("horizon weight must be positive")
        if self.max_age <= timedelta(0):
            raise ValueError("horizon max_age must be positive")


def _default_horizons() -> tuple[HorizonPolicy, ...]:
    return (
        HorizonPolicy(AnalysisHorizon.LONG_TERM, Decimal("0.25"), timedelta(days=7)),
        HorizonPolicy(AnalysisHorizon.DILUTION, Decimal("0.20"), timedelta(hours=24)),
        HorizonPolicy(AnalysisHorizon.SWING, Decimal("0.30"), timedelta(hours=8)),
        HorizonPolicy(AnalysisHorizon.INTRADAY, Decimal("0.25"), timedelta(minutes=30)),
        HorizonPolicy(
            AnalysisHorizon.VOLUME_STRUCTURE,
            Decimal("0.10"),
            timedelta(days=14),
        ),
    )


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    """All alert policy knobs, including time, are explicit and immutable."""

    horizons: tuple[HorizonPolicy, ...] = field(default_factory=_default_horizons)
    required_horizons: tuple[AnalysisHorizon, ...] = (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )
    min_fresh_horizons: int = 3
    watch_threshold: Decimal = Decimal("60")
    action_threshold: Decimal = Decimal("75")
    critical_threshold: Decimal = Decimal("90")
    cooldown: timedelta = timedelta(minutes=15)
    alert_ttl: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        available = tuple(item.horizon for item in self.horizons)
        if len(available) != len(set(available)):
            raise ValueError("horizon policies must be unique")
        if set(available) != set(AnalysisHorizon):
            raise ValueError("policy must configure all analysis horizons")
        if not set(self.required_horizons).issubset(available):
            raise ValueError("required horizons must be configured")
        if not 1 <= self.min_fresh_horizons <= len(available):
            raise ValueError("min_fresh_horizons is out of range")
        if not ZERO <= self.watch_threshold < self.action_threshold < self.critical_threshold:
            raise ValueError("severity thresholds must be strictly increasing")
        if self.critical_threshold > HUNDRED:
            raise ValueError("critical threshold cannot exceed 100")
        if self.cooldown <= timedelta(0) or self.alert_ttl <= timedelta(0):
            raise ValueError("cooldown and alert_ttl must be positive")

    def for_horizon(self, horizon: AnalysisHorizon) -> HorizonPolicy:
        return next(item for item in self.horizons if item.horizon is horizon)


ZERO = Decimal("0")
HUNDRED = Decimal("100")
