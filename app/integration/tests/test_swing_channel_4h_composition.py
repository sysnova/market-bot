from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    SWING_CHANNEL_ASSESSMENT_EVENT,
    SWING_CHANNEL_TRANSITION_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    NamedValue,
    PatternDirection,
    SwingChannelMaturity,
)
from app.integration.swing_channel_4h_composition import SwingChannel4HRuntime
from app.swing_channel_4h_engine import SwingChannel4HEngine, SwingChannel4HEngineV11

START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


class Publisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


def bars() -> tuple[MarketBar, ...]:
    values = [
        ("101", "104", "103"),
        ("98", "102", "101"),
        ("94", "100", "98"),
        ("97", "103", "102"),
        ("100", "107", "106"),
        ("98", "104", "102"),
        ("101", "108", "107"),
        ("103", "111", "110"),
        ("102", "108", "106"),
        ("103.8", "106", "104.5"),
        ("104.2", "108", "107.5"),
    ]
    return tuple(
        MarketBar(
            symbol="AAPL",
            timeframe=BarTimeframe.HOUR_4,
            timestamp=START + timedelta(hours=4 * index),
            open=Decimal(close) - Decimal("0.5"),
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


def swing_result() -> AnalysisResult:
    return AnalysisResult(
        symbol="AAPL",
        horizon=AnalysisHorizon.SWING,
        as_of=bars()[-1].timestamp,
        engine_id="swing",
        engine_version="5.0.0",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("72"),
        confidence=Decimal("0.72"),
        reasons=("pullback",),
        metrics=(
            NamedValue(name="entry_zone_low", value=Decimal("103")),
            NamedValue(name="entry_zone_high", value=Decimal("106")),
        ),
        context_hash="sha256:" + "b" * 64,
    )


@pytest.mark.asyncio
async def test_runtime_publishes_independent_l2_then_l3_transition() -> None:
    publisher = Publisher()
    runtime = SwingChannel4HRuntime(
        engine=SwingChannel4HEngine(), publisher=publisher
    )

    assert await runtime.bootstrap(bars(), symbols=("AAPL",)) == 1
    first = publisher.events[-2:]
    assert first[0][1].event_type == SWING_CHANNEL_ASSESSMENT_EVENT
    assert first[1][1].event_type == SWING_CHANNEL_TRANSITION_EVENT
    assert first[1][1].payload.maturity is SwingChannelMaturity.L2_4H

    result = swing_result()
    await runtime.handle_analysis(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=result.as_of,
            source="swing",
            subject="AAPL",
            payload=result,
        )
    )

    assert publisher.events[-1][1].payload.maturity is SwingChannelMaturity.L3
    assert publisher.events[-1][1].event_type == SWING_CHANNEL_TRANSITION_EVENT


@pytest.mark.asyncio
async def test_runtime_deduplicates_unchanged_observation() -> None:
    publisher = Publisher()
    runtime = SwingChannel4HRuntime(
        engine=SwingChannel4HEngine(), publisher=publisher
    )

    await runtime.bootstrap(bars(), symbols=("AAPL",))
    count = len(publisher.events)
    await runtime.evaluate("AAPL", current_price=bars()[-1].close + Decimal("0.25"))

    assert len(publisher.events) == count


@pytest.mark.asyncio
async def test_v11_runtime_projects_the_published_channel_on_the_next_bar() -> None:
    publisher = Publisher()
    runtime = SwingChannel4HRuntime(
        engine=SwingChannel4HEngineV11(), publisher=publisher
    )
    history = bars()

    await runtime.bootstrap(history, symbols=("AAPL",))
    armed = next(
        event.payload
        for _, event in reversed(publisher.events)
        if event.event_type == SWING_CHANNEL_ASSESSMENT_EVENT
    )
    next_bar = MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.HOUR_4,
        timestamp=history[-1].timestamp + timedelta(hours=4),
        open=Decimal("108"),
        high=Decimal("112"),
        low=Decimal("107"),
        close=Decimal("111"),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )
    await runtime.handle_market(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=next_bar.timestamp,
            source="test",
            subject="AAPL",
            payload=next_bar,
        )
    )
    projected = next(
        event.payload
        for _, event in reversed(publisher.events)
        if event.event_type == SWING_CHANNEL_ASSESSMENT_EVENT
    )

    assert projected.pivot_a_at == armed.pivot_a_at
    assert projected.pivot_b_at == armed.pivot_b_at
    assert projected.pivot_c_at == armed.pivot_c_at
    assert projected.support == armed.support + armed.slope_per_bar
