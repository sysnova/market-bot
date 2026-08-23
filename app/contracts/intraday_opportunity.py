"""Stable paper lifecycle snapshots for intraday round trips."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, PositiveDecimal, StrictFrozenModel, new_uuid7


class IntradaySide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class IntradayOpportunityStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class IntradayCloseReason(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_EXIT = "TIME_EXIT"
    END_OF_DAY = "END_OF_DAY"
    FLOW_REVERSAL = "FLOW_REVERSAL"
    MANUAL = "MANUAL"


class IntradayFillRole(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class IntradayTradeAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class IntradayOpportunityEventKind(StrEnum):
    OPENED = "OPENED"
    MARKED = "MARKED"
    CLOSED = "CLOSED"


class IntradayFill(StrictFrozenModel):
    """One immutable simulated fill priced from an executable side of the quote."""

    fill_id: UUID = Field(default_factory=new_uuid7)
    opportunity_id: UUID
    source_event_id: UUID
    occurred_at: datetime
    role: IntradayFillRole
    action: IntradayTradeAction
    quantity: PositiveDecimal
    price: PositiveDecimal
    fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    @model_validator(mode="after")
    def validate_fill(self) -> IntradayFill:
        for name, value in (
            ("fill_id", self.fill_id),
            ("opportunity_id", self.opportunity_id),
            ("source_event_id", self.source_event_id),
        ):
            if value.version != 7:
                raise ValueError(f"{name} must be UUIDv7")
        return self


class IntradayOpportunity(StrictFrozenModel):
    """Materialized state for exactly one intraday paper round trip."""

    opportunity_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    strategy_id: Identifier
    session_date: date
    side: IntradaySide
    status: IntradayOpportunityStatus
    opened_at: datetime
    updated_at: datetime
    expires_at: datetime
    closed_at: datetime | None = None
    close_reason: IntradayCloseReason | None = None
    quantity: PositiveDecimal
    entry_price: PositiveDecimal
    current_price: PositiveDecimal
    exit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal
    target_price: PositiveDecimal
    highest_mark: PositiveDecimal
    lowest_mark: PositiveDecimal
    gross_pnl: Decimal
    net_pnl: Decimal
    gross_pnl_percent: Decimal
    net_pnl_percent: Decimal
    mfe_percent: Decimal = Field(ge=Decimal("0"))
    mae_percent: Decimal = Field(le=Decimal("0"))
    fees_total: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    revision: int = Field(default=1, ge=1)
    source_signal_id: UUID
    entry_fill: IntradayFill
    exit_fill: IntradayFill | None = None

    @model_validator(mode="after")
    def validate_opportunity(self) -> IntradayOpportunity:
        if self.opportunity_id.version != 7:
            raise ValueError("opportunity_id must be UUIDv7")
        if self.source_signal_id.version != 7:
            raise ValueError("source_signal_id must be UUIDv7")
        if self.updated_at < self.opened_at or self.expires_at <= self.opened_at:
            raise ValueError("opportunity timestamps are out of order")
        if self.lowest_mark > self.highest_mark:
            raise ValueError("opportunity price extrema are inverted")
        if self.side is IntradaySide.LONG:
            if not self.stop_price < self.entry_price < self.target_price:
                raise ValueError("LONG levels must satisfy stop < entry < target")
            expected_entry_action = IntradayTradeAction.BUY
            expected_exit_action = IntradayTradeAction.SELL
        else:
            if not self.target_price < self.entry_price < self.stop_price:
                raise ValueError("SHORT levels must satisfy target < entry < stop")
            expected_entry_action = IntradayTradeAction.SELL
            expected_exit_action = IntradayTradeAction.BUY
        if (
            self.entry_fill.opportunity_id != self.opportunity_id
            or self.entry_fill.role is not IntradayFillRole.ENTRY
            or self.entry_fill.action is not expected_entry_action
            or self.entry_fill.quantity != self.quantity
            or self.entry_fill.price != self.entry_price
            or self.entry_fill.source_event_id != self.source_signal_id
        ):
            raise ValueError("entry fill does not match the opportunity")
        closed = self.status is IntradayOpportunityStatus.CLOSED
        exit_evidence = (
            self.closed_at is not None
            and self.close_reason is not None
            and self.exit_price is not None
            and self.exit_fill is not None
        )
        if closed != exit_evidence:
            raise ValueError("closed opportunity requires exit evidence")
        if self.exit_fill is not None and (
            self.exit_fill.opportunity_id != self.opportunity_id
            or self.exit_fill.role is not IntradayFillRole.EXIT
            or self.exit_fill.action is not expected_exit_action
            or self.exit_fill.quantity != self.quantity
            or self.exit_fill.price != self.exit_price
            or self.exit_fill.occurred_at != self.closed_at
        ):
            raise ValueError("exit fill does not match the opportunity")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("opportunity cannot close before it opens")
        return self


class IntradayOpportunityEvent(StrictFrozenModel):
    """Append-only evidence for a material paper lifecycle transition."""

    event_id: UUID = Field(default_factory=new_uuid7)
    source_event_id: UUID
    kind: IntradayOpportunityEventKind
    occurred_at: datetime
    opportunity: IntradayOpportunity
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    fill: IntradayFill | None = None

    @model_validator(mode="after")
    def validate_event(self) -> IntradayOpportunityEvent:
        if self.event_id.version != 7 or self.source_event_id.version != 7:
            raise ValueError("event identifiers must be UUIDv7")
        if self.occurred_at < self.opportunity.opened_at:
            raise ValueError("event cannot precede the opportunity")
        if self.fill is not None and self.fill.opportunity_id != self.opportunity.opportunity_id:
            raise ValueError("event fill must belong to the opportunity")
        if self.fill is not None and self.fill.source_event_id != self.source_event_id:
            raise ValueError("event fill must share the source event")
        if self.kind is IntradayOpportunityEventKind.OPENED and self.fill != (
            self.opportunity.entry_fill
        ):
            raise ValueError("opened event requires its entry fill")
        if self.kind is IntradayOpportunityEventKind.CLOSED and self.fill != (
            self.opportunity.exit_fill
        ):
            raise ValueError("closed event requires its exit fill")
        if self.kind is IntradayOpportunityEventKind.MARKED and self.fill is not None:
            raise ValueError("marked event cannot contain a fill")
        if self.kind is IntradayOpportunityEventKind.CLOSED and (
            self.opportunity.status is not IntradayOpportunityStatus.CLOSED
        ):
            raise ValueError("closed event requires a closed opportunity")
        if self.kind is not IntradayOpportunityEventKind.CLOSED and (
            self.opportunity.status is IntradayOpportunityStatus.CLOSED
        ):
            raise ValueError("only a closed event may carry a closed opportunity")
        return self
