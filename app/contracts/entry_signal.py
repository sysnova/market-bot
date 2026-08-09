"""Stable, source-agnostic entry-decision messages."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, PositiveDecimal, SemVer, StrictFrozenModel, new_uuid7
from .enums import AnalysisHorizon, EntryMaturityLevel, EntrySignalFamily


class EntrySignal(StrictFrozenModel):
    """One analytical entry decision; never a broker order or sizing instruction."""

    signal_id: UUID = Field(default_factory=new_uuid7)
    family: EntrySignalFamily
    maturity: EntryMaturityLevel | None = None
    symbol: Identifier
    created_at: datetime
    setup_id: NonEmptyStr
    entry_price: PositiveDecimal
    horizons: tuple[AnalysisHorizon, ...] = Field(min_length=1)
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    targets: tuple[PositiveDecimal, ...] = ()
    policy_id: Identifier
    policy_version: SemVer
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_event_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_signal(self) -> EntrySignal:
        if self.signal_id.version != 7:
            raise ValueError("signal_id must be UUIDv7")
        if len(self.horizons) != len(set(self.horizons)):
            raise ValueError("horizons must be unique")
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must be unique")
        if any(value.version != 7 for value in self.source_event_ids):
            raise ValueError("source_event_ids must contain UUIDv7 values")
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("targets must be unique")
        if self.family in {EntrySignalFamily.CORE_ENTRY, EntrySignalFamily.CORE_RECOVERY}:
            if self.maturity is None:
                raise ValueError("core entry signals require maturity")
        elif self.maturity is not None:
            raise ValueError("only core entry signal families use L1-L4 maturity")
        zone_values = (self.zone_low, self.zone_high, self.invalidation)
        if any(value is not None for value in zone_values):
            if any(value is None for value in zone_values):
                raise ValueError("zone_low, zone_high and invalidation must be provided together")
            invalidation = self.invalidation
            zone_low = self.zone_low
            zone_high = self.zone_high
            if invalidation is None or zone_low is None or zone_high is None:
                raise AssertionError("complete zone values were checked above")
            if not invalidation < zone_low <= zone_high:
                raise ValueError("signal levels must satisfy invalidation < zone_low <= zone_high")
        return self
