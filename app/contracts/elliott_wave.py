"""Stable shadow-analysis message produced by the Elliott Wave engine."""

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
from .enums import BarTimeframe, WavePhase
from .rules import NamedValue


class WaveAssessment(StrictFrozenModel):
    """One append-only Elliott hypothesis for a held symbol."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    primary_timeframe: BarTimeframe
    phase: WavePhase
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confidence: UnitInterval
    current_price: PositiveDecimal
    wave1_origin: PositiveDecimal | None = None
    wave1_peak: PositiveDecimal | None = None
    wave2_low: PositiveDecimal | None = None
    wave3_peak: PositiveDecimal | None = None
    corrective_low: PositiveDecimal | None = None
    retracement: UnitInterval | None = None
    entry_zone_low: PositiveDecimal | None = None
    entry_zone_high: PositiveDecimal | None = None
    trigger_price: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    target_low: PositiveDecimal | None = None
    target_high: PositiveDecimal | None = None
    alternative_phase: WavePhase | None = None
    alternative_score: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    violations: tuple[NonEmptyStr, ...] = ()
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> WaveAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        actionable = {
            WavePhase.WAVE_2_ENDING,
            WavePhase.WAVE_3_ACTIVE,
            WavePhase.WAVE_4_ENDING,
            WavePhase.WAVE_5_ACTIVE,
        }
        levels = (
            self.wave1_origin,
            self.wave1_peak,
            self.corrective_low,
            self.entry_zone_low,
            self.entry_zone_high,
            self.trigger_price,
            self.invalidation,
            self.target_low,
            self.target_high,
        )
        if self.phase in actionable and any(level is None for level in levels):
            raise ValueError("actionable wave phases require complete levels")
        if (
            self.entry_zone_low is not None
            and self.entry_zone_high is not None
            and self.entry_zone_low > self.entry_zone_high
        ):
            raise ValueError("entry wave levels are out of order")
        if (
            self.target_low is not None
            and self.target_high is not None
            and self.target_low > self.target_high
        ):
            raise ValueError("target wave levels are out of order")
        metric_names = tuple(item.name for item in self.metrics)
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("wave metrics must be unique by name")
        return self
