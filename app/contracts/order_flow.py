"""Typed hot-path market data and compact Order Flow analytical messages."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, computed_field, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    NonNegativeDecimal,
    PositiveDecimal,
    SemVer,
    Sha256,
    StrictFrozenModel,
    UnitInterval,
    new_uuid7,
)
from .microstructure_events import (
    MARKET_QUOTE_EVENT,
    MARKET_TRADE_CANCEL_EVENT,
    MARKET_TRADE_CORRECTION_EVENT,
    MARKET_TRADE_EVENT,
    ORDER_FLOW_STATE_EVENT,
    ORDER_FLOW_TRANSITION_EVENT,
    order_flow_state_subject,
    order_flow_transition_subject,
)

__all__ = [
    "MARKET_QUOTE_EVENT",
    "MARKET_TRADE_CANCEL_EVENT",
    "MARKET_TRADE_CORRECTION_EVENT",
    "MARKET_TRADE_EVENT",
    "ORDER_FLOW_STATE_EVENT",
    "ORDER_FLOW_TRANSITION_EVENT",
    "ORDER_FLOW_WINDOWS",
    "MarketQuote",
    "MarketTrade",
    "MarketTradeCancel",
    "MarketTradeCorrection",
    "OrderFlowState",
    "OrderFlowStateKind",
    "OrderFlowTransition",
    "OrderFlowWindow",
    "TradeAggressor",
    "WindowSeconds",
    "market_quote_subject",
    "market_trade_cancel_subject",
    "market_trade_correction_subject",
    "market_trade_subject",
    "order_flow_state_subject",
    "order_flow_transition_subject",
]

WindowSeconds = Literal[1, 5, 15, 60, 300]
ORDER_FLOW_WINDOWS: Final[tuple[WindowSeconds, ...]] = (1, 5, 15, 60, 300)


class TradeAggressor(StrEnum):
    """Best-effort classification of the liquidity taker."""

    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class OrderFlowStateKind(StrEnum):
    """Compact state suitable for downstream analytical consumers."""

    NEUTRAL = "NEUTRAL"
    BUY_PRESSURE = "BUY_PRESSURE"
    SELL_PRESSURE = "SELL_PRESSURE"
    SELLER_EXHAUSTION = "SELLER_EXHAUSTION"
    BUYER_EXHAUSTION = "BUYER_EXHAUSTION"
    BUY_ABSORPTION = "BUY_ABSORPTION"
    SELL_ABSORPTION = "SELL_ABSORPTION"
    BULLISH_DIVERGENCE = "BULLISH_DIVERGENCE"
    BEARISH_DIVERGENCE = "BEARISH_DIVERGENCE"


class MarketTrade(StrictFrozenModel):
    """One normalized SIP trade used by microstructure consumers."""

    event_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    received_at: datetime
    price: PositiveDecimal
    size: PositiveDecimal
    trade_id: NonEmptyStr
    exchange: NonEmptyStr | None = None
    tape: NonEmptyStr | None = None
    conditions: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_trade(self) -> MarketTrade:
        if self.event_id.version != 7:
            raise ValueError("event_id must be UUIDv7")
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede occurred_at")
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("trade conditions must be unique")
        return self


class MarketQuote(StrictFrozenModel):
    """One normalized SIP top-of-book quote."""

    event_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    received_at: datetime
    bid_price: PositiveDecimal
    ask_price: PositiveDecimal
    bid_size: NonNegativeDecimal
    ask_size: NonNegativeDecimal
    bid_exchange: NonEmptyStr | None = None
    ask_exchange: NonEmptyStr | None = None
    tape: NonEmptyStr | None = None
    conditions: tuple[NonEmptyStr, ...] = ()

    @computed_field
    @property
    def mid_price(self) -> Decimal:
        return (self.bid_price + self.ask_price) / Decimal("2")

    @computed_field
    @property
    def spread(self) -> Decimal:
        return self.ask_price - self.bid_price

    @model_validator(mode="after")
    def validate_quote(self) -> MarketQuote:
        if self.event_id.version != 7:
            raise ValueError("event_id must be UUIDv7")
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede occurred_at")
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price cannot exceed ask_price")
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("quote conditions must be unique")
        return self


class MarketTradeCorrection(StrictFrozenModel):
    """Replace one previously observed trade with the corrected print."""

    event_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    original_trade_id: NonEmptyStr
    corrected_trade: MarketTrade

    @model_validator(mode="after")
    def validate_correction(self) -> MarketTradeCorrection:
        if self.event_id.version != 7:
            raise ValueError("event_id must be UUIDv7")
        if self.symbol != self.corrected_trade.symbol:
            raise ValueError("correction symbol must match corrected trade symbol")
        if self.original_trade_id == self.corrected_trade.trade_id:
            raise ValueError("corrected trade must have a new trade_id")
        return self


class MarketTradeCancel(StrictFrozenModel):
    """Cancel one previously observed trade print."""

    event_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    trade_id: NonEmptyStr

    @model_validator(mode="after")
    def validate_cancel(self) -> MarketTradeCancel:
        if self.event_id.version != 7:
            raise ValueError("event_id must be UUIDv7")
        return self


class OrderFlowWindow(StrictFrozenModel):
    """One causal rolling window embedded in an Order Flow state."""

    window_seconds: WindowSeconds
    trade_count: int = Field(ge=0)
    buy_volume: NonNegativeDecimal
    sell_volume: NonNegativeDecimal
    neutral_volume: NonNegativeDecimal
    unknown_volume: NonNegativeDecimal
    delta: Decimal
    volume_velocity: NonNegativeDecimal
    large_buy_volume: NonNegativeDecimal
    large_sell_volume: NonNegativeDecimal
    price_change_bps: Decimal

    @computed_field
    @property
    def total_volume(self) -> Decimal:
        return self.buy_volume + self.sell_volume + self.neutral_volume + self.unknown_volume

    @model_validator(mode="after")
    def validate_window(self) -> OrderFlowWindow:
        if self.delta != self.buy_volume - self.sell_volume:
            raise ValueError("delta must equal buy_volume minus sell_volume")
        if self.large_buy_volume > self.buy_volume:
            raise ValueError("large_buy_volume cannot exceed buy_volume")
        if self.large_sell_volume > self.sell_volume:
            raise ValueError("large_sell_volume cannot exceed sell_volume")
        return self


class OrderFlowState(StrictFrozenModel):
    """Latest compact, analytical-only Order Flow state for one symbol."""

    state_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    state: OrderFlowStateKind
    pulse_state: OrderFlowStateKind | None = None
    candidate_state: OrderFlowStateKind | None = None
    candidate_samples: int = Field(default=0, ge=0)
    state_stable_since: datetime | None = None
    current_price: PositiveDecimal
    mid_price: PositiveDecimal | None = None
    bid_price: PositiveDecimal | None = None
    ask_price: PositiveDecimal | None = None
    spread_bps: NonNegativeDecimal | None = None
    cumulative_delta: Decimal = Decimal("0")
    confidence: UnitInterval
    data_quality: UnitInterval
    quote_age_ms: NonNegativeDecimal | None = None
    quote_fresh: bool
    unknown_trade_ratio: UnitInterval
    windows: tuple[OrderFlowWindow, ...]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_event_ids: tuple[UUID, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_state(self) -> OrderFlowState:
        if self.state_id.version != 7:
            raise ValueError("state_id must be UUIDv7")
        seconds = tuple(window.window_seconds for window in self.windows)
        if seconds != ORDER_FLOW_WINDOWS:
            raise ValueError("windows must use the canonical 1/5/15/60/300 second order")
        if self.quote_fresh and (self.mid_price is None or self.quote_age_ms is None):
            raise ValueError("fresh quote state requires mid_price and quote_age_ms")
        quote_evidence = (self.bid_price, self.ask_price, self.spread_bps)
        if any(value is not None for value in quote_evidence) and any(
            value is None for value in quote_evidence
        ):
            raise ValueError("order-flow quote evidence must be complete")
        if self.bid_price is not None and self.ask_price is not None:
            if self.bid_price > self.ask_price:
                raise ValueError("bid_price cannot exceed ask_price")
            midpoint = (self.bid_price + self.ask_price) / Decimal("2")
            expected_spread = (
                (self.ask_price - self.bid_price) / midpoint * Decimal("10000")
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if self.spread_bps != expected_spread:
                raise ValueError("spread_bps must match bid_price and ask_price")
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must be unique")
        if any(event_id.version != 7 for event_id in self.source_event_ids):
            raise ValueError("source_event_ids must be UUIDv7")
        if self.state_stable_since is not None and self.state_stable_since > self.occurred_at:
            raise ValueError("state_stable_since cannot be later than occurred_at")
        if self.candidate_state is None and self.candidate_samples != 0:
            raise ValueError("candidate_samples requires candidate_state")
        if self.candidate_state is not None and self.candidate_samples < 1:
            raise ValueError("candidate_state requires positive candidate_samples")
        return self


class OrderFlowTransition(StrictFrozenModel):
    """Append-only material state change emitted by Order Flow."""

    transition_id: UUID = Field(default_factory=new_uuid7)
    state_id: UUID
    symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    previous_state: OrderFlowStateKind | None = None
    state: OrderFlowStateKind
    confidence: UnitInterval
    current_price: PositiveDecimal
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_transition(self) -> OrderFlowTransition:
        if self.transition_id.version != 7 or self.state_id.version != 7:
            raise ValueError("transition_id and state_id must be UUIDv7")
        if self.previous_state is self.state:
            raise ValueError("an Order Flow transition must change state")
        return self


def market_trade_subject(symbol: str) -> str:
    return f"marketbot.market.data.trade.{_symbol_token(symbol)}"


def market_quote_subject(symbol: str) -> str:
    return f"marketbot.market.data.quote.{_symbol_token(symbol)}"


def market_trade_correction_subject(symbol: str) -> str:
    return f"marketbot.market.data.trade-correction.{_symbol_token(symbol)}"


def market_trade_cancel_subject(symbol: str) -> str:
    return f"marketbot.market.data.trade-cancel.{_symbol_token(symbol)}"


def _symbol_token(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if (
        not normalized
        or len(normalized) > 16
        or not normalized[0].isalnum()
        or any(not character.isalnum() and character not in ".-" for character in normalized)
    ):
        raise ValueError("symbol is not safe for an event subject")
    # Alpaca Core publishes ephemeral market-data subjects with lowercase,
    # hyphenated symbol tokens. NATS subjects are case-sensitive, so the exact
    # bounded Order Flow subscriptions must use the same wire representation.
    return normalized.lower().replace(".", "-")
