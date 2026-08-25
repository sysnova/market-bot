from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    MARKET_BAR_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_TRANSITION_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    SubscriptionOptions,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportTransition,
)
from app.integration import support_confirmation_monitor
from app.integration.support_confirmation_composition import (
    SupportConfirmationRuntime,
    load_support_holdings,
    load_support_universe,
    support_analysis_symbols,
)
from app.integration.support_confirmation_monitor import (
    _format_assessment,
    _format_reentry,
    _is_reentry_transition,
    run_support_confirmation_monitor,
)
from app.support_confirmation_engine import SupportContext


class _Universe:
    def __init__(self) -> None:
        self.holdings_calls = 0
        self.universe_calls = 0

    async def get_holdings(self) -> SimpleNamespace:
        self.holdings_calls += 1
        return SimpleNamespace(symbols=("TGT", "MSFT"), source="postgresql-local-holdings")

    async def get_universe(self) -> SimpleNamespace:
        self.universe_calls += 1
        return SimpleNamespace(symbols=("TGT", "MSFT", "WATCH_ONLY"))


async def test_support_universe_is_strictly_positive_holdings() -> None:
    provider = _Universe()

    snapshot = await load_support_holdings(provider)

    assert snapshot.symbols == ("TGT", "MSFT")
    assert provider.holdings_calls == 1
    assert provider.universe_calls == 0


async def test_support_universe_rejects_an_empty_portfolio() -> None:
    provider = _Universe()

    async def empty_holdings() -> SimpleNamespace:
        return SimpleNamespace(symbols=(), source="postgresql-local-holdings")

    provider.get_holdings = empty_holdings  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="positive local holding"):
        await load_support_holdings(provider)


async def test_support_enrichment_universe_includes_watchlist_symbols() -> None:
    provider = _Universe()

    snapshot = await load_support_universe(provider)

    assert snapshot.symbols == ("TGT", "MSFT", "WATCH_ONLY")
    assert provider.universe_calls == 1
    assert provider.holdings_calls == 0


def test_support_universe_always_includes_fixed_thesis_underlyings() -> None:
    assert support_analysis_symbols(
        ("TGT", "asts"),
        ("ASTS", "NBIS"),
    ) == ("TGT", "ASTS", "NBIS")


def test_tmux_launcher_has_a_sibling_support_confirmation_window() -> None:
    launcher = Path("scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")

    assert "-n SupportConfirmation" in launcher
    assert "--role support-confirmation" in launcher
    assert "SUPPORT CONFIRMATION" in launcher


def test_tmux_launcher_can_leave_the_runtime_detached() -> None:
    launcher = Path("scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")

    assert "--detach" in launcher
    assert "((DETACH)) && return" in launcher


def test_panel_separates_reaction_from_reversal() -> None:
    item = SimpleNamespace(
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        data_as_of=datetime(2026, 8, 2, 4, tzinfo=UTC),
        assessed_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        symbol="TGT",
        state=SupportState.RECLAIMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        reaction_score=Decimal("82"),
        reversal_score=Decimal("25"),
        support_score=Decimal("80"),
        current_price=Decimal("105"),
        zone_low=Decimal("99"),
        zone_high=Decimal("101"),
        invalidation=Decimal("96"),
        b_wave_risk=True,
        structural_supports=(
            SimpleNamespace(
                source="weekly_sma200",
                price=Decimal("391.03"),
                distance_percent=Decimal("19.81"),
                distance_atr=Decimal("5.53"),
            ),
        ),
        impulse_origin=Decimal("377.39"),
        impulse_origin_at=datetime(2026, 7, 23, tzinfo=UTC),
        impulse_peak=Decimal("491.65"),
        impulse_advance_percent=Decimal("30.28"),
    )

    text = _format_assessment(item)

    assert "RECLAIMED" in text
    assert text.startswith("20:00 TGT")
    assert "DATA 08-02 04:00" in text
    assert "REACT 82" in text
    assert "REV 25" in text
    assert "B-RISK YES" in text
    assert "STRUCT W-SMA200:391.03" in text
    assert "IMP 377.39@07-23 +30.28%" in text


