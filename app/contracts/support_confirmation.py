"""Stable messages emitted by the independent Support Confirmation engine."""

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
from .enums import SupportConfirmationType, SupportState, SupportZonePosition
from .rules import NamedValue


class StructuralSupportReference(StrictFrozenModel):
    """Higher-timeframe support retained even when it is not actionable nearby."""

    source: NonEmptyStr
    price: PositiveDecimal
    distance_percent: Decimal = Field(ge=Decimal("0"))
    distance_atr: Decimal = Field(ge=Decimal("0"))


class SupportAssessment(StrictFrozenModel):
    """Current support reaction and structural-reversal evidence for one symbol."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    state: SupportState
    confirmation_type: SupportConfirmationType = SupportConfirmationType.NONE
    current_price: PositiveDecimal
    zone_low: PositiveDecimal | None = None
    zone_center: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    support_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reaction_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reversal_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confidence: UnitInterval
    data_as_of: datetime | None = None
    assessed_at: datetime | None = None
    liquidity_sweep: bool = False
    higher_high: bool = False
    higher_low: bool = False
    b_wave_risk: bool = False
    zone_position: SupportZonePosition = SupportZonePosition.NO_ZONE
    zone_distance_percent: Decimal | None = Field(default=None, ge=Decimal("0"))
    zone_distance_atr: Decimal | None = Field(default=None, ge=Decimal("0"))
    touch_count: int = Field(default=0, ge=0)
    touch_age_sessions: int | None = Field(default=None, ge=0)
    four_hour_reclaim: bool = False
    four_hour_higher_high: bool = False
    four_hour_higher_low: bool = False
    actionability_score: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100"))
    support_sources: tuple[NonEmptyStr, ...] = ()
    structural_supports: tuple[StructuralSupportReference, ...] = ()
    impulse_origin: PositiveDecimal | None = None
    impulse_origin_at: datetime | None = None
    impulse_peak: PositiveDecimal | None = None
    impulse_advance_percent: Decimal | None = Field(default=None, ge=Decimal("0"))
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> SupportAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if self.data_as_of is not None and self.data_as_of != self.occurred_at:
            raise ValueError("data_as_of must match legacy occurred_at")
        data_as_of = self.data_as_of or self.occurred_at
        if self.assessed_at is not None and self.assessed_at < data_as_of:
            raise ValueError("assessed_at cannot precede data_as_of")
        levels = (self.zone_low, self.zone_center, self.zone_high, self.invalidation)
        if self.state not in {
            SupportState.NO_KEY_SUPPORT,
            SupportState.NO_NEARBY_SUPPORT,
        } and any(level is None for level in levels):
            raise ValueError("support state requires a complete zone")
        if all(level is not None for level in levels):
            assert self.invalidation is not None
            assert self.zone_low is not None
            assert self.zone_center is not None
            assert self.zone_high is not None
            if not self.invalidation < self.zone_low <= self.zone_center <= self.zone_high:
                raise ValueError("support zone levels are out of order")
        elif self.zone_position is not SupportZonePosition.NO_ZONE:
            raise ValueError("support zone position requires a complete zone")
        if len(self.support_sources) != len(set(self.support_sources)):
            raise ValueError("support sources must be unique")
        structural_sources = tuple(item.source for item in self.structural_supports)
        if len(structural_sources) != len(set(structural_sources)):
            raise ValueError("structural support sources must be unique")
        if any(item.price >= self.current_price for item in self.structural_supports):
            raise ValueError("structural support references must be below current price")
        impulse = (
            self.impulse_origin,
            self.impulse_origin_at,
            self.impulse_peak,
            self.impulse_advance_percent,
        )
        if any(item is not None for item in impulse) and not all(
            item is not None for item in impulse
        ):
            raise ValueError("impulse reference fields must be complete")
        if (
            self.impulse_origin is not None
            and self.impulse_peak is not None
            and self.impulse_origin >= self.impulse_peak
        ):
            raise ValueError("impulse peak must be above its origin")
        if self.impulse_origin is not None and self.impulse_origin >= self.current_price:
            raise ValueError("impulse origin support must be below current price")
        metric_names = tuple(item.name for item in self.metrics)
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("support metrics must be unique by name")
        return self


class SupportTransition(StrictFrozenModel):
    """Append-only change in the state of a support thesis."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_state: SupportState | None = None
    state: SupportState
    confirmation_type: SupportConfirmationType = SupportConfirmationType.NONE
    support_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reaction_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reversal_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> SupportTransition:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition_id and assessment_id must be UUIDv7")
        if self.previous_state is self.state:
            raise ValueError("a support transition must change state")
        return self
