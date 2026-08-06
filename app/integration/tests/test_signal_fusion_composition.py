from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.alert_engine.confirmed import BuyMaturity
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ELLIOTT_WAVE_ASSESSMENT_EVENT,
    FUSION_ASSESSMENT_EVENT,
    FUSION_BUY_CONFIRMED_EVENT,
    FUSION_RECOVERY_CONFIRMED_EVENT,
    FUSION_TRANSITION_EVENT,
    PATREON_CAPS_ASSESSMENT_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    FusionAssessment,
    FusionState,
    MacroRegime,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatternDirection,
    StrategyMode,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    WaveAssessment,
    WavePhase,
)
from app.integration import signal_fusion_monitor
from app.integration.signal_fusion_composition import (
    SignalFusionRuntime,
    load_fusion_holdings,
)
from app.integration.signal_fusion_monitor import (
    _format_assessment,
    _format_solid_banner,
    run_signal_fusion_monitor,
)
from app.signal_fusion_engine import SignalFusionContext


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


async def test_fusion_universe_is_strictly_positive_holdings() -> None:
    provider = _Universe()

    snapshot = await load_fusion_holdings(provider)

    assert snapshot.symbols == ("TGT", "MSFT")
    assert provider.holdings_calls == 1
    assert provider.universe_calls == 0


def test_tmux_launcher_has_a_two_pane_signal_fusion_window() -> None:
    launcher = Path("scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")

    assert "-n SignalFusion" in launcher
    assert "--role signal-fusion-analysis" in launcher
    assert "--role signal-fusion-buys" in launcher
    assert "FUSION — Z/R/S + GATES" in launcher
    assert "FUSION — BUY CONFIRMED" in launcher


def test_fusion_panel_exposes_every_hard_gate() -> None:
    item = SimpleNamespace(
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        symbol="TGT",
        state=FusionState.ARMED,
        score=Decimal("75"),
        support_zone_gate=True,
        support_reaction_gate=True,
        support_gate=True,
        trend_gate=True,
        timing_gate=True,
        execution_gate=False,
        dilution_gate=True,
        portfolio_gate=True,
        reward_risk_gate=True,
        recovery_gate=False,
        current_price=Decimal("105"),
        trigger_price=Decimal("106"),
        invalidation=Decimal("99"),
        target_price=Decimal("120"),
        reward_risk_ratio=Decimal("2.5"),
        patreon_context="CONFIRMED_BASE",
        missing_sources=(),
    )

    text = _format_assessment(item)

    assert "ARMED" in text
    assert "Z:Y R:Y S:Y L:Y T:Y X:N D:Y P:Y RR:Y" in text
    assert "PAT CONFIRMED_BASE" in text


def test_fusion_panel_distinguishes_defended_zone_from_structure() -> None:
    item = SimpleNamespace(
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        symbol="ADUR",
        state=FusionState.OBSERVING,
        score=Decimal("13"),
        support_zone_gate=True,
        support_reaction_gate=True,
        support_gate=False,
        trend_gate=False,
        timing_gate=False,
        execution_gate=False,
        dilution_gate=True,
        portfolio_gate=True,
        reward_risk_gate=True,
        recovery_gate=False,
        current_price=Decimal("13.78"),
        trigger_price=Decimal("13.86"),
        invalidation=Decimal("12.9477"),
        target_price=Decimal("15.4446"),
        reward_risk_ratio=Decimal("2"),
        patreon_context=None,
        missing_sources=(),
    )

    text = _format_assessment(item)

    assert "ADUR" in text
    assert "Z:Y R:Y S:N" in text


def test_fusion_confirmation_has_explicit_fully_matured_banner() -> None:
    engine = _Engine()
    engine.state = FusionState.BUY_CONFIRMED
    item = engine.evaluate(
        SignalFusionContext(
            symbol="TGT",
            support=_support(),
            wave=None,
            analyses=(),
            holding_quantity=Decimal("1"),
        )
    )

    banner = _format_solid_banner(item, color=True)

    assert banner == (
        "\x1b[1;97;45m TGT | BUY L4 $105 | FUSION BUY CONFIRMED \x1b[0m"
    )


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.items.append((subject, envelope))


class _Engine:
    def __init__(self) -> None:
        self.state = FusionState.ARMED
        self.hash = f"sha256:{'1' * 64}"
        self.contexts: list[SignalFusionContext] = []

    def evaluate(self, context: SignalFusionContext) -> FusionAssessment:
        self.contexts.append(context)
        source = context.support or context.wave
        if source is None:
            raise AssertionError("fixture requires a price source")
        confirmed = self.state in {
            FusionState.BUY_CONFIRMED,
            FusionState.RECOVERY_CONFIRMED,
        }
        recovery = self.state is FusionState.RECOVERY_CONFIRMED
        return FusionAssessment(
            symbol=context.symbol,
            occurred_at=source.occurred_at,
            engine_version="0.1.0",
            state=self.state,
            score=Decimal("90" if confirmed else "75"),
            confidence=Decimal("0.9" if confirmed else "0.75"),
            current_price=Decimal("105"),
            support_zone_gate=True,
            support_reaction_gate=True,
            support_gate=True,
            trend_gate=True,
            timing_gate=True,
            execution_gate=confirmed,
            dilution_gate=True,
            portfolio_gate=True,
            reward_risk_gate=True,
            recovery_gate=recovery,
            trigger_price=Decimal("103"),
            entry_price=Decimal("105"),
            invalidation=Decimal("100"),
            target_price=Decimal("116"),
            reward_risk_ratio=Decimal("2.2"),
            reasons=("fixture",),
            context_hash=self.hash,
        )


