from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts.order_flow import (
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
    OrderFlowStateKind,
    TradeAggressor,
)
from app.order_flow_engine import (
    OrderFlowEngine,
    OrderFlowEngineV11,
    OrderFlowEngineV12,
    OrderFlowPolicy,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _quote(
    *,
    at: datetime = NOW,
    bid: str = "99.90",
    ask: str = "100.10",
) -> MarketQuote:
    return MarketQuote(
        symbol="AAPL",
        occurred_at=at,
        received_at=at,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("500"),
        ask_size=Decimal("400"),
    )


def _trade(
    trade_id: str,
    *,
    at: datetime = NOW,
    price: str = "100.10",
    size: str = "100",
) -> MarketTrade:
    return MarketTrade(
        symbol="AAPL",
        occurred_at=at,
        received_at=at,
        price=Decimal(price),
        size=Decimal(size),
        trade_id=trade_id,
    )


def test_classifies_quote_midpoint_and_tick_rule_without_float_math() -> None:
    engine = OrderFlowEngine()
    engine.ingest_quote(_quote())

    assert engine.ingest_trade(_trade("ask", price="100.10")).aggressor is TradeAggressor.BUY
    assert engine.ingest_trade(_trade("bid", price="99.90")).aggressor is TradeAggressor.SELL
    assert engine.ingest_trade(_trade("above-mid", price="100.06")).aggressor is TradeAggressor.BUY
    assert engine.ingest_trade(_trade("mid", price="100.00")).aggressor is TradeAggressor.NEUTRAL

    stale_at = NOW + timedelta(seconds=3)
    update = engine.ingest_trade(_trade("tick", at=stale_at, price="100.01"))
    assert update.aggressor is TradeAggressor.BUY
    assert update.state.quote_fresh is False
    assert isinstance(update.state.cumulative_delta, Decimal)


def test_unknown_without_quote_or_previous_tick_reduces_data_quality() -> None:
    update = OrderFlowEngine().ingest_trade(_trade("first"))

    assert update.aggressor is TradeAggressor.UNKNOWN
    assert update.state.unknown_trade_ratio == Decimal("1")
    assert update.state.data_quality < Decimal("0.5")


def test_v11_lifts_executable_quote_evidence_into_the_durable_assessment() -> None:
    engine = OrderFlowEngineV11(
        OrderFlowPolicy(
            tracked_symbols=("AAPL",),
            minimum_trades=1,
            minimum_volume=Decimal("1"),
        )
    )
    engine.ingest_quote(_quote(bid="99.90", ask="100.10"))

    state = engine.ingest_trade(_trade("quoted")).state

    assert state.engine_version == "1.1.0"
    assert state.bid_price == Decimal("99.90")
    assert state.ask_price == Decimal("100.10")
    assert state.spread_bps == Decimal("20.0000")


def test_v11_does_not_publish_a_future_quote_as_executable_evidence() -> None:
    engine = OrderFlowEngineV11(
        OrderFlowPolicy(
            tracked_symbols=("AAPL",),
            minimum_trades=1,
            minimum_volume=Decimal("1"),
        )
    )
    engine.ingest_quote(_quote(at=NOW + timedelta(milliseconds=10)))

    state = engine.ingest_trade(_trade("before-quote", at=NOW)).state

    assert state.quote_fresh is False
    assert state.quote_age_ms is None
    assert state.bid_price is None
    assert state.ask_price is None
    assert "quote_timestamp_ahead_of_trade" in state.reasons


def test_builds_canonical_rolling_windows_and_expires_old_trades() -> None:
    engine = OrderFlowEngine(OrderFlowPolicy(minimum_trades=1, minimum_volume=Decimal("1")))
    engine.ingest_quote(_quote())
    engine.ingest_trade(_trade("old", size="10"))
    update = engine.ingest_trade(
        _trade("new", at=NOW + timedelta(seconds=6), price="100.20", size="20")
    )

    by_seconds = {window.window_seconds: window for window in update.state.windows}
    assert tuple(by_seconds) == (1, 5, 15, 60, 300)
    assert by_seconds[1].trade_count == 1
    assert by_seconds[5].trade_count == 1
    assert by_seconds[15].trade_count == 2
    assert by_seconds[300].volume_velocity == Decimal("0.1")


def test_buy_pressure_emits_a_transition_only_when_state_changes() -> None:
    engine = OrderFlowEngine(OrderFlowPolicy(minimum_trades=2, minimum_volume=Decimal("100")))
    engine.ingest_quote(_quote())
    first = engine.ingest_trade(_trade("1", size="60"))
    second = engine.ingest_trade(_trade("2", at=NOW + timedelta(milliseconds=100), size="60"))
    third = engine.ingest_trade(_trade("3", at=NOW + timedelta(milliseconds=200), size="60"))

    assert first.state.state is OrderFlowStateKind.NEUTRAL
    assert first.transition is None
    assert second.state.state is OrderFlowStateKind.BUY_PRESSURE
    assert second.transition is not None
    assert second.transition.previous_state is OrderFlowStateKind.NEUTRAL
    assert third.transition is None


def test_v12_requires_persistent_pulse_before_promoting_a_stable_state() -> None:
    engine = OrderFlowEngineV12(
        OrderFlowPolicy(
            tracked_symbols=("AAPL",),
            minimum_trades=1,
            minimum_volume=Decimal("1"),
            transition_confirmation_samples=3,
            transition_confirmation_seconds=Decimal("2"),
        )
    )
    engine.ingest_quote(_quote())

    first = engine.ingest_trade(_trade("1", size="100"))
    second = engine.ingest_trade(
        _trade("2", at=NOW + timedelta(seconds=1), price="100.20", size="100")
    )
    third = engine.ingest_trade(
        _trade("3", at=NOW + timedelta(seconds=2), price="100.30", size="100")
    )

    assert first.state.pulse_state is OrderFlowStateKind.BUY_PRESSURE
    assert first.state.state is OrderFlowStateKind.NEUTRAL
    assert first.state.candidate_state is OrderFlowStateKind.BUY_PRESSURE
    assert first.state.candidate_samples == 1
    assert second.state.state is OrderFlowStateKind.NEUTRAL
    assert second.state.candidate_samples == 2
    assert third.state.state is OrderFlowStateKind.BUY_PRESSURE
    assert third.state.candidate_state is None
    assert third.state.state_stable_since == NOW + timedelta(seconds=2)
    assert third.transition is not None


def test_v12_does_not_reverse_a_stable_bias_on_one_opposite_pulse() -> None:
    engine = OrderFlowEngineV12(
        OrderFlowPolicy(
            tracked_symbols=("AAPL",),
            minimum_trades=1,
            minimum_volume=Decimal("1"),
            transition_confirmation_samples=2,
            transition_confirmation_seconds=Decimal("1"),
            reversal_confirmation_samples=3,
            reversal_confirmation_seconds=Decimal("3"),
        )
    )
    engine.ingest_quote(_quote())
    engine.ingest_trade(_trade("buy-1", size="100"))
    stable = engine.ingest_trade(
        _trade("buy-2", at=NOW + timedelta(seconds=1), price="100.20", size="100")
    )
    engine.ingest_quote(
        _quote(at=NOW + timedelta(seconds=2), bid="99.80", ask="100.00")
    )
    opposite = engine.ingest_trade(
        _trade("sell-1", at=NOW + timedelta(seconds=2), price="99.80", size="1000")
    )

    assert stable.state.state is OrderFlowStateKind.BUY_PRESSURE
    assert opposite.state.pulse_state is OrderFlowStateKind.SELL_PRESSURE
    assert opposite.state.state is OrderFlowStateKind.BUY_PRESSURE
    assert opposite.state.candidate_state is OrderFlowStateKind.SELL_PRESSURE
    assert opposite.state.candidate_samples == 1
    assert opposite.state.confidence < Decimal("0.50")
    assert opposite.transition is None


def test_large_trade_and_correction_are_reflected_without_double_counting() -> None:
    engine = OrderFlowEngine(
        OrderFlowPolicy(
            minimum_trades=1,
            minimum_volume=Decimal("1"),
            large_trade_size=Decimal("1000"),
        )
    )
    engine.ingest_quote(_quote())
    original = _trade("original", size="1200")
    engine.ingest_trade(original)
    corrected = _trade("corrected", price="99.90", size="500")
    update = engine.apply_correction(
        MarketTradeCorrection(
            symbol="AAPL",
            occurred_at=NOW + timedelta(seconds=1),
            original_trade_id="original",
            corrected_trade=corrected,
        )
    )

    assert update is not None
    window = update.state.windows[-1]
    assert window.trade_count == 1
    assert window.buy_volume == Decimal("0")
    assert window.sell_volume == Decimal("500")
    assert window.large_buy_volume == Decimal("0")
    assert update.state.cumulative_delta == Decimal("-500")


def test_cancel_reverses_a_known_trade_and_is_idempotent() -> None:
    engine = OrderFlowEngine(OrderFlowPolicy(minimum_trades=1, minimum_volume=Decimal("1")))
    engine.ingest_quote(_quote())
    engine.ingest_trade(_trade("cancel-me", size="250"))
    cancel = MarketTradeCancel(
        symbol="AAPL",
        occurred_at=NOW + timedelta(seconds=1),
        trade_id="cancel-me",
    )

    update = engine.apply_cancel(cancel)

    assert update is not None
    assert update.state.cumulative_delta == Decimal("0")
    assert update.state.windows[-1].trade_count == 0
    assert engine.apply_cancel(cancel) is None


def test_rejects_out_of_order_trades_to_preserve_causal_results() -> None:
    engine = OrderFlowEngine()
    engine.ingest_trade(_trade("new", at=NOW + timedelta(seconds=1)))

    with pytest.raises(ValueError, match="out of order"):
        engine.ingest_trade(_trade("old", at=NOW))


def test_reset_symbol_starts_a_new_intraday_cvd_session() -> None:
    engine = OrderFlowEngine(OrderFlowPolicy(minimum_trades=1, minimum_volume=Decimal("1")))
    engine.ingest_quote(_quote())
    engine.ingest_trade(_trade("1"))

    engine.reset_symbol("AAPL")

    update = engine.ingest_trade(_trade("2", at=NOW + timedelta(seconds=1)))
    assert update.state.cumulative_delta == Decimal("0")
    assert update.aggressor is TradeAggressor.UNKNOWN
