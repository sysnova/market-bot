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
from app.integration.intraday_worker import IntradayWorker
from app.intraday_engine.models import IntradayContext

NOW = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)
HASH = "sha256:" + "c" * 64


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.contexts: list[IntradayContext] = []

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[object, ...] = (),
    ) -> AnalysisResult:
        self.contexts.append(context)
        return AnalysisResult(
            engine_id="intraday-v2",
            engine_version="2.0.0",
            symbol=context.symbol,
            horizon=AnalysisHorizon.INTRADAY,
            as_of=context.as_of,
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            score=Decimal("84"),
            confidence=Decimal("0.8"),
            reasons=("fixture",),
            context_hash=HASH,
        )


def minute(timestamp: datetime, close: str = "100") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        symbol="HIMS",
        timeframe=BarTimeframe.MINUTE_1,
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
async def test_intraday_worker_builds_5m_context_inside_its_own_process() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = IntradayWorker(publisher=publisher, analyzer=analyzer)
    bars = tuple(minute(NOW + timedelta(minutes=index)) for index in range(6))

    count = await worker.bootstrap(bars, symbols=("HIMS",))
    live = minute(NOW + timedelta(minutes=6), close="101")
    await worker.handle_market_event(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=live.timestamp,
            source="test",
            subject="HIMS",
            payload=live,
        )
    )

    assert count == 1
    assert len(analyzer.contexts) == 2
    assert len(analyzer.contexts[-1].five_minute_bars) == 1
    assert analyzer.contexts[-1].minute_bars[-1] == live
    assert all(event.event_type == ANALYSIS_RESULT_EVENT for _, event in publisher.events)
    assert all(subject.endswith(".INTRADAY.HIMS") for subject, _ in publisher.events)


@pytest.mark.unit
async def test_intraday_worker_keeps_unknown_symbol_quiet_until_warmup_completes() -> None:
    publisher = RecordingPublisher()
    worker = IntradayWorker(publisher=publisher, analyzer=RecordingAnalyzer())
    bars = tuple(minute(NOW + timedelta(minutes=index)) for index in range(6))
    worker.activate_universe(())

    assert await worker.bootstrap(bars, symbols=("HIMS",)) == 0
    assert publisher.events == []

    await worker.handle_universe_changed(
        UniverseChanged(
            occurred_at=NOW + timedelta(minutes=6),
            source="postgresql-local",
            previous_symbols=(),
            symbols=("HIMS",),
            added_symbols=("HIMS",),
            removed_symbols=(),
        )
    )

    assert len(publisher.events) == 1


@pytest.mark.unit
async def test_intraday_worker_ignores_extended_hours_bar() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = IntradayWorker(publisher=publisher, analyzer=analyzer)
    await worker.bootstrap((minute(NOW),), symbols=("HIMS",))

    premarket = minute(datetime(2026, 7, 28, 12, 0, tzinfo=UTC), "120")
    await worker.handle_market_event(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=premarket.timestamp,
            source="test",
            subject="HIMS",
            payload=premarket,
        )
    )

    assert len(analyzer.contexts) == 1
    assert analyzer.contexts[-1].minute_bars[-1].close == Decimal("100")
