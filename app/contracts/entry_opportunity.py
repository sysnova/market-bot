"""Stable snapshots for the Entry Watcher paper-opportunity lifecycle."""

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
    StrictFrozenModel,
    new_uuid7,
)
from .enums import (
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunityStatus,
    EntrySignalFamily,
    SwingTradeMaturity,
)
from .market_analysis import AnalysisResult


class EntryMaturityCheckpoint(StrictFrozenModel):
    """One simulated entry at a maturity level within the same opportunity."""

    checkpoint_id: UUID = Field(default_factory=new_uuid7)
    level: EntryMaturityLevel
    swing_trade_maturity: SwingTradeMaturity | None = None
    signal_family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY
    setup_id: NonEmptyStr | None = None
    reached_at: datetime
    entry_price: PositiveDecimal
    current_price: PositiveDecimal
    highest_price: PositiveDecimal
    lowest_price: PositiveDecimal
    invalidation: PositiveDecimal
    target: PositiveDecimal | None = None
    zone_low: PositiveDecimal | None = None
    zone_high: PositiveDecimal | None = None
    retested_at: datetime | None = None
    retest_low: PositiveDecimal | None = None
    status: EntryCheckpointStatus = EntryCheckpointStatus.OPEN
    closed_at: datetime | None = None
    exit_price: PositiveDecimal | None = None
    outcome: EntryLegStatus | None = None
    gain_loss_percent: Decimal | None = None
    mfe_percent: Decimal = Decimal("0")
    mae_percent: Decimal = Decimal("0")
    return_15m: Decimal | None = None
    return_30m: Decimal | None = None
    return_60m: Decimal | None = None
    return_close: Decimal | None = None

    @model_validator(mode="after")
    def validate_checkpoint(self) -> EntryMaturityCheckpoint:
        if self.checkpoint_id.version != 7:
            raise ValueError("checkpoint_id must be UUIDv7")
        closed = self.status is EntryCheckpointStatus.CLOSED
        if closed != (self.closed_at is not None and self.exit_price is not None):
            raise ValueError("closed checkpoint requires closed_at and exit_price")
        if closed != (self.outcome is not None and self.gain_loss_percent is not None):
            raise ValueError("closed checkpoint requires outcome and gain/loss")
        if self.lowest_price > self.highest_price:
            raise ValueError("checkpoint price extrema are inverted")
        if (self.zone_low is None) != (self.zone_high is None):
            raise ValueError("checkpoint zone requires both low and high")
        if (
            self.zone_low is not None
            and self.zone_high is not None
            and not self.invalidation < self.zone_low <= self.zone_high
        ):
            raise ValueError("checkpoint zone must sit above invalidation")
        if (self.retested_at is None) != (self.retest_low is None):
            raise ValueError("checkpoint retest requires timestamp and low")
        return self


class EntryHorizonLeg(StrictFrozenModel):
    """One paper-trade leg whose horizon may close independently."""

    leg_id: UUID = Field(default_factory=new_uuid7)
    horizon: AnalysisHorizon
    status: EntryLegStatus
    opened_at: datetime | None = None
    entry_price: PositiveDecimal | None = None
    current_price: PositiveDecimal
    invalidation: PositiveDecimal
    target: PositiveDecimal | None = None
    highest_price: PositiveDecimal
    lowest_price: PositiveDecimal
    closed_at: datetime | None = None
    exit_price: PositiveDecimal | None = None
    gain_loss_percent: Decimal | None = None
    mfe_percent: Decimal = Decimal("0")
    mae_percent: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_leg(self) -> EntryHorizonLeg:
        if self.leg_id.version != 7:
            raise ValueError("leg_id must be UUIDv7")
        opened = self.status is not EntryLegStatus.WATCHING
        if opened and (self.opened_at is None or self.entry_price is None):
            raise ValueError("non-watching horizon leg requires an entry")
        terminal = self.status not in {EntryLegStatus.WATCHING, EntryLegStatus.OPEN}
        if terminal != (
            self.closed_at is not None
            and self.exit_price is not None
            and self.gain_loss_percent is not None
        ):
            raise ValueError("terminal horizon leg requires exit evidence")
        if self.lowest_price > self.highest_price:
            raise ValueError("horizon price extrema are inverted")
        return self


class EntryOpportunitySourceCursor(StrictFrozenModel):
    """Last causally applied event for one independent opportunity input stream."""

    source: Identifier
    event_id: UUID
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_cursor(self) -> EntryOpportunitySourceCursor:
        if self.event_id.version != 7:
            raise ValueError("source cursor event_id must be UUIDv7")
        return self


