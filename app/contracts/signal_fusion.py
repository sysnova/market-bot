"""Stable outputs of the independent cross-engine Signal Fusion process."""

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
from .enums import FusionState


class FusionAssessment(StrictFrozenModel):
    """Current deterministic fusion decision and every explicit gate."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    state: FusionState
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confidence: UnitInterval
    current_price: PositiveDecimal
    support_zone_gate: bool = False
    support_reaction_gate: bool = False
    support_gate: bool = False
    trend_gate: bool = False
    timing_gate: bool = False
    execution_gate: bool = False
    dilution_gate: bool = False
    portfolio_gate: bool = False
    reward_risk_gate: bool = False
    recovery_gate: bool = False
    trigger_price: PositiveDecimal | None = None
    entry_price: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    target_price: PositiveDecimal | None = None
    reward_risk_ratio: Decimal | None = Field(default=None, ge=Decimal("0"))
    patreon_context: NonEmptyStr | None = None
    dilution_context: NonEmptyStr | None = None
    missing_sources: tuple[NonEmptyStr, ...] = ()
    source_assessment_ids: tuple[UUID, ...] = ()
    source_analysis_ids: tuple[UUID, ...] = ()
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> FusionAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if len(self.missing_sources) != len(set(self.missing_sources)):
            raise ValueError("missing sources must be unique")
        if len(self.source_assessment_ids) != len(set(self.source_assessment_ids)):
            raise ValueError("source assessment ids must be unique")
        if len(self.source_analysis_ids) != len(set(self.source_analysis_ids)):
            raise ValueError("source analysis ids must be unique")
        if self.state is FusionState.BUY_CONFIRMED:
            gates = (
                self.support_gate,
                self.trend_gate,
                self.timing_gate,
                self.execution_gate,
                self.dilution_gate,
                self.portfolio_gate,
                self.reward_risk_gate,
            )
            if not all(gates):
                raise ValueError("BUY_CONFIRMED requires all gates")
            levels = (
                self.trigger_price,
                self.entry_price,
                self.invalidation,
                self.target_price,
                self.reward_risk_ratio,
            )
            if any(level is None for level in levels):
                raise ValueError("BUY_CONFIRMED requires complete trade levels")
        if self.state is FusionState.RECOVERY_CONFIRMED:
            gates = (
                self.support_zone_gate,
                self.support_reaction_gate,
                self.timing_gate,
                self.execution_gate,
                self.dilution_gate,
                self.portfolio_gate,
                self.reward_risk_gate,
                self.recovery_gate,
            )
            if not all(gates):
                raise ValueError("RECOVERY_CONFIRMED requires all recovery gates")
            levels = (
                self.trigger_price,
                self.entry_price,
                self.invalidation,
                self.target_price,
                self.reward_risk_ratio,
            )
            if any(level is None for level in levels):
                raise ValueError("RECOVERY_CONFIRMED requires complete trade levels")
        if (
            self.entry_price is not None
            and self.invalidation is not None
            and self.invalidation >= self.entry_price
        ):
            raise ValueError("fusion invalidation must be below entry")
        if (
            self.entry_price is not None
            and self.target_price is not None
            and self.target_price <= self.entry_price
        ):
            raise ValueError("fusion target must be above entry")
        return self


class FusionTransition(StrictFrozenModel):
    """Append-only state change emitted by Signal Fusion."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_state: FusionState | None = None
    state: FusionState
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    trigger_price: PositiveDecimal | None = None
    entry_price: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    target_price: PositiveDecimal | None = None
    reward_risk_ratio: Decimal | None = Field(default=None, ge=Decimal("0"))
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> FusionTransition:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition_id and assessment_id must be UUIDv7")
        if self.previous_state is self.state:
            raise ValueError("a fusion transition must change state")
        return self
