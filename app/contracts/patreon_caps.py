"""Stable analytical messages produced by PatreonCaps."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, PositiveDecimal, SemVer, StrictFrozenModel, new_uuid7
from .enums import MacroRegime, PatreonCapsState, StrategyMode
from .rules import NamedValue


class PatreonCapsAssessment(StrictFrozenModel):
    """One observable PatreonCaps calculation, including non-transition evaluations."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    rule_version: SemVer
    mode: StrategyMode
    state: PatreonCapsState
    current_price: PositiveDecimal
    zone_low: PositiveDecimal
    zone_center: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    atr14: PositiveDecimal
    confluence_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confirmation_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    alignment_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    lesson_score: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100"))
    lesson_gate_passed: bool = True
    lesson_reasons: tuple[NonEmptyStr, ...] = ()
    lesson_metrics: tuple[NamedValue, ...] = ()
    patreon_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    macro_regime: MacroRegime
    macro_threshold: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    macro_signals: tuple[NonEmptyStr, ...] = ()
    macro_metrics: tuple[NamedValue, ...] = ()
    support_sources: tuple[NonEmptyStr, ...] = ()
    source_analysis_ids: tuple[UUID, ...] = ()
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self) -> PatreonCapsAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if not self.invalidation < self.zone_low <= self.zone_center <= self.zone_high:
            raise ValueError("PatreonCaps levels must satisfy invalidation < low <= center <= high")
        if len(self.support_sources) != len(set(self.support_sources)):
            raise ValueError("support sources must be unique")
        if len(self.source_analysis_ids) != len(set(self.source_analysis_ids)):
            raise ValueError("source analysis ids must be unique")
        return self


class PatreonCapsTransition(StrictFrozenModel):
    """Durable lifecycle transition for one PatreonCaps watch."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    watch_id: UUID
    symbol: Identifier
    previous_state: PatreonCapsState | None = None
    state: PatreonCapsState
    occurred_at: datetime
    rule_version: SemVer
    current_price: PositiveDecimal
    zone_low: PositiveDecimal
    zone_center: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    confluence_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confirmation_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    alignment_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    lesson_score: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100"))
    lesson_gate_passed: bool = True
    lesson_reasons: tuple[NonEmptyStr, ...] = ()
    lesson_metrics: tuple[NamedValue, ...] = ()
    patreon_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    macro_regime: MacroRegime
    macro_signals: tuple[NonEmptyStr, ...] = ()
    macro_metrics: tuple[NamedValue, ...] = ()
    confirmation_type: str | None = None
    tranche_stage: int | None = Field(default=None, ge=1, le=5)
    suggested_tranche_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    suggested_whole_shares: Decimal | None = Field(default=None, ge=Decimal("0"))
    source_analysis_ids: tuple[UUID, ...] = ()
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_transition(self) -> PatreonCapsTransition:
        if self.transition_id.version != 7 or self.watch_id.version != 7:
            raise ValueError("transition_id and watch_id must be UUIDv7")
        if self.previous_state is self.state and self.state is not PatreonCapsState.IMPULSE_RETEST:
            raise ValueError("a PatreonCaps transition must change state")
        if not self.invalidation < self.zone_low <= self.zone_center <= self.zone_high:
            raise ValueError("PatreonCaps levels are out of order")
        if self.expires_at < self.occurred_at:
            raise ValueError("expires_at cannot precede occurred_at")
        return self
