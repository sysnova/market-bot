"""Frozen values owned by the swing engine."""

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
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        use_enum_values=False,
    )


class SwingContext(FrozenModel):
    """Completed daily and intraday bars used in one swing evaluation."""

    symbol: Symbol
    as_of: datetime
    price: PositiveDecimal
    daily_bars: tuple[MarketBar, ...]
    intraday_bars: tuple[MarketBar, ...]

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        self._validate_series("daily", self.daily_bars)
        self._validate_series("intraday", self.intraday_bars)
        if any(bar.timeframe is not BarTimeframe.DAY_1 for bar in self.daily_bars):
            raise ValueError("daily bars must use 1Day")
        allowed = {BarTimeframe.MINUTE_15, BarTimeframe.HOUR_1}
        if any(bar.timeframe not in allowed for bar in self.intraday_bars):
            raise ValueError("intraday bars must use 15Min or 1Hour")
        if len({bar.timeframe for bar in self.intraday_bars}) > 1:
            raise ValueError("intraday bars must share one timeframe")
        return self

    def _validate_series(self, name: str, bars: tuple[MarketBar, ...]) -> None:
        timestamps = tuple(bar.timestamp for bar in bars)
        if any(left >= right for left, right in pairwise(timestamps)):
            raise ValueError(f"{name} bars must be strictly chronological and unique")
        if any(bar.timestamp > self.as_of for bar in bars):
            raise ValueError(f"{name} bar is later than as_of")
        if any(bar.symbol != self.symbol for bar in bars):
            raise ValueError(f"{name} bars must match context symbol")
        if any(not bar.is_final for bar in bars):
            raise ValueError(f"{name} bars must be final")


class SwingClassification(StrEnum):
    BREAKOUT = "breakout"
    PULLBACK = "pullback"
    RECOVERY = "recovery"
    SETUP = "setup"
    EXTENDED = "extended"
    AVOID = "avoid"
    INSUFFICIENT_DATA = "insufficient_data"


class SwingIndicators(FrozenModel):
    daily_sma20: PositiveDecimal
    daily_sma50: PositiveDecimal
    daily_sma20_slope_percent: Decimal
    daily_rsi14: Score
    atr14: PositiveDecimal
    atr_percent: PositiveDecimal
    daily_rvol20: Decimal | None
    intraday_rvol20: Decimal | None
    price_vs_sma20_percent: Decimal
    price_vs_sma50_percent: Decimal
    price_vs_resistance_percent: Decimal
    pivot_low_anchor_at: datetime | None
    pivot_low_avwap: PositiveDecimal | None
    price_vs_pivot_low_avwap_percent: Decimal | None
    breakout_anchor_at: datetime | None
    breakout_avwap: PositiveDecimal | None
    price_vs_breakout_avwap_percent: Decimal | None
    bullish_trend: bool
    bearish_trend: bool
    breakout_location: bool
    volume_confirmed: bool


class SwingLevels(FrozenModel):
    support: PositiveDecimal
    resistance: PositiveDecimal
    invalidation: PositiveDecimal
    target: PositiveDecimal
    risk_percent: PositiveDecimal
    risk_atr: PositiveDecimal
    risk_ok: bool
    levels_as_of: datetime


class SwingAnalysis(FrozenModel):
    symbol: Symbol
    as_of: datetime
    score: Score
    classification: SwingClassification
    indicators: SwingIndicators | None
    levels: SwingLevels | None
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
