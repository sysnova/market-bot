"""Frozen input and output values owned by the long-term engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts import BarTimeframe, MarketBar

PositiveDecimal = Annotated[Decimal, Field(gt=Decimal("0"))]
Score = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"))]
Symbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z][A-Z0-9.-]{0,14}$"),
]


class FrozenModel(BaseModel):
    """Strict immutable base used by this engine's local boundary."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        use_enum_values=False,
    )


class LongTermContext(FrozenModel):
    """Normalized historical context supplied by the composition layer."""

    symbol: Symbol
    as_of: datetime
    price: PositiveDecimal
    daily_bars: tuple[MarketBar, ...]
    weekly_bars: tuple[MarketBar, ...]

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        series = (
            ("daily", self.daily_bars, BarTimeframe.DAY_1),
            ("weekly", self.weekly_bars, BarTimeframe.WEEK_1),
        )
        for name, bars, timeframe in series:
            timestamps = tuple(bar.timestamp for bar in bars)
            if any(left >= right for left, right in pairwise(timestamps)):
                raise ValueError(f"{name} bars must be strictly chronological and unique")
            if any(timestamp > self.as_of for timestamp in timestamps):
                raise ValueError(f"{name} bar is later than as_of")
            if any(bar.symbol != self.symbol for bar in bars):
                raise ValueError(f"{name} bars must match context symbol")
            if any(bar.timeframe is not timeframe for bar in bars):
                raise ValueError(f"{name} bars have the wrong timeframe")
            if any(not bar.is_final for bar in bars):
                raise ValueError(f"{name} bars must be final")
        return self


class LongTermClassification(StrEnum):
    """Chart-state classification; none of these values submit an order."""

    BUY_ZONE = "buy_zone"
    SETUP = "setup"
    WATCH_PULLBACK = "watch_pullback"
    EXTENDED = "extended"
    AVOID = "avoid"
    INSUFFICIENT_DATA = "insufficient_data"


class LongTermBias(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


class EntryZoneStatus(StrEnum):
    IN_BUY_ZONE = "in_buy_zone"
    ABOVE_BUY_ZONE = "above_buy_zone"
    BELOW_BUY_ZONE = "below_buy_zone"
    UNKNOWN = "unknown"


class TrendTemplate(FrozenModel):
    score: Score
    passed: bool
    passed_criteria: tuple[str, ...]
    failed_criteria: tuple[str, ...]


class LongTermIndicators(FrozenModel):
    daily_sma20: Decimal
    daily_sma50: Decimal
    daily_sma150: Decimal
    daily_sma200: Decimal
    weekly_sma10: Decimal
    weekly_sma30: Decimal
    weekly_sma50: Decimal
    weekly_sma200: Decimal | None
    daily_rsi14: Decimal
    weekly_rsi14: Decimal
    daily_rvol20: Decimal | None
    weekly_rvol10: Decimal | None
    daily_price_vs_sma50_percent: Decimal
    weekly_price_vs_sma10_percent: Decimal
    weekly_price_vs_sma30_percent: Decimal
    weekly_price_vs_sma50_percent: Decimal
    weekly_price_vs_sma200_percent: Decimal | None
    weekly_sma30_slope_percent: Decimal
    weekly_sma50_slope_percent: Decimal
    distance_to_high52_percent: Decimal
    distance_from_low52_percent: Decimal
    distribution_weeks: int
    higher_weekly_lows: bool
    trend_template: TrendTemplate


class LongTermLevels(FrozenModel):
    support: PositiveDecimal
    resistance: PositiveDecimal
    high52: PositiveDecimal
    low52: PositiveDecimal
    buy_zone_low: PositiveDecimal
    buy_zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    entry_zone_status: EntryZoneStatus
    levels_as_of: datetime


class LongTermAnalysis(FrozenModel):
    symbol: Symbol
    as_of: datetime
    score: Score
    setup_score: Score
    entry_score: Score
    classification: LongTermClassification
    bias: LongTermBias
    indicators: LongTermIndicators | None
    levels: LongTermLevels | None
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
