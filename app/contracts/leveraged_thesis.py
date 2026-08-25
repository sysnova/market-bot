"""Stable analytical contracts for directional leveraged-instrument theses."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Self
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
from .enums import PatternDirection, SupportState, SupportZonePosition
from .order_flow import OrderFlowStateKind


class LeveragedExposure(StrEnum):
    """Economic exposure obtained by buying the selected instrument."""

    NONE = "NONE"
    LONG_1X = "LONG_1X"
    LONG_2X = "LONG_2X"
    INVERSE_2X = "INVERSE_2X"


class LeveragedThesisState(StrEnum):
    """Causal maturity of one intraday directional thesis."""

    OBSERVING = "OBSERVING"
    EARLY_FLOW = "EARLY_FLOW"
    STRUCTURE_ARMED = "STRUCTURE_ARMED"
    BUY_CONFIRMED = "BUY_CONFIRMED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class LeveragedThesisAssessment(StrictFrozenModel):
    """Current human-decision support state; it can never express an order."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    underlying_symbol: Identifier
    instrument_symbol: Identifier | None = None
    occurred_at: datetime
    expires_at: datetime
    engine_version: SemVer
    state: LeveragedThesisState
    direction: PatternDirection = PatternDirection.NEUTRAL
    exposure: LeveragedExposure = LeveragedExposure.NONE
    underlying_price: PositiveDecimal
    instrument_bid: PositiveDecimal | None = None
    instrument_ask: PositiveDecimal | None = None
    spread_bps: Decimal | None = Field(default=None, ge=Decimal("0"))
    underlying_flow_state: OrderFlowStateKind | None = None
    underlying_flow_confidence: UnitInterval | None = None
    instrument_flow_state: OrderFlowStateKind | None = None
    instrument_flow_confidence: UnitInterval | None = None
    support_state: SupportState | None = None
    support_zone_position: SupportZonePosition | None = None
    support_zone_low: PositiveDecimal | None = None
    support_zone_high: PositiveDecimal | None = None
    support_invalidation: PositiveDecimal | None = None
    support_distance_percent: Decimal | None = Field(default=None, ge=Decimal("0"))
    support_actionability_score: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("100")
    )
    structure_score: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    source_analysis_id: UUID | None = None
    source_underlying_flow_state_id: UUID | None = None
    source_instrument_flow_state_id: UUID | None = None
    source_support_assessment_id: UUID | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        _require_utc(self.occurred_at, "occurred_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.occurred_at:
            raise ValueError("expires_at must follow occurred_at")
        for name, value in (
            ("source_analysis_id", self.source_analysis_id),
            ("source_underlying_flow_state_id", self.source_underlying_flow_state_id),
            ("source_instrument_flow_state_id", self.source_instrument_flow_state_id),
            ("source_support_assessment_id", self.source_support_assessment_id),
        ):
            if value is not None and value.version != 7:
                raise ValueError(f"{name} must be UUIDv7")

        directional = self.state in {
            LeveragedThesisState.EARLY_FLOW,
            LeveragedThesisState.STRUCTURE_ARMED,
            LeveragedThesisState.BUY_CONFIRMED,
            LeveragedThesisState.CANCELLED,
        }
        if directional and (
            self.direction is PatternDirection.NEUTRAL
            or self.exposure is LeveragedExposure.NONE
            or self.instrument_symbol is None
        ):
            raise ValueError("actionable thesis states require directional instrument exposure")
        quote_values = (self.instrument_bid, self.instrument_ask, self.spread_bps)
        if any(value is not None for value in quote_values) and any(
            value is None for value in quote_values
        ):
            raise ValueError("instrument quote evidence must be complete")
        if (
            self.instrument_bid is not None
            and self.instrument_ask is not None
            and self.instrument_bid > self.instrument_ask
        ):
            raise ValueError("instrument bid cannot exceed ask")
        support_levels = (
            self.support_zone_low,
            self.support_zone_high,
            self.support_invalidation,
        )
        if any(value is not None for value in support_levels) and any(
            value is None for value in support_levels
        ):
            raise ValueError("support level evidence must be complete")
        if (
            self.support_zone_low is not None
            and self.support_zone_high is not None
            and self.support_invalidation is not None
            and not self.support_invalidation < self.support_zone_low <= self.support_zone_high
        ):
            raise ValueError("support levels are out of order")
        if self.state is LeveragedThesisState.BUY_CONFIRMED:
            if any(value is None for value in quote_values):
                raise ValueError("BUY_CONFIRMED requires executable instrument quote evidence")
            if self.instrument_flow_state is None or self.instrument_flow_confidence is None:
                raise ValueError("BUY_CONFIRMED requires instrument order-flow evidence")
            if self.structure_score is None:
                raise ValueError("BUY_CONFIRMED requires intraday structure evidence")
        return self


class LeveragedThesisTransition(StrictFrozenModel):
    """Append-only material maturity or selected-instrument change."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    underlying_symbol: Identifier
    instrument_symbol: Identifier | None = None
    occurred_at: datetime
    engine_version: SemVer
    previous_state: LeveragedThesisState | None = None
    state: LeveragedThesisState
    previous_instrument_symbol: Identifier | None = None
    direction: PatternDirection
    exposure: LeveragedExposure
    reference_price: PositiveDecimal
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition identifiers must be UUIDv7")
        _require_utc(self.occurred_at, "occurred_at")
        if (
            self.previous_state is self.state
            and self.previous_instrument_symbol == self.instrument_symbol
        ):
            raise ValueError("transition must change maturity or selected instrument")
        return self


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
