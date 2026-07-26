"""Stable messages emitted when a persisted entry thesis changes state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, PositiveDecimal, StrictFrozenModel, new_uuid7
from .enums import AnalysisHorizon, EntryWatchStatus


class EntryWatchTransition(StrictFrozenModel):
    """One durable and human-readable transition of an entry opportunity."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    watch_id: UUID
    symbol: Identifier
    previous_status: EntryWatchStatus | None = None
    status: EntryWatchStatus
    occurred_at: datetime
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    current_price: PositiveDecimal
    watch_expires_at: datetime
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    horizons: tuple[AnalysisHorizon, ...] = Field(min_length=1)
    source_analysis_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_transition(self) -> EntryWatchTransition:
        if self.transition_id.version != 7 or self.watch_id.version != 7:
            raise ValueError("transition_id and watch_id must be UUIDv7")
        if any(value.version != 7 for value in self.source_analysis_ids):
            raise ValueError("source_analysis_ids must contain UUIDv7 values")
        if self.invalidation >= self.zone_low or self.zone_low > self.zone_high:
            raise ValueError("entry watch levels must satisfy invalidation < low <= high")
        if (
            self.status is not EntryWatchStatus.EXPIRED
            and self.watch_expires_at < self.occurred_at
        ):
            raise ValueError("watch_expires_at cannot precede occurred_at")
        if self.previous_status is self.status:
            raise ValueError("a transition must change status")
        if len(self.horizons) != len(set(self.horizons)):
            raise ValueError("horizons must be unique")
        if len(self.source_analysis_ids) != len(set(self.source_analysis_ids)):
            raise ValueError("source_analysis_ids must be unique")
        return self