class EntryOpportunitySignalReference(StrictFrozenModel):
    """Bounded source-agnostic provenance for one setup tracked by an opportunity."""

    signal_id: UUID
    family: EntrySignalFamily
    maturity: EntryMaturityLevel | None = None
    current_st: SwingTradeMaturity | None = None
    peak_st: SwingTradeMaturity | None = None
    setup_id: NonEmptyStr
    created_at: datetime
    entry_price: PositiveDecimal
    horizons: tuple[AnalysisHorizon, ...] = Field(min_length=1)
    policy_id: Identifier
    policy_version: SemVer

    @model_validator(mode="after")
    def validate_reference(self) -> EntryOpportunitySignalReference:
        if self.signal_id.version != 7:
            raise ValueError("signal reference signal_id must be UUIDv7")
        if len(self.horizons) != len(set(self.horizons)):
            raise ValueError("signal reference horizons must be unique")
        core = self.family in {
            EntrySignalFamily.CORE_ENTRY,
            EntrySignalFamily.CORE_RECOVERY,
        }
        if core != (self.maturity is not None):
            raise ValueError("only core signal references use L1-L4 maturity")
        if self.family is EntrySignalFamily.SWING_TRADE:
            if self.peak_st is None and self.current_st is not None:
                raise ValueError("SwingTrade current ST requires peak ST")
        elif self.current_st is not None or self.peak_st is not None:
            raise ValueError("only SwingTrade references use ST maturity")
        return self


class EntryOpportunity(StrictFrozenModel):
    """Current materialized state of one ticker's original entry thesis."""

    opportunity_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    status: EntryOpportunityStatus
    current_maturity: EntryMaturityLevel
    peak_maturity: EntryMaturityLevel
    progress_percent: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    original_watch_id: UUID | None = None
    armed_at: datetime
    updated_at: datetime
    last_market_bar_at: datetime | None = None
    expires_at: datetime
    closed_at: datetime | None = None
    close_reason: EntryCloseReason | None = None
    zone_low: PositiveDecimal
    zone_high: PositiveDecimal
    invalidation: PositiveDecimal
    original_price: PositiveDecimal
    current_price: PositiveDecimal
    revision: int = Field(default=1, ge=1)
    source_analysis_ids: tuple[UUID, ...] = Field(min_length=1)
    primary_signal_family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY
    signal_references: tuple[EntryOpportunitySignalReference, ...] = Field(
        default=(), max_length=32
    )
    source_cursors: tuple[EntryOpportunitySourceCursor, ...] = Field(
        default=(), max_length=16
    )
    latest_analyses: tuple[AnalysisResult, ...] = ()
    legs: tuple[EntryHorizonLeg, ...] = ()
    checkpoints: tuple[EntryMaturityCheckpoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_opportunity(self) -> EntryOpportunity:
        if self.opportunity_id.version != 7:
            raise ValueError("opportunity_id must be UUIDv7")
        if self.original_watch_id is not None and self.original_watch_id.version != 7:
            raise ValueError("original_watch_id must be UUIDv7")
        if any(value.version != 7 for value in self.source_analysis_ids):
            raise ValueError("source_analysis_ids must contain UUIDv7 values")
        if len({item.source for item in self.source_cursors}) != len(self.source_cursors):
            raise ValueError("opportunity source cursors must be unique")
        if len({item.signal_id for item in self.signal_references}) != len(
            self.signal_references
        ):
            raise ValueError("opportunity signal references must have unique signal IDs")
        setups = {(item.family, item.setup_id) for item in self.signal_references}
        if len(setups) != len(self.signal_references):
            raise ValueError("opportunity signal references must have unique setups")
        if self.invalidation >= self.zone_low or self.zone_low > self.zone_high:
            raise ValueError("opportunity levels must satisfy invalidation < low <= high")
        closed = self.status is EntryOpportunityStatus.CLOSED
        if closed != (self.closed_at is not None and self.close_reason is not None):
            raise ValueError("closed opportunity requires closed_at and close_reason")
        checkpoint_keys = {
            (item.level, item.swing_trade_maturity, item.signal_family, item.setup_id)
            for item in self.checkpoints
        }
        if len(checkpoint_keys) != len(self.checkpoints):
            raise ValueError("maturity checkpoints must be unique by level and setup")
        return self


class EntryOpportunityEvent(StrictFrozenModel):
    """Immutable evidence emitted when an opportunity materially changes."""

    event_id: UUID = Field(default_factory=new_uuid7)
    occurred_at: datetime
    opportunity: EntryOpportunity
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event(self) -> EntryOpportunityEvent:
        if self.event_id.version != 7:
            raise ValueError("event_id must be UUIDv7")
        if self.occurred_at < self.opportunity.armed_at:
            raise ValueError("event cannot precede the opportunity")
        return self
