from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    MARKET_BAR_EVENT,
    SWING_TRADE_ASSESSMENT_EVENT,
    SWING_TRADE_TRANSITION_EVENT,
    BarTimeframe,
    EntrySignal,
    EventEnvelope,
    MarketBar,
    NamedValue,
    SwingTradeAssessment,
    SwingTradeTransition,
)
from app.integration.swing_trade_composition import (
    SwingTradeRuntime,
    swing_trade_replay_subjects,
)
from app.swing_trade_engine import SwingTradeEngine
from app.swing_trade_engine.tests.test_engine import analyze, daily_bars


class Publisher:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        del subject
        self.events.append(envelope)


class FixedEngine:
    def __init__(self, assessment: SwingTradeAssessment) -> None:
        self._assessment = assessment

    def analyze(self, context: object) -> SwingTradeAssessment:
        del context
        return self._assessment


class RejectingEngine:
    def analyze(self, context: object) -> SwingTradeAssessment:
        del context
        raise ValueError("no valid impulse")


def test_swing_trade_replay_excludes_order_flow_before_v15() -> None:
    order_flow_subject = "marketbot.v1.order-flow.support.>"

    assert order_flow_subject not in swing_trade_replay_subjects("1.4.0")
    assert order_flow_subject in swing_trade_replay_subjects("1.5.0")


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
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    runtime = SwingTradeRuntime(
        engine=SwingTradeEngine(),
        publisher=publisher,
        clock=FrozenClock(at + timedelta(minutes=20)),
    )
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))
    count = len(publisher.events)
    await runtime.handle_market(envelope(bar))

    assert count == 3
    assert len(publisher.events) == count


@pytest.mark.asyncio
async def test_initial_rejected_assessment_publishes_only_analysis() -> None:
    publisher = Publisher()
    runtime = SwingTradeRuntime(engine=SwingTradeEngine(), publisher=publisher)
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    bar = minute(at).model_copy(
        update={
            "timeframe": BarTimeframe.MINUTE_15,
            "open": Decimal("110"),
            "high": Decimal("110.1"),
            "low": Decimal("109.9"),
            "close": Decimal("110"),
        }
    )

    published = await runtime.bootstrap(
        (*daily_bars(support=Decimal("105")), bar), symbols=("AAPL",)
    )

    assert published == 1
    assert [item.event_type for item in publisher.events] == [SWING_TRADE_ASSESSMENT_EVENT]
    assessment = publisher.events[0].payload
    assert isinstance(assessment, SwingTradeAssessment)
    assert assessment.maturity is None
    assert assessment.invalidation > assessment.zone_high


@pytest.mark.asyncio
async def test_runtime_exposes_rejected_evaluation_reasons() -> None:
    runtime = SwingTradeRuntime(engine=RejectingEngine(), publisher=Publisher())
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    published = await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))

    assert published == 0
    assert runtime.diagnostics() == {"no valid impulse": 1}


@pytest.mark.asyncio
async def test_actionable_assessment_allows_invalidation_inside_fibonacci_zone() -> None:
    publisher = Publisher()
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    runtime = SwingTradeRuntime(
        engine=SwingTradeEngine(),
        publisher=publisher,
        clock=FrozenClock(at + timedelta(minutes=20)),
    )
    bar = minute(at).model_copy(
        update={
            "timeframe": BarTimeframe.MINUTE_15,
            "open": Decimal("99"),
            "high": Decimal("99.1"),
            "low": Decimal("98.9"),
            "close": Decimal("99"),
        }
    )

    await runtime.bootstrap((*daily_bars(support=Decimal("102")), bar), symbols=("AAPL",))

    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]
    assessment = publisher.events[0].payload
    assert isinstance(assessment, SwingTradeAssessment)
    assert assessment.maturity is not None
    assert assessment.zone_low < assessment.invalidation < assessment.current_price


