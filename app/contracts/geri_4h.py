"""Stable messages emitted by the independent horizontal-level 4HGERI engine."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    PositiveDecimal,
    SemVer,
    Sha256,
    StrictFrozenModel,
    new_uuid7,
)
from .enums import GeriLevelKind, GeriMaturity
from .rules import NamedValue


class GeriStructuralLevel(StrictFrozenModel):
    """One alternating horizontal level, confirmed by breaking its predecessor."""

    sequence: int = Field(ge=1)
    kind: GeriLevelKind
    price: PositiveDecimal
    source_at: datetime
    confirmed_at: datetime
    broken_at: datetime | None = None

    @model_validator(mode="after")
    def validate_level(self) -> GeriStructuralLevel:
        if self.confirmed_at < self.source_at:
            raise ValueError("level confirmation cannot precede its source extreme")
        if self.broken_at is not None and self.broken_at < self.confirmed_at:
            raise ValueError("level break cannot precede confirmation")
        return self


class GeriAssessment(StrictFrozenModel):
    """Current causal 4HGERI structure and independent entry maturity."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    assessed_at: datetime | None = None
    engine_version: SemVer
    maturity: GeriMaturity
    current_price: PositiveDecimal
    levels: tuple[GeriStructuralLevel, ...] = Field(min_length=1)
    active_level_sequence: int = Field(ge=1)
    active_level_kind: GeriLevelKind
    active_level_price: PositiveDecimal
    atr14: PositiveDecimal
    breakout_buffer: PositiveDecimal
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    bounce_confirmed: bool = False
    daily_swing_aligned: bool = False
    existing_maturity_aligned: bool = False
    current_swing_zone_low: PositiveDecimal | None = None
    current_swing_zone_high: PositiveDecimal | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> GeriAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        sequences = tuple(level.sequence for level in self.levels)
        if sequences != tuple(range(1, len(self.levels) + 1)):
            raise ValueError("4HGERI level sequences must be contiguous")
        for previous, current in zip(self.levels, self.levels[1:], strict=False):
            if previous.kind is current.kind:
                raise ValueError("4HGERI levels must alternate support and resistance")
            if previous.broken_at is None:
                raise ValueError("only the active 4HGERI level may remain unbroken")
        active = self.levels[-1]
        if active.broken_at is not None:
            raise ValueError("active 4HGERI level cannot already be broken")
        if (
            self.active_level_sequence != active.sequence
            or self.active_level_kind is not active.kind
            or self.active_level_price != active.price
        ):
            raise ValueError("active 4HGERI fields must match the latest level")
        zone = (self.zone_low, self.zone_high, self.invalidation)
        if active.kind is GeriLevelKind.RESISTANCE:
            if any(value is not None for value in zone):
                raise ValueError("active resistance cannot expose a long entry zone")
            if self.maturity is not GeriMaturity.BUILDING:
                raise ValueError("active resistance must remain BUILDING")
        else:
            if any(value is None for value in zone):
                raise ValueError("active support requires a complete long entry zone")
            assert self.zone_low is not None
            assert self.zone_high is not None
            assert self.invalidation is not None
            if not self.invalidation < self.zone_low <= active.price <= self.zone_high:
                raise ValueError("4HGERI support zone levels are out of order")
        if self.maturity in {
            GeriMaturity.L2_4H,
            GeriMaturity.L3,
            GeriMaturity.L4,
        } and not self.bounce_confirmed:
            raise ValueError("4HGERI L2 or later requires a confirmed bounce")
        if self.maturity in {GeriMaturity.L3, GeriMaturity.L4} and not (
            self.daily_swing_aligned
        ):
            raise ValueError("4HGERI L3 or L4 requires daily Swing alignment")
        if self.maturity is GeriMaturity.L4 and not self.existing_maturity_aligned:
            raise ValueError("4HGERI L4 requires existing L3/L4 alignment")
        if (self.current_swing_zone_low is None) != (self.current_swing_zone_high is None):
            raise ValueError("current Swing zone must be complete")
        return self


class GeriTransition(StrictFrozenModel):
    """Material maturity or active structural-level change."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    assessment_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_maturity: GeriMaturity | None = None
    maturity: GeriMaturity
    active_level_sequence: int = Field(ge=1)
    active_level_kind: GeriLevelKind
    active_level_price: PositiveDecimal
    current_price: PositiveDecimal
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    invalidation: PositiveDecimal | None = None
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> GeriTransition:
        if self.transition_id.version != 7 or self.assessment_id.version != 7:
            raise ValueError("transition_id and assessment_id must be UUIDv7")
        if self.active_level_kind is GeriLevelKind.SUPPORT:
            if self.zone_low is None or self.zone_high is None or self.invalidation is None:
                raise ValueError("support transition requires long entry levels")
        elif any(
            value is not None
            for value in (self.zone_low, self.zone_high, self.invalidation)
        ):
            raise ValueError("resistance transition cannot expose long entry levels")
        return self