def _support() -> SupportAssessment:
    return SupportAssessment(
        symbol="TGT",
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        engine_version="0.1.0",
        state=SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        current_price=Decimal("105"),
        zone_low=Decimal("99"),
        zone_center=Decimal("100"),
        zone_high=Decimal("101"),
        invalidation=Decimal("96"),
        support_score=Decimal("85"),
        reaction_score=Decimal("90"),
        reversal_score=Decimal("70"),
        confidence=Decimal("0.9"),
        reasons=("fixture",),
        context_hash=f"sha256:{'2' * 64}",
    )


def _support_event(item: SupportAssessment) -> EventEnvelope:
    return EventEnvelope(
        event_type=SUPPORT_ASSESSMENT_EVENT,
        occurred_at=item.occurred_at,
        source="support-confirmation-v0",
        subject=item.symbol,
        payload=item,
    )


async def test_runtime_deduplicates_and_emits_buy_only_on_state_change() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SignalFusionRuntime(
        engine=engine,
        publisher=publisher,
        symbols=("TGT",),
        holding_quantities={"TGT": Decimal("10")},
    )
    support = _support()

    await runtime.handle_source(_support_event(support))
    assert publisher.items == []
    assert await runtime.complete_hydration() == 1
    assert [item.event_type for _, item in publisher.items] == [
        FUSION_ASSESSMENT_EVENT,
        FUSION_TRANSITION_EVENT,
    ]

    await runtime.handle_source(_support_event(support))
    assert len(publisher.items) == 2

    engine.state = FusionState.BUY_CONFIRMED
    engine.hash = f"sha256:{'3' * 64}"
    await runtime.handle_source(_support_event(support))
    assert [item.event_type for _, item in publisher.items[-3:]] == [
        FUSION_ASSESSMENT_EVENT,
        FUSION_TRANSITION_EVENT,
        FUSION_BUY_CONFIRMED_EVENT,
    ]
    assert engine.contexts[-1].holding_quantity == Decimal("10")

    engine.state = FusionState.RECOVERY_CONFIRMED
    engine.hash = f"sha256:{'8' * 64}"
    await runtime.handle_source(_support_event(support))
    assert [item.event_type for _, item in publisher.items[-3:]] == [
        FUSION_ASSESSMENT_EVENT,
        FUSION_TRANSITION_EVENT,
        FUSION_RECOVERY_CONFIRMED_EVENT,
    ]


def _wave() -> WaveAssessment:
    return WaveAssessment(
        symbol="TGT",
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        engine_version="0.1.0",
        primary_timeframe=BarTimeframe.DAY_1,
        phase=WavePhase.UNRESOLVED,
        score=Decimal("20"),
        confidence=Decimal("0.2"),
        current_price=Decimal("105"),
        reasons=("fixture",),
        context_hash=f"sha256:{'4' * 64}",
    )


def _patreon() -> PatreonCapsAssessment:
    return PatreonCapsAssessment(
        symbol="TGT",
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        rule_version="1.1.0",
        mode=StrategyMode.SHADOW,
        state=PatreonCapsState.WATCH_ZONE,
        current_price=Decimal("105"),
        zone_low=Decimal("99"),
        zone_center=Decimal("100"),
        zone_high=Decimal("101"),
        invalidation=Decimal("96"),
        atr14=Decimal("3"),
        confluence_score=Decimal("60"),
        confirmation_score=Decimal("40"),
        alignment_score=Decimal("50"),
        patreon_score=Decimal("55"),
        macro_regime=MacroRegime.NEUTRAL,
        reasons=("fixture",),
    )


def _long() -> AnalysisResult:
    return AnalysisResult(
        engine_id="long-term-engine",
        engine_version="2.0.0",
        symbol="TGT",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=datetime(2026, 8, 2, 20, tzinfo=UTC),
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("80"),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        context_hash=f"sha256:{'5' * 64}",
    )


def _event(event_type: str, payload: object) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        occurred_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
        source="fixture",
        subject="TGT",
        payload=payload,
    )