@pytest.mark.asyncio
async def test_thesis_loss_uses_previous_actionable_setup_to_close_tracking() -> None:
    previous = analyze("97")
    rejected = analyze("110", support="105").model_copy(
        update={"occurred_at": previous.occurred_at + timedelta(minutes=15)}
    )
    publisher = Publisher()
    runtime = SwingTradeRuntime(engine=FixedEngine(rejected), publisher=publisher)
    await runtime.restore_assessment(
        EventEnvelope(
            event_type=SWING_TRADE_ASSESSMENT_EVENT,
            occurred_at=previous.occurred_at,
            source="test",
            subject=previous.symbol,
            payload=previous,
        )
    )
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    bar = minute(at).model_copy(
        update={
            "timeframe": BarTimeframe.MINUTE_15,
            "open": Decimal("110"),
            "high": Decimal("110.1"),
            "low": Decimal("109.9"),
            "close": Decimal("110"),
        }
    )

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))

    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]
    transition = publisher.events[1].payload
    assert isinstance(transition, SwingTradeTransition)
    assert transition.previous_maturity is previous.maturity
    assert transition.maturity is None
    signal = publisher.events[2].payload
    assert isinstance(signal, EntrySignal)
    assert signal.swing_trade_maturity is None
    assert signal.zone_low == previous.zone_low
    assert signal.zone_high == previous.zone_high
    assert signal.invalidation == previous.invalidation
    assert signal.setup_id == str(
        next(metric.value for metric in previous.metrics if metric.name == "setup_id")
    )


@pytest.mark.asyncio
async def test_runtime_publishes_setup_identity_migration_without_market_change() -> None:
    previous = analyze("97")
    canonical_setup = (
        f"swing-trade:AAPL:{previous.impulse_low_at.isoformat()}:"
        f"{previous.impulse_high_at.isoformat()}"
    )
    current = previous.model_copy(
        update={
            "engine_version": "1.4.0",
            "metrics": (
                *(item for item in previous.metrics if item.name != "setup_id"),
                NamedValue(name="setup_id", value=canonical_setup),
            ),
        }
    )
    publisher = Publisher()
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    runtime = SwingTradeRuntime(
        engine=FixedEngine(current),
        publisher=publisher,
        clock=FrozenClock(at + timedelta(minutes=20)),
    )
    await runtime.restore_assessment(
        EventEnvelope(
            event_type=SWING_TRADE_ASSESSMENT_EVENT,
            occurred_at=previous.occurred_at,
            source="test",
            subject=previous.symbol,
            payload=previous,
        )
    )
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))

    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]
    signal = publisher.events[-1].payload
    assert isinstance(signal, EntrySignal)
    assert signal.setup_id == canonical_setup


@pytest.mark.asyncio
async def test_runtime_publishes_strategy_migration_without_market_change() -> None:
    previous = analyze("97")
    current = previous.model_copy(update={"strategy_version": "1.2.0"})
    publisher = Publisher()
    at = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)
    runtime = SwingTradeRuntime(
        engine=FixedEngine(current),
        publisher=publisher,
        clock=FrozenClock(at + timedelta(minutes=20)),
    )
    await runtime.restore_assessment(
        EventEnvelope(
            event_type=SWING_TRADE_ASSESSMENT_EVENT,
            occurred_at=previous.occurred_at,
            source="test",
            subject=previous.symbol,
            payload=previous,
        )
    )
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))

    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]
    signal = publisher.events[-1].payload
    assert isinstance(signal, EntrySignal)
    assert signal.policy_version == "1.2.0"


@pytest.mark.asyncio
async def test_bootstrap_does_not_emit_actionable_signal_from_previous_session() -> None:
    assessment = analyze("97")
    publisher = Publisher()
    at = datetime(2026, 8, 20, 19, 45, tzinfo=UTC)
    runtime = SwingTradeRuntime(
        engine=FixedEngine(assessment),
        publisher=publisher,
        clock=FrozenClock(datetime(2026, 8, 21, 10, 0, tzinfo=UTC)),
    )
    bar = minute(at).model_copy(update={"timeframe": BarTimeframe.MINUTE_15})

    await runtime.bootstrap((*daily_bars(), bar), symbols=("AAPL",))

    assert [item.event_type for item in publisher.events] == [
        SWING_TRADE_ASSESSMENT_EVENT,
        SWING_TRADE_TRANSITION_EVENT,
    ]
