"""Stable contracts for the independent Fibonacci SwingTrade engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    PositiveDecimal,
    SemVer,
    Sha256,
    StrictFrozenModel,
    new_uuid7,
)
from .enums import SwingTradeMaturity
from .rules import NamedValue


class SwingTradeAssessment(StrictFrozenModel):
    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    assessed_at: datetime | None = None
    engine_version: SemVer
    strategy_version: SemVer
    maturity: SwingTradeMaturity | None = None
    current_price: PositiveDecimal
    impulse_low: PositiveDecimal
    impulse_low_at: datetime
    impulse_high: PositiveDecimal
    impulse_high_at: datetime
    fibonacci_50: PositiveDecimal
    fibonacci_618: PositiveDecimal
    fibonacci_1618: PositiveDecimal
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    support_20d: PositiveDecimal
    resistance_20d: PositiveDecimal
    support_band_low: PositiveDecimal
    support_band_high: PositiveDecimal
    invalidation: PositiveDecimal
    primary_target: PositiveDecimal
    extended_target: PositiveDecimal
    atr14: PositiveDecimal
    reward_risk: Decimal
    extended_reward_risk: Decimal
    support_confluence: bool = False
    spot_in_fibonacci_zone: bool = False
    geri_assessment_id: UUID | None = None
    geri_zone_low: PositiveDecimal | None = None
    geri_zone_high: PositiveDecimal | None = None
    geri_confluence: bool = False
    eligible: bool = False
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> SwingTradeAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if not self.impulse_low < self.impulse_high:
            raise ValueError("SwingTrade impulse must have positive range")
        if not self.impulse_low_at < self.impulse_high_at:
            raise ValueError("SwingTrade LONG impulse low must precede high")
        if not self.fibonacci_618 < self.fibonacci_50 < self.impulse_high:
            raise ValueError("SwingTrade Fibonacci retracement levels are out of order")
        if (self.zone_low, self.zone_high) != (self.fibonacci_618, self.fibonacci_50):
            raise ValueError("SwingTrade entry zone must be Fibonacci 61.8-50")
        if (
            not self.invalidation
            < self.support_band_low
            <= self.support_20d
            <= self.support_band_high
        ):
            raise ValueError("SwingTrade support and invalidation are out of order")
        if self.primary_target != self.resistance_20d:
            raise ValueError("SwingTrade primary target must be 20d resistance")
        if self.geri_assessment_id is not None and self.geri_assessment_id.version != 7:
            raise ValueError("geri_assessment_id must be UUIDv7")
        if (self.geri_zone_low is None) != (self.geri_zone_high is None):
            raise ValueError("SwingTrade GERI zone must be complete")
        if self.maturity is SwingTradeMaturity.ST4 and not self.geri_confluence:
            raise ValueError("SwingTrade ST4 requires GERI confluence")
        return self


class SwingTradeTransition(StrictFrozenModel):
    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    strategy_version: SemVer
    previous_maturity: SwingTradeMaturity | None = None
    maturity: SwingTradeMaturity | None = None
    current_price: PositiveDecimal
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    primary_target: PositiveDecimal
    reward_risk: Decimal
    eligible: bool
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> SwingTradeTransition:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("SwingTrade transition IDs must be UUIDv7")
        if not self.invalidation < self.zone_low <= self.zone_high:
            raise ValueError("SwingTrade transition levels are out of order")
        return self
