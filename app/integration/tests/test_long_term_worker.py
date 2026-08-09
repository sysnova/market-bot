from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    PatternDirection,
    UniverseChanged,
)
from app.integration.long_term_worker import LongTermWorker
from app.long_term_engine.models import LongTermContext

NOW = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.contexts: list[LongTermContext] = []

    def analyze(
        self,
        context: LongTermContext,
        *,
        source_event_ids: tuple[object, ...] = (),
    ) -> AnalysisResult:
        self.contexts.append(context)
        return AnalysisResult(
            engine_id="long-term-v2",
            engine_version="2.0.0",
            symbol=context.symbol,
            horizon=AnalysisHorizon.LONG_TERM,
            as_of=context.as_of,
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            score=Decimal("80"),
            confidence=Decimal("0.8"),
            reasons=("fixture",),
            context_hash=HASH,
        )


def bar(timeframe: BarTimeframe, timestamp: datetime, close: str = "100") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        symbol="HIMS",
        timeframe=timeframe,
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1000"),
        source="test",
        feed="sip",
    )


@pytest.mark.unit
async def test_long_worker_owns_history_and_publishes_results() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = LongTermWorker(publisher=publisher, analyzer=analyzer)

    count = await worker.bootstrap(
        (
            bar(BarTimeframe.WEEK_1, NOW - timedelta(days=7)),
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
        ),
        symbols=("HIMS",),
    )
    live = bar(BarTimeframe.DAY_1, NOW)
    await worker.handle_market_event(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=NOW,
            source="test",
            subject="HIMS",
            payload=live,
        )
    )

    assert count == 1
    assert len(analyzer.contexts) == 2
    assert analyzer.contexts[-1].daily_bars[-1] == live
    assert all(event.event_type == ANALYSIS_RESULT_EVENT for _, event in publisher.events)
    assert all(subject.endswith(".LONG_TERM.HIMS") for subject, _ in publisher.events)


@pytest.mark.unit
async def test_long_worker_reprices_completed_history_from_live_minutes() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = LongTermWorker(publisher=publisher, analyzer=analyzer)
    daily = bar(BarTimeframe.DAY_1, NOW - timedelta(days=1), "98")
    weekly = bar(BarTimeframe.WEEK_1, NOW - timedelta(days=7), "95")
    await worker.bootstrap((daily, weekly), symbols=("HIMS",))

    live = bar(BarTimeframe.MINUTE_1, NOW, "103")
    await worker.handle_market_event(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=NOW,
            source="alpaca-market-stream",
            subject="HIMS",
            payload=live,
        )
    )

    assert len(analyzer.contexts) == 2
    assert analyzer.contexts[-1].price == Decimal("103")
    assert analyzer.contexts[-1].as_of == NOW
    assert analyzer.contexts[-1].daily_bars == (daily,)


@pytest.mark.unit
async def test_long_worker_publishes_added_symbol_only_after_worker_warmup() -> None:
    publisher = RecordingPublisher()
    worker = LongTermWorker(publisher=publisher, analyzer=RecordingAnalyzer())
    worker.activate_universe(())
    assert await worker.bootstrap(
        (
            bar(BarTimeframe.WEEK_1, NOW - timedelta(days=7)),
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
        ),
        symbols=("HIMS",),
    ) == 0

    count = await worker.handle_universe_changed(
        UniverseChanged(
            occurred_at=NOW,
            source="postgresql-local",
            previous_symbols=(),
            symbols=("HIMS",),
            added_symbols=("HIMS",),
            removed_symbols=(),
        )
    )

    assert count == 1
    assert len(publisher.events) == 1
