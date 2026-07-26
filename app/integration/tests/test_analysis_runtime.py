from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.alert_engine import AlertDispatcher, AlertEngine
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    LocalAlert,
    MarketBar,
    PatternDirection,
)
from app.integration.analysis_runtime import AnalysisRuntime
from app.integration.market_bar_store import MarketBarStore

NOW = datetime(2026, 7, 24, 19, 59, tzinfo=UTC)
HASH = "sha256:" + "b" * 64


class StaticEngine:
    def __init__(self, horizon: AnalysisHorizon) -> None:
        self.horizon = horizon
        self.contexts: list[Any] = []

    def analyze(
        self,
        context: Any,
        *,
        source_event_ids: tuple[Any, ...] = (),
    ) -> AnalysisResult:
        self.contexts.append(context)
        return AnalysisResult(
            engine_id=f"{self.horizon.value.lower()}-test",
            engine_version="1.0.0",
            symbol=context.symbol,
            horizon=self.horizon,
            as_of=context.as_of,
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            score=Decimal("84"),
            confidence=Decimal("1"),
            reasons=("fixture",),
            source_event_ids=source_event_ids,
            context_hash=HASH,
        )


class RecordingPublisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.items.append((subject, envelope))


class RecordingSink:
    def __init__(self) -> None:
        self.alerts: list[LocalAlert] = []

    def emit(self, alert: LocalAlert) -> None:
        self.alerts.append(alert)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def bar(timeframe: BarTimeframe, index: int, count: int) -> MarketBar:
    step = {
        BarTimeframe.MINUTE_1: timedelta(minutes=1),
        BarTimeframe.MINUTE_15: timedelta(minutes=15),
        BarTimeframe.DAY_1: timedelta(days=1),
        BarTimeframe.WEEK_1: timedelta(weeks=1),
    }[timeframe]
    timestamp = NOW - step * (count - index)
    price = Decimal("100") + Decimal(index) / Decimal("10")
    return MarketBar(
        symbol="AAPL",
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
async def test_backfill_is_quiet_then_evaluate_all_fans_results_into_one_alert() -> None:
    store = MarketBarStore()
    for timeframe, count in (
        (BarTimeframe.MINUTE_1, 30),
        (BarTimeframe.MINUTE_15, 21),
        (BarTimeframe.DAY_1, 50),
        (BarTimeframe.WEEK_1, 50),
    ):
        for index in range(count):
            store.add(bar(timeframe, index, count))
    publisher = RecordingPublisher()
    sink = RecordingSink()
    long_term = StaticEngine(AnalysisHorizon.LONG_TERM)
    swing = StaticEngine(AnalysisHorizon.SWING)
    intraday = StaticEngine(AnalysisHorizon.INTRADAY)
    runtime = AnalysisRuntime(
        store=store,
        publisher=publisher,
        long_term=long_term,
        swing=swing,
        intraday=intraday,
        alert_engine=AlertEngine(),
        alert_dispatcher=AlertDispatcher(sinks=(sink,)),
        clock=FixedClock(),
    )
    dilution = AnalysisResult(
        engine_id="dilution-test",
        engine_version="1.0.0",
        symbol="AAPL",
        horizon=AnalysisHorizon.DILUTION,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.NEUTRAL,
        score=Decimal("0"),
        confidence=Decimal("1"),
        reasons=("no dilution evidence",),
        context_hash=HASH,
    )

    await runtime.ingest_analysis(dilution)
    assert sink.alerts == []
    await runtime.evaluate_all(("AAPL",))

    analysis_events = [
        item
        for item in publisher.items
        if item[1].event_type == "analysis.result.produced"
    ]
    assert len(analysis_events) == 4
    assert analysis_events[-1][0] == "marketbot.v1.analysis.result.INTRADAY.AAPL"
    assert len(sink.alerts) == 1
    assert "order" not in sink.alerts[0].model_dump()


@pytest.mark.unit
async def test_market_events_only_trigger_live_intraday_after_enable() -> None:
    publisher = RecordingPublisher()
    intraday = StaticEngine(AnalysisHorizon.INTRADAY)
    runtime = AnalysisRuntime(
        store=MarketBarStore(),
        publisher=publisher,
        long_term=StaticEngine(AnalysisHorizon.LONG_TERM),
        swing=StaticEngine(AnalysisHorizon.SWING),
        intraday=intraday,
        alert_engine=AlertEngine(),
        alert_dispatcher=AlertDispatcher(sinks=()),
        clock=FixedClock(),
    )
    latest = bar(BarTimeframe.MINUTE_1, 29, 30)
    for index in range(29):
        historical = bar(BarTimeframe.MINUTE_1, index, 30)
        await runtime.handle_market_event(
            EventEnvelope(
                event_type="market.bar.received",
                occurred_at=historical.timestamp,
                source="test",
                subject="AAPL",
                payload=historical,
            )
        )
    assert intraday.contexts == []

    runtime.enable_live()
    await runtime.handle_market_event(
        EventEnvelope(
            event_type="market.bar.received",
            occurred_at=latest.timestamp,
            source="test",
            subject="AAPL",
            payload=latest,
        )
    )

    assert len(intraday.contexts) == 1
