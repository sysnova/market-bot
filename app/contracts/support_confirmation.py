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
from .enums import SupportConfirmationType, SupportState
from .rules import NamedValue


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
    liquidity_sweep: bool = False
    higher_high: bool = False
    higher_low: bool = False
    b_wave_risk: bool = False
    support_sources: tuple[NonEmptyStr, ...] = ()
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> SupportAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        levels = (self.zone_low, self.zone_center, self.zone_high, self.invalidation)
        if self.state is not SupportState.NO_KEY_SUPPORT and any(
            level is None for level in levels
        ):
            raise ValueError("support state requires a complete zone")
        if all(level is not None for level in levels):
            assert self.invalidation is not None
            assert self.zone_low is not None
            assert self.zone_center is not None
            assert self.zone_high is not None
            if not self.invalidation < self.zone_low <= self.zone_center <= self.zone_high:
                raise ValueError("support zone levels are out of order")
        if len(self.support_sources) != len(set(self.support_sources)):
            raise ValueError("support sources must be unique")
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
