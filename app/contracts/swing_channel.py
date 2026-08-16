"""Stable messages emitted by the independent four-hour Swing channel engine."""

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
    UnitInterval,
    new_uuid7,
)
from .enums import SwingChannelMaturity
from .rules import NamedValue


class SwingChannelAssessment(StrictFrozenModel):
    """One reproducible observation of an ascending RTH channel."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    assessed_at: datetime | None = None
    engine_version: SemVer
    maturity: SwingChannelMaturity
    current_price: PositiveDecimal
    pivot_a_at: datetime
    pivot_a_price: PositiveDecimal
    pivot_b_at: datetime
    pivot_b_price: PositiveDecimal
    pivot_c_at: datetime
    pivot_c_price: PositiveDecimal
    support: PositiveDecimal
    middle: PositiveDecimal
    resistance: PositiveDecimal
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    slope_per_bar: Decimal = Field(gt=Decimal("0"))
    width: PositiveDecimal
    width_atr: PositiveDecimal
    distance_to_support_atr: Decimal
    containment_ratio: UnitInterval
    support_touch_count: int = Field(ge=0)
    touch_low: PositiveDecimal | None = None
    bounce_confirmed: bool = False
    daily_swing_aligned: bool = False
    existing_maturity_aligned: bool = False
    current_swing_zone_low: PositiveDecimal | None = None
    current_swing_zone_high: PositiveDecimal | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> SwingChannelAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if not self.pivot_a_at < self.pivot_b_at < self.pivot_c_at:
            raise ValueError("channel pivots must be chronologically ordered")
        if self.pivot_b_price <= self.pivot_a_price:
            raise ValueError("pivot B must be higher than pivot A")
        if not self.invalidation < self.zone_low <= self.support <= self.zone_high:
            raise ValueError("support zone levels are out of order")
        if not self.support < self.middle < self.resistance:
            raise ValueError("channel lines are out of order")
        if self.assessed_at is not None and self.assessed_at < self.occurred_at:
            raise ValueError("assessed_at cannot precede market data")
        if self.maturity in {
            SwingChannelMaturity.L2_4H,
            SwingChannelMaturity.L3,
            SwingChannelMaturity.L4,
        } and not self.bounce_confirmed:
            raise ValueError("L2_4H or later requires a confirmed bounce")
        if self.maturity in {SwingChannelMaturity.L3, SwingChannelMaturity.L4} and not (
            self.daily_swing_aligned
        ):
            raise ValueError("L3 or L4 requires daily Swing alignment")
        if self.maturity is SwingChannelMaturity.L4 and not self.existing_maturity_aligned:
            raise ValueError("L4 requires existing L3/L4 alignment")
        if (self.current_swing_zone_low is None) != (self.current_swing_zone_high is None):
            raise ValueError("current Swing zone must be complete")
        if (
            self.current_swing_zone_low is not None
            and self.current_swing_zone_high is not None
            and self.current_swing_zone_low > self.current_swing_zone_high
        ):
            raise ValueError("current Swing zone is out of order")
        names = tuple(item.name for item in self.metrics)
        if len(names) != len(set(names)):
            raise ValueError("swing channel metrics must be unique")
        return self


class SwingChannelTransition(StrictFrozenModel):
    """Append-only maturity change for one channel assessment stream."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_maturity: SwingChannelMaturity | None = None
    maturity: SwingChannelMaturity
    current_price: PositiveDecimal
    support: PositiveDecimal
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> SwingChannelTransition:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition_id and assessment_id must be UUIDv7")
        if self.previous_maturity is self.maturity:
            raise ValueError("a swing channel transition must change maturity")
        return self
