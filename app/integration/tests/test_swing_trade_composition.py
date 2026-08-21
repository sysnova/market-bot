from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    MARKET_BAR_EVENT,
    SWING_TRADE_ASSESSMENT_EVENT,
    SWING_TRADE_TRANSITION_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
)
from app.integration.swing_trade_composition import SwingTradeRuntime
from app.swing_trade_engine import SwingTradeEngine
from app.swing_trade_engine.tests.test_engine import daily_bars


class Publisher:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        del subject
        self.events.append(envelope)


def minute(at: datetime, *, final: bool = True) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=at,
        open=Decimal("97"),
        high=Decimal("97.1"),
        low=Decimal("96.9"),
        close=Decimal("97"),
        volume=Decimal("100"),
        source="test",
        feed="sip",
        is_final=final,
    )


def envelope(bar: MarketBar) -> EventEnvelope:
    return EventEnvelope(
        event_type=MARKET_BAR_EVENT,
        occurred_at=bar.timestamp,
        source="test",
        subject=bar.symbol,
        payload=bar,
    )


@pytest.mark.asyncio
async def test_runtime_evaluates_only_when_a_completed_15m_bucket_emits() -> None:
    publisher = Publisher()
    runtime = SwingTradeRuntime(engine=SwingTradeEngine(), publisher=publisher)
    await runtime.bootstrap(daily_bars(), symbols=("AAPL",))
    start = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)

    for offset in range(15):
        await runtime.handle_market(envelope(minute(start + timedelta(minutes=offset))))
    assert publisher.events == []

    await runtime.handle_market(envelope(minute(start + timedelta(minutes=15))))
    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]


@pytest.mark.asyncio
async def test_runtime_deduplicates_the_same_completed_15m_observation() -> None:
    publisher = Publisher()
    runtime = SwingTradeRuntime(engine=SwingTradeEngine(), publisher=publisher)
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))
    count = len(publisher.events)
    await runtime.handle_market(envelope(bar))

    assert count == 3
    assert len(publisher.events) == count