async def test_runtime_hydrates_every_source_contract_without_feedback() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SignalFusionRuntime(
        engine=engine,
        publisher=publisher,
        symbols=("TGT",),
        holding_quantities={"TGT": Decimal("3")},
    )

    assert await runtime.complete_hydration() == 0
    await runtime.handle_source(_event("ignored", {}))
    await runtime.handle_source(
        _event(ELLIOTT_WAVE_ASSESSMENT_EVENT, _wave().model_dump(mode="json"))
    )
    assert engine.contexts[-1].wave is not None

    engine.hash = f"sha256:{'6' * 64}"
    await runtime.handle_source(_event(PATREON_CAPS_ASSESSMENT_EVENT, _patreon()))
    assert engine.contexts[-1].patreon is not None

    engine.hash = f"sha256:{'7' * 64}"
    await runtime.handle_source(_event(ANALYSIS_RESULT_EVENT, _long()))
    assert engine.contexts[-1].analyses[0].horizon is AnalysisHorizon.LONG_TERM
    assert all(
        item.event_type != FUSION_BUY_CONFIRMED_EVENT
        for _, item in publisher.items
    )


async def test_runtime_restores_serialized_fusion_state() -> None:
    publisher = _Publisher()
    engine = _Engine()
    runtime = SignalFusionRuntime(
        engine=engine,
        publisher=publisher,
        symbols=("TGT",),
        holding_quantities={"TGT": Decimal("1")},
    )
    assessment = engine.evaluate(
        SignalFusionContext(
            symbol="TGT",
            support=_support(),
            wave=None,
            analyses=(),
            holding_quantity=Decimal("1"),
        )
    )
    await runtime.restore_fusion(_event("ignored", {}))
    await runtime.restore_fusion(
        _event(FUSION_ASSESSMENT_EVENT, assessment.model_dump(mode="json"))
    )
    await runtime.handle_source(_support_event(_support()))

    assert await runtime.complete_hydration() == 0
    assert publisher.items == []


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _MonitorBus:
    envelope: EventEnvelope
    instance: _MonitorBus | None = None

    def __init__(self) -> None:
        self.subscription = _Subscription()
        self.closed = False
        self.subjects: list[str] = []
        type(self).instance = self

    @classmethod
    async def connect(cls, **_: Any) -> _MonitorBus:
        return cls()

    async def subscribe(self, subject: str, handler: Any, **_: Any) -> _Subscription:
        self.subjects.append(subject)
        await handler(self.envelope)
        return self.subscription

    async def wait_until_caught_up(self, *_: Any, **__: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _StopEvent:
    async def wait(self) -> None:
        raise RuntimeError("stop monitor")


async def test_analysis_monitor_replays_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _Engine()
    assessment = engine.evaluate(
        SignalFusionContext(
            symbol="TGT",
            support=_support(),
            wave=None,
            analyses=(),
            holding_quantity=Decimal("1"),
        )
    )
    _MonitorBus.envelope = _event(FUSION_ASSESSMENT_EVENT, assessment)
    monkeypatch.setattr(signal_fusion_monitor, "NatsJetStreamEventBus", _MonitorBus)
    monkeypatch.setattr(signal_fusion_monitor.asyncio, "Event", _StopEvent)
    output = StringIO()

    with pytest.raises(RuntimeError, match="stop monitor"):
        await run_signal_fusion_monitor(mode="analysis", stream=output, bell=False)

    assert "ARMED" in output.getvalue()
    assert "GATES Z=zona R=reaccion S=estructura" in output.getvalue()
    assert _MonitorBus.instance is not None
    assert _MonitorBus.instance.subscription.unsubscribed is True
    assert _MonitorBus.instance.closed is True


async def test_buy_monitor_includes_recovery_confirmations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    engine.state = FusionState.RECOVERY_CONFIRMED
    assessment = engine.evaluate(
        SignalFusionContext(
            symbol="TGT",
            support=_support(),
            wave=None,
            analyses=(),
            holding_quantity=Decimal("1"),
        )
    )
    _MonitorBus.envelope = _event(FUSION_RECOVERY_CONFIRMED_EVENT, assessment)
    monkeypatch.setattr(signal_fusion_monitor, "NatsJetStreamEventBus", _MonitorBus)
    monkeypatch.setattr(signal_fusion_monitor.asyncio, "Event", _StopEvent)
    alarm_calls: list[BuyMaturity] = []
    monkeypatch.setattr(
        signal_fusion_monitor,
        "play_buy_maturity_sound",
        lambda maturity, **_: alarm_calls.append(maturity) or True,
    )
    output = StringIO()

    with pytest.raises(RuntimeError, match="stop monitor"):
        await run_signal_fusion_monitor(mode="buys", stream=output, bell=True)

    assert "RECOVERY_CONFIRMED" in output.getvalue()
    assert "BUY L4 $105 | FUSION RECOVERY CONFIRMED" in output.getvalue()
    assert alarm_calls == [BuyMaturity.FULLY_MATURED]
    assert "marketbot.v1.signal-fusion.recovery-confirmed.>" in (
        _MonitorBus.instance.subjects if _MonitorBus.instance is not None else []
    )
