"""Stable analytical contracts emitted by the intraday scalping engine."""

from __future__ import annotations

from datetime import datetime
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


class ScalpState(StrEnum):
    """Causal maturity of one analytical intraday setup."""

    WATCHING = "WATCHING"
    ARMED = "ARMED"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    MANAGING = "MANAGING"
    EXIT_CONFIRMED = "EXIT_CONFIRMED"
    INVALIDATED = "INVALIDATED"


class ScalpSetup(StrEnum):
    """Setup families supported by the first scalping policy."""

    NONE = "NONE"
    SUPPORT_REVERSAL = "SUPPORT_REVERSAL"
    VWAP_RECLAIM = "VWAP_RECLAIM"
    VWAP_REJECTION = "VWAP_REJECTION"


class ScalpDirection(StrEnum):
    """Analytical direction; it is not a broker order side."""

    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class ScalpExitReason(StrEnum):
    """Why a mature setup stopped being actionable."""

    STOP = "STOP"
    TARGET = "TARGET"
    ORDER_FLOW_REVERSAL = "ORDER_FLOW_REVERSAL"
    MAX_HOLD = "MAX_HOLD"
    DATA_QUALITY = "DATA_QUALITY"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"


class ScalpAssessment(StrictFrozenModel):
    """Current analytical state of one same-session scalping setup."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    state: ScalpState
    setup: ScalpSetup = ScalpSetup.NONE
    direction: ScalpDirection = ScalpDirection.NONE
    current_price: PositiveDecimal
    bid_price: PositiveDecimal
    ask_price: PositiveDecimal
    session_vwap: PositiveDecimal
    spread_bps: Decimal = Field(ge=Decimal("0"))
    order_flow_confidence: UnitInterval
    entry_price: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    target: PositiveDecimal | None = None
    max_hold_seconds: int | None = Field(default=None, gt=0)
    support_low: PositiveDecimal | None = None
    support_high: PositiveDecimal | None = None
    entry_confirmed_at: datetime | None = None
    exit_reason: ScalpExitReason | None = None
    source_order_flow_state_id: UUID | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if (
            self.source_order_flow_state_id is not None
            and self.source_order_flow_state_id.version != 7
        ):
            raise ValueError("source_order_flow_state_id must be UUIDv7")
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price cannot exceed ask_price")
        if (self.support_low is None) != (self.support_high is None):
            raise ValueError("support zone requires both support_low and support_high")
        if (
            self.support_low is not None
            and self.support_high is not None
            and self.support_low > self.support_high
        ):
            raise ValueError("support_low cannot exceed support_high")

        levels = (self.entry_price, self.invalidation, self.target, self.max_hold_seconds)
        if self.state is ScalpState.WATCHING:
            if self.setup is not ScalpSetup.NONE or self.direction is not ScalpDirection.NONE:
                raise ValueError("watching assessment cannot claim an entry setup")
            if any(value is not None for value in levels):
                raise ValueError("watching assessment cannot expose trade levels")
        else:
            if self.setup is ScalpSetup.NONE or self.direction is ScalpDirection.NONE:
                raise ValueError("actionable assessment requires setup and direction")
            if any(value is None for value in levels):
                raise ValueError("actionable assessment requires complete trade levels")
            assert self.entry_price is not None
            assert self.invalidation is not None
            assert self.target is not None
            if self.direction is ScalpDirection.LONG:
                if not self.invalidation < self.entry_price < self.target:
                    raise ValueError("long levels must satisfy invalidation < entry_price < target")
            elif not self.target < self.entry_price < self.invalidation:
                raise ValueError("short levels must satisfy target < entry_price < invalidation")
            if self.setup is ScalpSetup.SUPPORT_REVERSAL and self.support_low is None:
                raise ValueError("support reversal requires a support zone")

        entered = self.state in {
            ScalpState.ENTRY_CONFIRMED,
            ScalpState.MANAGING,
            ScalpState.EXIT_CONFIRMED,
        }
        if entered != (self.entry_confirmed_at is not None):
            raise ValueError("entered states require entry_confirmed_at")
        if self.entry_confirmed_at is not None and self.entry_confirmed_at > self.occurred_at:
            raise ValueError("entry_confirmed_at cannot follow occurred_at")
        terminal = self.state in {ScalpState.EXIT_CONFIRMED, ScalpState.INVALIDATED}
        if terminal != (self.exit_reason is not None):
            raise ValueError("terminal states require exit_reason")
        return self


class ScalpTransition(StrictFrozenModel):
    """Append-only evidence for one material scalping-state change."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_state: ScalpState | None = None
    state: ScalpState
    setup: ScalpSetup
    direction: ScalpDirection
    reference_price: PositiveDecimal
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition_id and assessment_id must be UUIDv7")
        if self.previous_state is self.state:
            raise ValueError("a scalp transition must change state")
        return self
