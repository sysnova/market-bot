from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.contracts.order_flow import (
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowTransition,
    OrderFlowWindow,
    TradeAggressor,
    market_quote_subject,
    market_trade_cancel_subject,
    market_trade_correction_subject,
    market_trade_subject,
    order_flow_state_subject,
    order_flow_transition_subject,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _trade(*, trade_id: str = "trade-1", price: str = "100.05") -> MarketTrade:
    return MarketTrade(
        symbol="AAPL",
        occurred_at=NOW,
        received_at=NOW + timedelta(milliseconds=2),
        price=Decimal(price),
        size=Decimal("100"),
        trade_id=trade_id,
        exchange="V",
        tape="C",
        conditions=("@",),
    )


def test_market_trade_and_quote_are_typed_frozen_utc_contracts() -> None:
    trade = _trade()
    quote = MarketQuote(
        symbol="AAPL",
        occurred_at=NOW,
        received_at=NOW + timedelta(milliseconds=1),
        bid_price=Decimal("100.00"),
        ask_price=Decimal("100.10"),
        bid_size=Decimal("500"),
        ask_size=Decimal("400"),
    )

    assert trade.event_id.version == 7
    assert quote.mid_price == Decimal("100.05")
    assert quote.spread == Decimal("0.10")
    with pytest.raises(ValidationError):
        trade.price = Decimal("101")
    with pytest.raises(ValidationError, match="UTC"):
        MarketQuote(
            symbol="AAPL",
            occurred_at=datetime(2026, 8, 24, 14, 30),
            received_at=NOW,
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
        )


def test_quote_rejects_crossed_market_and_receive_time_before_exchange_time() -> None:
    with pytest.raises(ValidationError, match="bid_price"):
        MarketQuote(
            symbol="AAPL",
            occurred_at=NOW,
            received_at=NOW,
            bid_price=Decimal("101"),
            ask_price=Decimal("100"),
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
        )
    with pytest.raises(ValidationError, match="received_at"):
        MarketQuote(
            symbol="AAPL",
            occurred_at=NOW,
            received_at=NOW - timedelta(microseconds=1),
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
        )


def test_trade_correction_and_cancel_reference_original_print() -> None:
    corrected = _trade(trade_id="trade-2", price="100.07")
    correction = MarketTradeCorrection(
        symbol="AAPL",
        occurred_at=NOW + timedelta(seconds=1),
        original_trade_id="trade-1",
        corrected_trade=corrected,
    )
    cancel = MarketTradeCancel(
        symbol="AAPL",
        occurred_at=NOW + timedelta(seconds=2),
        trade_id="trade-2",
    )

    assert correction.event_id.version == 7
    assert correction.corrected_trade.price == Decimal("100.07")
    assert cancel.trade_id == "trade-2"

    with pytest.raises(ValidationError, match="symbol"):
        MarketTradeCorrection(
            symbol="MSFT",
            occurred_at=NOW + timedelta(seconds=1),
            original_trade_id="trade-1",
            corrected_trade=corrected,
        )


def test_order_flow_state_requires_the_canonical_five_windows() -> None:
    windows = tuple(
        OrderFlowWindow(
            window_seconds=seconds,
            trade_count=2,
            buy_volume=Decimal("100"),
            sell_volume=Decimal("50"),
            neutral_volume=Decimal("0"),
            unknown_volume=Decimal("0"),
            delta=Decimal("50"),
            volume_velocity=Decimal("10"),
            large_buy_volume=Decimal("100"),
            large_sell_volume=Decimal("0"),
            price_change_bps=Decimal("5"),
        )
        for seconds in (1, 5, 15, 60, 300)
    )
    state = OrderFlowState(
        symbol="AAPL",
        occurred_at=NOW,
        engine_version="1.0.0",
        state=OrderFlowStateKind.BUY_PRESSURE,
        current_price=Decimal("100.05"),
        mid_price=Decimal("100.05"),
        bid_price=Decimal("100.00"),
        ask_price=Decimal("100.10"),
        spread_bps=Decimal("9.9950"),
        cumulative_delta=Decimal("250"),
        confidence=Decimal("0.8"),
        data_quality=Decimal("0.9"),
        quote_age_ms=Decimal("10"),
        quote_fresh=True,
        unknown_trade_ratio=Decimal("0"),
        windows=windows,
        reasons=("buy_pressure",),
        context_hash=HASH,
    )

    assert state.state_id.version == 7
    assert state.windows[-1].window_seconds == 300
    assert state.bid_price == Decimal("100.00")
    assert state.ask_price == Decimal("100.10")
    with pytest.raises(ValidationError, match="canonical"):
        OrderFlowState(
            **{**state.model_dump(), "windows": windows[:-1]},
        )


def test_order_flow_quote_evidence_is_optional_but_atomic() -> None:
    state = OrderFlowState.model_validate(_order_flow_state_payload())

    assert state.bid_price is None
    with pytest.raises(ValidationError, match="quote evidence"):
        OrderFlowState.model_validate(
            {
                **_order_flow_state_payload(),
                "bid_price": Decimal("100"),
            }
        )


def test_window_rejects_inconsistent_delta() -> None:
    with pytest.raises(ValidationError, match="delta"):
        OrderFlowWindow(
            window_seconds=5,
            trade_count=1,
            buy_volume=Decimal("10"),
            sell_volume=Decimal("2"),
            neutral_volume=Decimal("0"),
            unknown_volume=Decimal("0"),
            delta=Decimal("7"),
            volume_velocity=Decimal("2"),
            large_buy_volume=Decimal("0"),
            large_sell_volume=Decimal("0"),
            price_change_bps=Decimal("0"),
        )


def test_transition_must_change_state() -> None:
    with pytest.raises(ValidationError, match="change state"):
        OrderFlowTransition(
            state_id=_state_id(),
            symbol="AAPL",
            occurred_at=NOW,
            engine_version="1.0.0",
            previous_state=OrderFlowStateKind.NEUTRAL,
            state=OrderFlowStateKind.NEUTRAL,
            confidence=Decimal("0.4"),
            current_price=Decimal("100"),
            reasons=("unchanged",),
            context_hash=HASH,
        )


def _state_id() -> UUID:
    state = _trade().event_id
    return state


def _order_flow_state_payload() -> dict[str, object]:
    windows = tuple(
        OrderFlowWindow(
            window_seconds=seconds,
            trade_count=1,
            buy_volume=Decimal("100"),
            sell_volume=Decimal("0"),
            neutral_volume=Decimal("0"),
            unknown_volume=Decimal("0"),
            delta=Decimal("100"),
            volume_velocity=Decimal("1"),
            large_buy_volume=Decimal("0"),
            large_sell_volume=Decimal("0"),
            price_change_bps=Decimal("1"),
        )
        for seconds in (1, 5, 15, 60, 300)
    )
    return {
        "symbol": "AAPL",
        "occurred_at": NOW,
        "engine_version": "1.0.0",
        "state": OrderFlowStateKind.BUY_PRESSURE,
        "current_price": Decimal("100"),
        "mid_price": Decimal("100"),
        "confidence": Decimal("0.8"),
        "data_quality": Decimal("0.9"),
        "quote_age_ms": Decimal("10"),
        "quote_fresh": True,
        "unknown_trade_ratio": Decimal("0"),
        "windows": windows,
        "reasons": ("buy_pressure",),
        "context_hash": HASH,
    }


def test_subjects_keep_hot_market_data_separate_from_durable_analytics() -> None:
    assert market_trade_subject("BRK.B") == "marketbot.market.data.trade.BRK_B"
    assert market_quote_subject("BRK.B") == "marketbot.market.data.quote.BRK_B"
    assert market_trade_correction_subject("BRK.B") == (
        "marketbot.market.data.trade-correction.BRK_B"
    )
    assert market_trade_cancel_subject("BRK.B") == "marketbot.market.data.trade-cancel.BRK_B"
    assert order_flow_state_subject("BRK.B") == "marketbot.v1.order-flow.state.BRK_B"
    assert order_flow_transition_subject(OrderFlowStateKind.BUY_PRESSURE, "BRK.B") == (
        "marketbot.v1.order-flow.transition.BUY_PRESSURE.BRK_B"
    )


def test_trade_aggressor_values_are_stable() -> None:
    assert tuple(TradeAggressor) == (
        TradeAggressor.BUY,
        TradeAggressor.SELL,
        TradeAggressor.NEUTRAL,
        TradeAggressor.UNKNOWN,
    )
