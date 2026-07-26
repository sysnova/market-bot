"""Frozen input and detailed output values owned by Intraday v1."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts import BarTimeframe, MarketBar

Score = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"))]
PositiveDecimal = Annotated[Decimal, Field(gt=Decimal("0"))]
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


class IntradayContext(FrozenModel):
    """Completed normalized bars used in one clock-free evaluation."""

    symbol: Symbol
    as_of: datetime
    minute_bars: tuple[MarketBar, ...]
    five_minute_bars: tuple[MarketBar, ...] = ()

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        self._validate_series("1Min", self.minute_bars, BarTimeframe.MINUTE_1)
        self._validate_series("5Min", self.five_minute_bars, BarTimeframe.MINUTE_5)
        return self

    def _validate_series(
        self,
        name: str,
        bars: tuple[MarketBar, ...],
        timeframe: BarTimeframe,
    ) -> None:
        timestamps = tuple(bar.timestamp for bar in bars)
        if any(left >= right for left, right in pairwise(timestamps)):
            raise ValueError(f"{name} bars must be strictly chronological and unique")
        if any(bar.timestamp > self.as_of for bar in bars):
            raise ValueError(f"{name} bar is later than as_of")
        if any(bar.symbol != self.symbol for bar in bars):
            raise ValueError(f"{name} bars must match context symbol")
        if any(bar.timeframe is not timeframe for bar in bars):
            raise ValueError(f"{name} bars have the wrong timeframe")
        if any(not bar.is_final for bar in bars):
            raise ValueError(f"{name} bars must be final")


class IntradaySetup(StrEnum):
    BULLISH_BREAKOUT = "bullish_breakout"
    BULLISH_VWAP_RECLAIM = "bullish_vwap_reclaim"
    BEARISH_BREAKDOWN = "bearish_breakdown"
    BEARISH_VWAP_REJECTION = "bearish_vwap_rejection"
    NO_TRIGGER = "no_trigger"
    INSUFFICIENT_DATA = "insufficient_data"


class IntradayIndicators(FrozenModel):
    price: PositiveDecimal
    session_vwap: PositiveDecimal
    previous_session_vwap: PositiveDecimal
    relative_volume: Decimal = Field(ge=Decimal("0"))
    ema9: PositiveDecimal
    ema20: PositiveDecimal
    momentum_5_percent: Decimal
    atr14: PositiveDecimal
    atr_percent: PositiveDecimal
    prior_range_high: PositiveDecimal
    prior_range_low: PositiveDecimal
    price_vs_vwap_percent: Decimal
    five_minute_bias: str


class IntradayLevels(FrozenModel):
    reference_price: PositiveDecimal
    invalidation_level: PositiveDecimal
    objective_level: PositiveDecimal
    risk_percent: PositiveDecimal
    reward_risk_ratio: PositiveDecimal
    risk_ok: bool


class IntradayAnalysis(FrozenModel):
    symbol: Symbol
    as_of: datetime
    setup: IntradaySetup
    score: Score
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    indicators: IntradayIndicators | None
    levels: IntradayLevels | None
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
