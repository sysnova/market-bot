"""Operational Order Flow evidence evaluated over an existing support zone."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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
from .order_flow import OrderFlowStateKind


class OrderFlowSupportDisposition(StrEnum):
    """Incremental meaning of microstructure at an independently-owned support."""

    CONFIRMS_SUPPORT = "CONFIRMS_SUPPORT"
    WARNS_BREAKDOWN = "WARNS_BREAKDOWN"
    NEUTRAL = "NEUTRAL"


class OrderFlowSupportAssessment(StrictFrozenModel):
    """Link one Order Flow state to one Support assessment without owning geometry."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    disposition: OrderFlowSupportDisposition
    support_assessment_id: UUID
    order_flow_state_id: UUID
    support_occurred_at: datetime
    order_flow_occurred_at: datetime
    current_price: PositiveDecimal
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    order_flow_state: OrderFlowStateKind
    confidence: UnitInterval
    data_quality: UnitInterval
    quote_fresh: bool
    fresh_until: datetime
    fresh_at_assessment: bool = True
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_event_ids: tuple[UUID, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> OrderFlowSupportAssessment:
        ids = (
            self.assessment_id,
            self.support_assessment_id,
            self.order_flow_state_id,
            *self.source_event_ids,
        )
        if any(identifier.version != 7 for identifier in ids):
            raise ValueError("Order Flow Support identifiers must be UUIDv7")
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must be unique")
        if self.zone_low > self.zone_high:
            raise ValueError("zone_low cannot exceed zone_high")
        if self.order_flow_occurred_at > self.occurred_at:
            raise ValueError("Order Flow evidence cannot be later than assessment")
        if self.support_occurred_at > self.occurred_at:
            raise ValueError("Support evidence cannot be later than assessment")
        if self.fresh_until < self.order_flow_occurred_at:
            raise ValueError("fresh_until cannot precede Order Flow evidence")
        if self.fresh_at_assessment and self.occurred_at > self.fresh_until:
            raise ValueError("fresh assessment cannot already be expired")
        if self.fresh_at_assessment and not self.quote_fresh:
            raise ValueError("fresh assessment requires a fresh quote")
        if (
            self.disposition is not OrderFlowSupportDisposition.NEUTRAL
            and self.order_flow_state is OrderFlowStateKind.NEUTRAL
        ):
            raise ValueError("directional disposition requires directional Order Flow")
        return self
