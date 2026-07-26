"""Frozen domain state owned by the entry watcher."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import EntryWatchStatus

PositiveDecimal = Annotated[Decimal, Field(gt=Decimal("0"))]
NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal("0"))]


class EntryWatch(BaseModel):
    """Original thesis plus its latest durable lifecycle state."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    watch_id: UUID
    symbol: str
    status: EntryWatchStatus
    armed_at: datetime
    updated_at: datetime
    expires_at: datetime
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    original_price: PositiveDecimal
    current_price: PositiveDecimal
    correction_target_percent: NonNegativeDecimal
    source_analysis_id: UUID
    source_context_hash: str
    anchor_snapshot: dict[str, Any]
    terminal_at: datetime | None = None

    @model_validator(mode="after")
    def validate_watch(self) -> EntryWatch:
        if self.watch_id.version != 7 or self.source_analysis_id.version != 7:
            raise ValueError("watch identity must use UUIDv7")
        if self.invalidation >= self.zone_low or self.zone_low > self.zone_high:
            raise ValueError("entry levels must satisfy invalidation < low <= high")
        if self.updated_at < self.armed_at or self.expires_at <= self.armed_at:
            raise ValueError("entry watch timestamps are out of order")
        terminal = self.status in {
            EntryWatchStatus.TRIGGERED,
            EntryWatchStatus.INVALIDATED,
            EntryWatchStatus.EXPIRED,
        }
        if terminal != (self.terminal_at is not None):
            raise ValueError("terminal_at must match terminal status")
        return self
