from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    MARKET_QUOTE_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    market_bar_subject,
)
from app.integration.market_stream_recovery import (
    BufferedMarketDataPublisher,
    ReconnectBackoff,
    pending_recovery_bars,
    recovery_requirement,
)

START = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


def bar(minute: int, *, symbol: str = "AAPL") -> MarketBar:
    timestamp = START + timedelta(minutes=minute)
    return MarketBar(
        symbol=symbol,
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
        source="alpaca",
        feed="sip",
        is_final=True,
    )


def envelope(value: MarketBar) -> EventEnvelope:
    return EventEnvelope(
        event_type=MARKET_BAR_EVENT,
        occurred_at=value.timestamp,
        source="alpaca-market-stream",
        subject=value.symbol,
        payload=value,
    )


@pytest.mark.unit
async def test_recovery_buffer_orders_gap_before_live_and_deduplicates_same_bar() -> None:
    target = RecordingPublisher()
    publisher = BufferedMarketDataPublisher(target, max_buffered_bars=10)
    publisher.begin_recovery()
    recovered = bar(1)
    later = bar(2)

    # Non-bar microstructure events remain live while analytical bars are gated.
    quote = EventEnvelope(
        event_type=MARKET_QUOTE_EVENT,
        occurred_at=START,
        source="alpaca-market-stream",
        subject="AAPL",
        payload={"symbol": "AAPL"},
    )
    await publisher.publish("market.data.quote.AAPL", quote)
    corrected_update = recovered.model_copy(update={"is_final": False})
    await publisher.publish(
        market_bar_subject(BarTimeframe.MINUTE_1, "AAPL"),
        envelope(corrected_update).model_copy(update={"event_type": MARKET_BAR_UPDATED_EVENT}),
    )
    await publisher.publish(market_bar_subject(BarTimeframe.MINUTE_1, "AAPL"), envelope(later))

    published = await publisher.finish_recovery((recovered,))

    assert published == 2
    assert [event.event_type for _, event in target.events] == [
        MARKET_QUOTE_EVENT,
        MARKET_BAR_EVENT,
        MARKET_BAR_EVENT,
    ]
    assert [
        event.payload.timestamp
        for _, event in target.events
        if isinstance(event.payload, MarketBar)
    ] == [recovered.timestamp, later.timestamp]
    assert publisher.final_bar_cursors == {"AAPL": later.timestamp}


@pytest.mark.unit
async def test_recovery_buffer_is_bounded_and_can_be_restarted_cleanly() -> None:
    publisher = BufferedMarketDataPublisher(RecordingPublisher(), max_buffered_bars=1)
    publisher.begin_recovery()
    await publisher.publish(market_bar_subject(BarTimeframe.MINUTE_1, "AAPL"), envelope(bar(1)))

    with pytest.raises(RuntimeError, match="buffer capacity"):
        await publisher.publish(
            market_bar_subject(BarTimeframe.MINUTE_1, "AAPL"), envelope(bar(2))
        )

    publisher.begin_recovery()
    assert await publisher.finish_recovery(()) == 0


@pytest.mark.unit
def test_pending_recovery_uses_per_symbol_cursor_and_excludes_open_minute() -> None:
    values = (
        bar(-5, symbol="NVDA"),
        bar(0),
        bar(1),
        bar(2),
        bar(3, symbol="MSFT"),
    )
    cursors = {"AAPL": START, "MSFT": START + timedelta(minutes=2)}

    pending = pending_recovery_bars(
        values,
        cursors=cursors,
        recovery_started_at=START - timedelta(minutes=5),
        connected_at=START + timedelta(minutes=3),
    )

    assert [(item.symbol, item.timestamp) for item in pending] == [
        ("NVDA", START - timedelta(minutes=5)),
        ("AAPL", START + timedelta(minutes=1)),
        ("AAPL", START + timedelta(minutes=2)),
    ]


@pytest.mark.unit
def test_recovery_requirement_scales_for_long_outages_but_stays_bounded() -> None:
    short = recovery_requirement(START, START + timedelta(minutes=4))
    long = recovery_requirement(START, START + timedelta(days=30))

    assert short.lookback == timedelta(minutes=5)
    assert short.max_bars_per_symbol == 15
    assert long.max_bars_per_symbol == 10_000


@pytest.mark.unit
async def test_reconnect_backoff_resets_only_after_a_stable_session() -> None:
    backoff = ReconnectBackoff(initial_seconds=1, maximum_seconds=300, stable_seconds=60)

    assert backoff.failure_delay(session_uptime_seconds=0) == 1
    assert backoff.failure_delay(session_uptime_seconds=5) == 2
    assert backoff.failure_delay(session_uptime_seconds=5) == 4
    assert backoff.failure_delay(session_uptime_seconds=61) == 1
    assert backoff.failure_delay(session_uptime_seconds=0) == 2
