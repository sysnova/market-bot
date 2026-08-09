"""Source-agnostic setup evidence submitted to the Alert decision boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, PositiveDecimal, SemVer, StrictFrozenModel, new_uuid7
from .enums import AnalysisHorizon, EntrySignalFamily
from .market_analysis import AnalysisResult


class EntrySetupAssessment(StrictFrozenModel):
    """Analytical setup evidence without a buy decision or L1-L4 quality."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    family: EntrySignalFamily
    symbol: Identifier
    assessed_at: datetime
    setup_id: NonEmptyStr
    entry_price: PositiveDecimal
    horizons: tuple[AnalysisHorizon, ...] = Field(min_length=1)
    component_analyses: tuple[AnalysisResult, ...] = Field(min_length=1)
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    targets: tuple[PositiveDecimal, ...] = ()
    policy_id: Identifier
    policy_version: SemVer
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_event_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_assessment(self) -> EntrySetupAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if len(self.horizons) != len(set(self.horizons)):
            raise ValueError("horizons must be unique")
        if tuple(item.horizon for item in self.component_analyses) != self.horizons:
            raise ValueError("component analyses must match assessment horizons")
        if any(item.symbol != self.symbol for item in self.component_analyses):
            raise ValueError("component analyses must belong to the assessment symbol")
        analysis_ids = tuple(item.analysis_id for item in self.component_analyses)
        if len(analysis_ids) != len(set(analysis_ids)):
            raise ValueError("component analyses must be unique")
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must be unique")
        if any(value.version != 7 for value in self.source_event_ids):
            raise ValueError("source_event_ids must contain UUIDv7 values")
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("targets must be unique")
        zone_values = (self.zone_low, self.zone_high, self.invalidation)
        if any(value is not None for value in zone_values):
            if any(value is None for value in zone_values):
                raise ValueError(
                    "zone_low, zone_high and invalidation must be provided together"
                )
            assert self.invalidation is not None
            assert self.zone_low is not None
            assert self.zone_high is not None
            if not self.invalidation < self.zone_low <= self.zone_high:
                raise ValueError(
                    "assessment levels must satisfy invalidation < zone_low <= zone_high"
                )
        return self
