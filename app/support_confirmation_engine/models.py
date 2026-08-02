"""Immutable inputs owned by Support Confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar, SupportAssessment


@dataclass(frozen=True, slots=True)
class SupportZoneHint:
    low: Decimal
    center: Decimal
    high: Decimal
    invalidation: Decimal
    score: Decimal
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.invalidation < self.low <= self.center <= self.high:
            raise ValueError("support zone hint levels are out of order")
        if not Decimal() <= self.score <= Decimal("100"):
            raise ValueError("support zone hint score is out of range")
        if not self.sources:
            raise ValueError("support zone hint requires sources")


@dataclass(frozen=True, slots=True)
class SupportContext:
    symbol: str
    daily_bars: tuple[MarketBar, ...]
    weekly_bars: tuple[MarketBar, ...] = ()
    hourly_bars: tuple[MarketBar, ...] = ()
    previous_assessment: SupportAssessment | None = None
    zone_hint: SupportZoneHint | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("support context requires a symbol")
        for bar in (*self.daily_bars, *self.weekly_bars, *self.hourly_bars):
            if bar.symbol.strip().upper() != symbol:
                raise ValueError("all support bars must belong to the context symbol")
        if any(bar.timeframe is not BarTimeframe.DAY_1 for bar in self.daily_bars):
            raise ValueError("daily_bars must contain only 1Day bars")
        if any(bar.timeframe is not BarTimeframe.WEEK_1 for bar in self.weekly_bars):
            raise ValueError("weekly_bars must contain only 1Week bars")
        if any(bar.timeframe is not BarTimeframe.HOUR_1 for bar in self.hourly_bars):
            raise ValueError("hourly_bars must contain only 1Hour bars")
