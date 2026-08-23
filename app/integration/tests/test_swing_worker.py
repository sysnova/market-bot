from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    OrderFlowStateKind,
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
    PatternDirection,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    UniverseChanged,
    new_uuid7,
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


@pytest.mark.unit
async def test_swing_worker_publishes_added_symbol_only_after_worker_warmup() -> None:
    publisher = RecordingPublisher()
    worker = SwingWorker(publisher=publisher, analyzer=RecordingAnalyzer())
    worker.activate_universe(())
    assert (
        await worker.bootstrap(
            (
                bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
                bar(BarTimeframe.MINUTE_15, NOW - timedelta(minutes=15)),
            ),
            symbols=("HIMS",),
        )
        == 0
    )

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


@pytest.mark.unit
async def test_swing_worker_ignores_after_hours_price() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = SwingWorker(publisher=publisher, analyzer=analyzer)
    await worker.bootstrap(
        (
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
            bar(BarTimeframe.MINUTE_15, NOW - timedelta(minutes=15)),
        ),
        symbols=("HIMS",),
    )

    after_hours = bar(
        BarTimeframe.MINUTE_1,
        datetime(2026, 7, 28, 21, 0, tzinfo=UTC),
        "130",
    )
    await worker.handle_market_event(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=after_hours.timestamp,
            source="test",
            subject="HIMS",
            payload=after_hours,
        )
    )

    assert len(analyzer.contexts) == 1
    assert analyzer.contexts[-1].price == Decimal("100")


@pytest.mark.unit
async def test_swing_worker_passes_latest_support_assessment_to_the_engine() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = SwingWorker(publisher=publisher, analyzer=analyzer)
    support = SupportAssessment(
        symbol="HIMS",
        occurred_at=NOW - timedelta(days=1),
        engine_version="0.2.0",
        state=SupportState.REACTION_CONFIRMED,
        confirmation_type=SupportConfirmationType.V_RECOVERY,
        current_price=Decimal("100"),
        zone_low=Decimal("99"),
        zone_center=Decimal("100"),
        zone_high=Decimal("101"),
        invalidation=Decimal("97"),
        support_score=Decimal("80"),
        reaction_score=Decimal("70"),
        reversal_score=Decimal("30"),
        confidence=Decimal("0.7"),
        reasons=("fixture",),
        context_hash="sha256:" + "8" * 64,
    )
    await worker.handle_support_event(
        EventEnvelope(
            event_type=SUPPORT_ASSESSMENT_EVENT,
            occurred_at=NOW,
            source="support-confirmation-v0",
            subject="HIMS",
            payload=support,
        )
    )

    await worker.bootstrap(
        (
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
            bar(BarTimeframe.MINUTE_15, NOW - timedelta(minutes=15)),
        ),
        symbols=("HIMS",),
    )

    assert analyzer.contexts[-1].support == support


@pytest.mark.unit
async def test_swing_worker_passes_latest_order_flow_support_evidence() -> None:
    publisher = RecordingPublisher()
    analyzer = RecordingAnalyzer()
    worker = SwingWorker(publisher=publisher, analyzer=analyzer)
    support_id = new_uuid7()
    support = SupportAssessment(
        assessment_id=support_id,
        symbol="HIMS",
        occurred_at=NOW - timedelta(days=1),
        engine_version="0.3.0",
        state=SupportState.REACTION_CONFIRMED,
        current_price=Decimal("100"),
        zone_low=Decimal("99"),
        zone_center=Decimal("100"),
        zone_high=Decimal("101"),
        invalidation=Decimal("97"),
        support_score=Decimal("80"),
        reaction_score=Decimal("70"),
        reversal_score=Decimal("60"),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        context_hash="sha256:" + "8" * 64,
    )
    evidence = OrderFlowSupportAssessment(
        symbol="HIMS",
        occurred_at=NOW - timedelta(seconds=10),
        engine_version="1.0.0",
        disposition=OrderFlowSupportDisposition.CONFIRMS_SUPPORT,
        support_assessment_id=support_id,
        order_flow_state_id=new_uuid7(),
        support_occurred_at=NOW - timedelta(days=1),
        order_flow_occurred_at=NOW - timedelta(seconds=10),
        current_price=Decimal("100"),
        zone_low=Decimal("99"),
        zone_high=Decimal("101"),
        order_flow_state=OrderFlowStateKind.SELLER_EXHAUSTION,
        confidence=Decimal("0.8"),
        data_quality=Decimal("0.9"),
        quote_fresh=True,
        fresh_until=NOW + timedelta(seconds=110),
        reasons=("seller_exhaustion_over_support",),
        context_hash="sha256:" + "9" * 64,
    )
    await worker.handle_support_event(
        EventEnvelope(
            event_type=SUPPORT_ASSESSMENT_EVENT,
            occurred_at=support.occurred_at,
            source="support-confirmation-v0",
            subject="HIMS",
            payload=support,
        )
    )
    await worker.bootstrap(
        (
            bar(BarTimeframe.DAY_1, NOW - timedelta(days=1)),
            bar(BarTimeframe.MINUTE_15, NOW - timedelta(minutes=15)),
        ),
        symbols=("HIMS",),
    )
    await worker.handle_order_flow_support_event(
        EventEnvelope(
            event_type=ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
            occurred_at=evidence.occurred_at,
            source="order-flow",
            subject="HIMS",
            payload=evidence,
        )
    )

    assert analyzer.contexts[-1].order_flow_support == evidence
