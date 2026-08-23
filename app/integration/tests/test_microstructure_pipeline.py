from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import IntradaySide, MarketQuote, MarketTrade, ScalpState
from app.intraday_opportunity_engine import (
    InMemoryIntradayOpportunityStore,
    IntradayOpportunityEngine,
)
from app.order_flow_engine import OrderFlowEngine, OrderFlowPolicy
from app.scalp_engine import ScalpContext, ScalpEngine

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _quote(at: datetime, bid: str, ask: str) -> MarketQuote:
    return MarketQuote(
        symbol="AAPL",
        occurred_at=at,
        received_at=at,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("500"),
        ask_size=Decimal("500"),
    )


def _trade(index: int, at: datetime, price: str) -> MarketTrade:
    return MarketTrade(
        symbol="AAPL",
        occurred_at=at,
        received_at=at,
        price=Decimal(price),
        size=Decimal("100"),
        trade_id=f"trade-{index}",
    )


@pytest.mark.asyncio
async def test_order_flow_to_scalp_to_paper_opportunity_is_causal_and_independent() -> None:
    flow_engine = OrderFlowEngine(
        OrderFlowPolicy(minimum_trades=3, minimum_volume=Decimal("100"))
    )
    quote = _quote(NOW, "99.99", "100.10")
    flow_engine.ingest_quote(quote)
    update = None
    for index in range(3):
        at = NOW + timedelta(milliseconds=index * 100)
        update = flow_engine.ingest_trade(_trade(index, at, "100.10"))
    assert update is not None

    scalp_engine = ScalpEngine()
    armed = scalp_engine.evaluate(
        ScalpContext(
            symbol="AAPL",
            as_of=update.state.occurred_at,
            current_price=update.state.current_price,
            previous_price=Decimal("100"),
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            session_vwap=Decimal("100.50"),
            atr=Decimal("1"),
            support_low=Decimal("99"),
            support_high=Decimal("100.20"),
            order_flow=update.state,
        )
    ).assessment
    assert armed.state is ScalpState.ARMED

    next_at = NOW + timedelta(seconds=1)
    next_quote = _quote(next_at, "100.19", "100.21")
    flow_engine.ingest_quote(next_quote)
    confirmed_flow = flow_engine.ingest_trade(_trade(4, next_at, "100.21"))
    entered = scalp_engine.evaluate(
        ScalpContext(
            symbol="AAPL",
            as_of=next_at,
            current_price=confirmed_flow.state.current_price,
            previous_price=update.state.current_price,
            bid_price=next_quote.bid_price,
            ask_price=next_quote.ask_price,
            session_vwap=Decimal("100.50"),
            atr=Decimal("1"),
            support_low=Decimal("99"),
            support_high=Decimal("100.20"),
            order_flow=confirmed_flow.state,
            previous_assessment=armed,
        )
    ).assessment
    assert entered.state is ScalpState.ENTRY_CONFIRMED
    assert entered.invalidation is not None
    assert entered.target is not None
    assert entered.max_hold_seconds is not None

    store = InMemoryIntradayOpportunityStore()
    opportunity_engine = IntradayOpportunityEngine(store=store)
    event = await opportunity_engine.open_position(
        source_event_id=entered.assessment_id,
        symbol=entered.symbol,
        strategy_id="scalp-v1",
        side=IntradaySide.LONG,
        quantity=Decimal("10"),
        bid=entered.bid_price,
        ask=entered.ask_price,
        stop_price=entered.invalidation,
        target_price=entered.target,
        occurred_at=entered.occurred_at,
        max_holding=timedelta(seconds=entered.max_hold_seconds),
    )

    assert event is not None
    assert event.opportunity.entry_price == entered.ask_price
    assert event.opportunity.current_price == entered.bid_price
    assert event.opportunity.net_pnl_percent < 0