def test_reentry_alarm_only_accepts_structural_confirmation() -> None:
    assessment = SupportAssessment(
        symbol="ADUR",
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        engine_version="0.1.0",
        state=SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        current_price=Decimal("13.78"),
        zone_low=Decimal("12.75"),
        zone_center=Decimal("13.10"),
        zone_high=Decimal("13.53"),
        invalidation=Decimal("11.78"),
        support_score=Decimal("100"),
        reaction_score=Decimal("93"),
        reversal_score=Decimal("70"),
        confidence=Decimal("1"),
        reasons=("fixture",),
        context_hash=f"sha256:{'9' * 64}",
    )
    structural = SupportTransition(
        assessment_id=assessment.assessment_id,
        symbol=assessment.symbol,
        occurred_at=assessment.occurred_at,
        engine_version=assessment.engine_version,
        previous_state=SupportState.RECLAIMED,
        state=SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=assessment.confirmation_type,
        support_score=assessment.support_score,
        reaction_score=assessment.reaction_score,
        reversal_score=assessment.reversal_score,
        zone_low=assessment.zone_low,
        zone_high=assessment.zone_high,
        invalidation=assessment.invalidation,
        reasons=assessment.reasons,
        context_hash=assessment.context_hash,
    )
    reclaimed = structural.model_copy(
        update={
            "previous_state": SupportState.LIQUIDITY_SWEEP,
            "state": SupportState.RECLAIMED,
        }
    )

    assert _is_reentry_transition(structural) is True
    assert _is_reentry_transition(reclaimed) is False
    assert "REENTRY ARMED" in _format_reentry(structural)
    assert "ADUR" in _format_reentry(structural)


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.items.append((subject, envelope))


class _Engine:
    def __init__(self) -> None:
        self.state = SupportState.RECLAIMED
        self.hash = f"sha256:{'1' * 64}"
        self.contexts: list[SupportContext] = []

    def evaluate(self, context: SupportContext) -> SupportAssessment:
        self.contexts.append(context)
        return SupportAssessment(
            symbol=context.symbol,
            occurred_at=context.daily_bars[-1].timestamp,
            engine_version="0.1.0",
            state=self.state,
            confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
            current_price=context.daily_bars[-1].close,
            zone_low=Decimal("99"),
            zone_center=Decimal("100"),
            zone_high=Decimal("101"),
            invalidation=Decimal("96"),
            support_score=Decimal("80"),
            reaction_score=Decimal("82"),
            reversal_score=Decimal("25"),
            confidence=Decimal("0.82"),
            reasons=("test",),
            context_hash=self.hash,
        )


def _bar(
    index: int,
    *,
    symbol: str = "TGT",
    timeframe: BarTimeframe = BarTimeframe.DAY_1,
    final: bool = True,
) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=index),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        is_final=final,
        source="test",
        feed="test",
    )


def _envelope(bar: MarketBar, *, event_type: str = MARKET_BAR_EVENT) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        occurred_at=bar.timestamp,
        source="test",
        subject=bar.symbol,
        payload=bar,
    )


async def test_runtime_publishes_assessment_transition_and_deduplicates() -> None:
    publisher = _Publisher()
    engine = _Engine()
    assessed_at = datetime(2026, 8, 3, 22, 30, tzinfo=UTC)
    runtime = SupportConfirmationRuntime(
        engine=engine,
        publisher=publisher,
        clock=FrozenClock(assessed_at),
    )

    published = await runtime.bootstrap(
        (*(_bar(index) for index in range(15)), _bar(1, symbol="WATCH")),
        symbols=("TGT",),
    )

    assert published == 1
    assert [item.event_type for _, item in publisher.items] == [
        SUPPORT_ASSESSMENT_EVENT,
        SUPPORT_TRANSITION_EVENT,
    ]
    assert publisher.items[0][0].endswith("assessment.TGT")
    assert publisher.items[1][0].endswith("transition.RECLAIMED.TGT")
    assessment_envelope = publisher.items[0][1]
    assessment = assessment_envelope.payload
    assert isinstance(assessment, SupportAssessment)
    assert assessment.data_as_of == _bar(14).timestamp
    assert assessment.assessed_at == assessed_at
    assert assessment_envelope.occurred_at == assessed_at
    assert publisher.items[1][1].occurred_at == assessed_at

    await runtime.handle_market(_envelope(_bar(15)))
    assert len(publisher.items) == 2

    engine.hash = f"sha256:{'2' * 64}"
    await runtime.handle_market(_envelope(_bar(16)))
    assert len(publisher.items) == 3

    engine.state = SupportState.STRUCTURE_CONFIRMED
    engine.hash = f"sha256:{'3' * 64}"
    await runtime.handle_market(_envelope(_bar(17)))
    assert len(publisher.items) == 5
    transition = publisher.items[-1][1].payload
    assert isinstance(transition, SupportTransition)
    assert engine.contexts[-1].previous_assessment is not None

    engine.hash = f"sha256:{'4' * 64}"
    hourly = _bar(18, timeframe=BarTimeframe.HOUR_1).model_copy(
        update={"timestamp": datetime(2026, 7, 20, 14, tzinfo=UTC)}
    )
    await runtime.handle_market(_envelope(hourly))
    assert len(publisher.items) == 6
    assert engine.contexts[-1].hourly_bars[-1].timeframe is BarTimeframe.HOUR_1


