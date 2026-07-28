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
)
from app.integration.swing_worker import SwingWorker
from app.swing_engine.models import SwingContext

NOW = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)
HASH = "sha256:" + "b" * 64


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.contexts: list[SwingContext] = []

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[object, ...] = (),
    ) -> AnalysisResult:
        self.contexts.append(context)
        return AnalysisResult(
            engine_id="swing-v2",
            engine_version="2.0.0",
            symbol=context.symbol,
            horizon=AnalysisHorizon.SWING,
            as_of=context.as_of,
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            score=Decimal("82"),
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
async def test_swing_worker_bootstraps_own_store_and_reacts_to_completed_15m() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = SwingWorker(publisher=publisher, analyzer=analyzer)

    count = await worker.bootstrap(
        (
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
            bar(BarTimeframe.MINUTE_15, NOW - timedelta(minutes=15)),
        ),
        symbols=("HIMS",),
    )
    live = bar(BarTimeframe.MINUTE_15, NOW)
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
    assert analyzer.contexts[-1].intraday_bars[-1] == live
    assert all(event.event_type == ANALYSIS_RESULT_EVENT for _, event in publisher.events)
    assert all(subject.endswith(".SWING.HIMS") for subject, _ in publisher.events)
