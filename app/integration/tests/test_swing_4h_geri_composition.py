from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    GERI_ASSESSMENT_EVENT,
    GERI_TRANSITION_EVENT,
    BarTimeframe,
    EventEnvelope,
    GeriMaturity,
    MarketBar,
)
from app.integration.swing_4h_geri_composition import Swing4HGeriRuntime
from app.swing_4h_geri_engine import Swing4HGeriEngine

START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


class Publisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


def bars() -> tuple[MarketBar, ...]:
    values = [
        ("99", "103", "101"),
        ("97", "102", "99"),
        ("100", "106", "105"),
        ("103", "110", "109"),
        ("100", "108", "101"),
        ("95", "102", "96"),
        ("93", "101", "95"),
        ("94", "105", "103"),
        ("101", "112", "111"),
    ]
    return tuple(
        MarketBar(
            symbol="AAPL",
            timeframe=BarTimeframe.HOUR_4,
            timestamp=START + timedelta(hours=4 * index),
            open=Decimal(close) - Decimal("1"),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1000"),
            source="test",
            feed="sip",
            is_final=True,
        )
        for index, (low, high, close) in enumerate(values)
    )


@pytest.mark.asyncio
async def test_runtime_publishes_only_independent_4hgeri_subjects() -> None:
    publisher = Publisher()
    runtime = Swing4HGeriRuntime(engine=Swing4HGeriEngine(), publisher=publisher)

    assert await runtime.bootstrap(bars(), symbols=("AAPL",)) == 1

    assert [event.event_type for _, event in publisher.events] == [
        GERI_ASSESSMENT_EVENT,
        GERI_TRANSITION_EVENT,
    ]
    assert publisher.events[0][0] == "marketbot.v1.4hgeri.assessment.AAPL"
    assert publisher.events[1][1].payload.maturity is GeriMaturity.ARMED


@pytest.mark.asyncio
async def test_runtime_deduplicates_price_noise_inside_same_armed_state() -> None:
    publisher = Publisher()
    runtime = Swing4HGeriRuntime(engine=Swing4HGeriEngine(), publisher=publisher)
    await runtime.bootstrap(bars(), symbols=("AAPL",))
    count = len(publisher.events)

    await runtime.evaluate("AAPL", current_price=Decimal("110.5"))

    assert len(publisher.events) == count