async def test_runtime_restores_state_and_ignores_irrelevant_market_events() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SupportConfirmationRuntime(engine=engine, publisher=publisher)
    assessment = engine.evaluate(
        SupportContext(symbol="TGT", daily_bars=tuple(_bar(index) for index in range(15)))
    )
    restore = EventEnvelope(
        event_type=SUPPORT_ASSESSMENT_EVENT,
        occurred_at=assessment.occurred_at,
        source="support-confirmation-v0",
        subject="TGT",
        payload=assessment.model_dump(mode="json"),
    )
    await runtime.restore_assessment(restore)
    await runtime.restore_assessment(
        EventEnvelope(
            event_type="ignored",
            occurred_at=assessment.occurred_at,
            source="test",
            subject="TGT",
            payload={},
        )
    )
    assert await runtime.bootstrap(tuple(_bar(index) for index in range(15)), symbols=("TGT",)) == 0

    await runtime.handle_market(_envelope(_bar(15), event_type="ignored"))
    await runtime.handle_market(_envelope(_bar(15, symbol="MSFT")))
    await runtime.handle_market(_envelope(_bar(15, final=False)))
    await runtime.handle_market(_envelope(_bar(15, timeframe=BarTimeframe.MINUTE_15)))
    await runtime.handle_market(_envelope(_bar(15, timeframe=BarTimeframe.WEEK_1)))
    assert publisher.items == []


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _MonitorBus:
    assessment: EventEnvelope
    transition: EventEnvelope
    instance: _MonitorBus | None = None

    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, SubscriptionOptions, _Subscription]] = []
        self.closed = False
        type(self).instance = self

    @classmethod
    async def connect(cls, **_: Any) -> _MonitorBus:
        return cls()

    async def subscribe(
        self, subject: str, handler: Any, *, options: SubscriptionOptions
    ) -> _Subscription:
        subscription = _Subscription()
        self.subscriptions.append((subject, options, subscription))
        envelope = self.transition if ".transition." in subject else self.assessment
        await handler(envelope)
        return subscription

    async def close(self) -> None:
        self.closed = True


class _StopEvent:
    async def wait(self) -> None:
        raise RuntimeError("stop monitor")


async def test_monitor_rings_only_for_a_new_structural_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    assessment = engine.evaluate(
        SupportContext(symbol="TGT", daily_bars=tuple(_bar(index) for index in range(15)))
    )
    transition = SupportTransition(
        assessment_id=assessment.assessment_id,
        symbol=assessment.symbol,
        occurred_at=assessment.occurred_at,
        engine_version=assessment.engine_version,
        previous_state=SupportState.RECLAIMED,
        state=SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=assessment.confirmation_type,
        support_score=assessment.support_score,
        reaction_score=assessment.reaction_score,
        reversal_score=Decimal("70"),
        zone_low=assessment.zone_low,
        zone_high=assessment.zone_high,
        invalidation=assessment.invalidation,
        reasons=("fixture",),
        context_hash=assessment.context_hash,
    )
    _MonitorBus.assessment = EventEnvelope(
        event_type=SUPPORT_ASSESSMENT_EVENT,
        occurred_at=assessment.occurred_at,
        source="fixture",
        subject=assessment.symbol,
        payload=assessment,
    )
    _MonitorBus.transition = EventEnvelope(
        event_type=SUPPORT_TRANSITION_EVENT,
        occurred_at=transition.occurred_at,
        source="fixture",
        subject=transition.symbol,
        payload=transition,
    )
    monkeypatch.setattr(support_confirmation_monitor, "NatsJetStreamEventBus", _MonitorBus)
    monkeypatch.setattr(support_confirmation_monitor.asyncio, "Event", _StopEvent)
    output = StringIO()

    with pytest.raises(RuntimeError, match="stop monitor"):
        await run_support_confirmation_monitor(stream=output, bell=True)

    assert output.getvalue().count("\a") == 1
    assert "REENTRY ARMED TGT STRUCTURE_CONFIRMED" in output.getvalue()
    assert _MonitorBus.instance is not None
    transition_options = _MonitorBus.instance.subscriptions[1][1]
    assert transition_options.replay_all is False
    assert all(
        subscription.unsubscribed for _, _, subscription in _MonitorBus.instance.subscriptions
    )
    assert _MonitorBus.instance.closed is True
