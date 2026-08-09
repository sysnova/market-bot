from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_SIGNAL_EVENT,
    LOCAL_ALERT_EVENT,
    MARKET_BAR_EVENT,
    PATREON_CAPS_ASSESSMENT_EVENT,
    PATREON_CAPS_TRANSITION_EVENT,
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EventEnvelope,
    MacroRegime,
    MarketBar,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    PatternDirection,
    StrategyMode,
    new_uuid7,
)
from app.integration import patreon_caps_store as store_module
from app.integration.distributed_composition import _stream_symbols
from app.integration.patreon_caps_composition import (
    PatreonCapsRuntime,
    _hydrate_latest_analyses,
    _local_alert,
    _subscribe_live_analyses,
)
from app.integration.patreon_caps_monitor import _format_assessment, _format_transition
from app.integration.patreon_caps_store import PostgresPatreonCapsStore, _deduplication_key
from app.patreon_caps_engine import PatreonCapsEvaluation, PatreonCapsWatch

NOW = datetime(2026, 8, 1, 15, 30, tzinfo=UTC)


def _transition(state: PatreonCapsState) -> PatreonCapsTransition:
    return PatreonCapsTransition(
        watch_id=new_uuid7(),
        symbol="NVO",
        previous_state=(
            PatreonCapsState.WATCH_ZONE
            if state is PatreonCapsState.SUPPORT_TEST
            else PatreonCapsState.SUPPORT_TEST
        ),
        state=state,
        occurred_at=NOW,
        rule_version="1.0.0",
        current_price=Decimal("48.5"),
        zone_low=Decimal("47"),
        zone_center=Decimal("48"),
        zone_high=Decimal("48.5"),
        invalidation=Decimal("45"),
        confluence_score=Decimal("80"),
        confirmation_score=Decimal("75"),
        alignment_score=Decimal("100"),
        patreon_score=Decimal("83"),
        macro_regime=MacroRegime.RISK_ON,
        tranche_stage=1,
        suggested_tranche_usd=Decimal("900"),
        suggested_whole_shares=Decimal("18"),
        source_analysis_ids=(new_uuid7(),),
        reasons=("confirmed",),
        expires_at=NOW + timedelta(days=56),
    )


def _assessment(state: PatreonCapsState) -> PatreonCapsAssessment:
    return PatreonCapsAssessment(
        symbol="NVO",
        occurred_at=NOW,
        rule_version="1.0.0",
        mode=StrategyMode.PRIMARY,
        state=state,
        current_price=Decimal("48.5"),
        zone_low=Decimal("47"),
        zone_center=Decimal("48"),
        zone_high=Decimal("48.5"),
        invalidation=Decimal("45"),
        atr14=Decimal("2"),
        confluence_score=Decimal("80"),
        confirmation_score=Decimal("75"),
        alignment_score=Decimal("100"),
        patreon_score=Decimal("83"),
        macro_regime=MacroRegime.RISK_ON,
        macro_threshold=Decimal("75"),
        support_sources=("pivot_daily", "avwap", "sma_weekly"),
        source_analysis_ids=(new_uuid7(),),
        reasons=("confirmed",),
    )


def _evaluation(state: PatreonCapsState = PatreonCapsState.CONFIRMED_V) -> PatreonCapsEvaluation:
    transition = _transition(state)
    assessment = _assessment(state)
    return PatreonCapsEvaluation(
        assessment=assessment,
        watch=PatreonCapsWatch(
            watch_id=transition.watch_id,
            symbol="NVO",
            rule_version="1.0.0",
            state=state,
            armed_at=NOW - timedelta(days=1),
            updated_at=NOW,
            expires_at=NOW + timedelta(days=56),
            zone_low=Decimal("47"),
            zone_center=Decimal("48"),
            zone_high=Decimal("48.5"),
            invalidation=Decimal("45"),
            highest_price=Decimal("49"),
            tranche_stage=1,
            support_sources=("pivot_daily", "avwap", "sma_weekly"),
            source_analysis_ids=transition.source_analysis_ids,
        ),
        transition=transition,
    )


def _bar(timeframe: BarTimeframe, index: int, *, symbol: str = "NVO") -> MarketBar:
    spacing = timedelta(days=7 if timeframe is BarTimeframe.WEEK_1 else 1)
    if timeframe in {BarTimeframe.MINUTE_1, BarTimeframe.MINUTE_15}:
        spacing = timedelta(minutes=1 if timeframe is BarTimeframe.MINUTE_1 else 15)
    elif timeframe is BarTimeframe.HOUR_1:
        spacing = timedelta(hours=1)
    timestamp = NOW - spacing * (300 - index)
    price = Decimal("40") + Decimal(index) / Decimal("10")
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price + Decimal("0.5"),
        volume=Decimal("1000"),
        vwap=price + Decimal("0.4"),
        source="fixture",
        feed="test",
        is_final=True,
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        engine_id="long-term-v2",
        engine_version="2.0.0",
        symbol="NVO",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("85"),
        confidence=Decimal("0.8"),
        reasons=("bullish",),
        context_hash="sha256:" + "a" * 64,
    )


class _Engine:
    def __init__(self, evaluation: PatreonCapsEvaluation | None) -> None:
        self.evaluation = evaluation
        self.contexts: list[Any] = []

    def evaluate(self, context: Any, *, now: datetime) -> PatreonCapsEvaluation | None:
        self.contexts.append((context, now))
        return self.evaluation


class _Publisher:
    def __init__(self, order: list[str]) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []
        self.order = order

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.order.append(envelope.event_type)
        self.events.append((subject, envelope))


class _Store:
    def __init__(self, order: list[str], *, inserted: bool = True) -> None:
        self.order = order
        self.inserted = inserted

    async def save(self, evaluation: PatreonCapsEvaluation) -> bool:
        self.order.append("persisted")
        return self.inserted


class _Portfolio:
    async def get_holding_quantity(self, symbol: str) -> Decimal:
        assert symbol == "NVO"
        return Decimal("3")


class _LatestAnalysisBus:
    def __init__(self, envelopes: dict[str, EventEnvelope]) -> None:
        self.envelopes = envelopes
        self.subjects: list[str] = []

    async def get_last(self, subject: str) -> EventEnvelope | None:
        self.subjects.append(subject)
        return self.envelopes.get(subject)


class _AnalysisCollector:
    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def handle_analysis(self, envelope: EventEnvelope) -> None:
        self.envelopes.append(envelope)


class _FakeSubscription:
    async def unsubscribe(self) -> None:
        return None


class _LiveAnalysisBus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any]] = []

    async def subscribe(
        self,
        subject: str,
        handler: Any,
        *,
        options: Any = None,
    ) -> _FakeSubscription:
        self.calls.append((subject, handler, options))
        return _FakeSubscription()


def test_market_stream_unions_macro_proxies_without_duplicates() -> None:
    assert _stream_symbols(("NVO", "SPY"), ("UUP", "SPY", "TLT")) == (
        "NVO",
        "SPY",
        "UUP",
        "TLT",
    )


async def test_hydration_reads_only_latest_exact_horizons_for_portfolio() -> None:
    nvo = EventEnvelope(
        event_type=ANALYSIS_RESULT_EVENT,
        occurred_at=NOW,
        source="long-term-v2",
        subject="NVO",
        payload=_analysis(),
    )
    bus = _LatestAnalysisBus(
        {"marketbot.v1.analysis.result.LONG_TERM.NVO": nvo}
    )
    runtime = _AnalysisCollector()

    await _hydrate_latest_analyses(bus, runtime, ("NVO", "TGT"))

    assert bus.subjects == [
        "marketbot.v1.analysis.result.LONG_TERM.NVO",
        "marketbot.v1.analysis.result.SWING.NVO",
        "marketbot.v1.analysis.result.INTRADAY.NVO",
        "marketbot.v1.analysis.result.LONG_TERM.TGT",
        "marketbot.v1.analysis.result.SWING.TGT",
        "marketbot.v1.analysis.result.INTRADAY.TGT",
    ]
    assert runtime.envelopes == [nvo]


async def test_live_analysis_subscriptions_use_one_subject_per_horizon_without_replay() -> None:
    bus = _LiveAnalysisBus()
    runtime = _AnalysisCollector()

    subscriptions = await _subscribe_live_analyses(bus, runtime)

    assert len(subscriptions) == 3
    assert [subject for subject, _, _ in bus.calls] == [
        "marketbot.v1.analysis.result.LONG_TERM.>",
        "marketbot.v1.analysis.result.SWING.>",
        "marketbot.v1.analysis.result.INTRADAY.>",
    ]
    assert all(options.replay_all is False for _, _, options in bus.calls)
    assert all(
        options.replay_latest_per_subject is False for _, _, options in bus.calls
    )


def test_patreon_buy_is_an_explicit_analytical_local_alert() -> None:
    alert = _local_alert(_transition(PatreonCapsState.CONFIRMED_V))

    assert alert.kind is AlertKind.PATREON_CAPS_BUY
    assert alert.severity is AlertSeverity.ACTION
    assert any(item.name == "patreon_caps_rule_version" for item in alert.metrics)


def test_local_alert_maps_watch_and_invalidation_states() -> None:
    watch = _local_alert(_transition(PatreonCapsState.WATCH_ZONE))
    invalidated = _local_alert(_transition(PatreonCapsState.INVALIDATED))

    assert watch.kind is AlertKind.PATREON_CAPS_WATCH
    assert watch.severity is AlertSeverity.WATCH
    assert invalidated.kind is AlertKind.PATREON_CAPS_INVALIDATED


async def test_runtime_persists_before_publishing_full_transition() -> None:
    order: list[str] = []
    engine = _Engine(_evaluation())
    publisher = _Publisher(order)
    runtime = PatreonCapsRuntime(
        engine=engine,  # type: ignore[arg-type]
        publisher=publisher,
        store=_Store(order),
        portfolio_data=_Portfolio(),
        allocations={"NVO": Decimal("4.31")},
        portfolio_capital_usd=Decimal("103000"),
        macro_symbols=("SPY",),
        require_hourly=True,
        analysis_settle_seconds=0,
    )
    bars = (
        *(_bar(BarTimeframe.DAY_1, index) for index in range(260)),
        *(_bar(BarTimeframe.WEEK_1, index) for index in range(220)),
        *(_bar(BarTimeframe.HOUR_1, index) for index in range(205)),
        *(_bar(BarTimeframe.MINUTE_15, index) for index in range(160)),
    )
    await runtime.bootstrap(bars, symbols=("NVO", "SPY"))
    analysis = _analysis()
    await runtime.handle_analysis(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=NOW,
            source="fixture",
            subject="NVO",
            payload=analysis,
        )
    )
    assert order == []
    await runtime.complete_hydration()

    assert order == [
        "persisted",
        PATREON_CAPS_ASSESSMENT_EVENT,
        PATREON_CAPS_TRANSITION_EVENT,
        LOCAL_ALERT_EVENT,
        ENTRY_SIGNAL_EVENT,
    ]
    assert len(engine.contexts) == 1
    assert engine.contexts[0][0].held_quantity == Decimal("3")
    assert engine.contexts[0][0].target_weight_percent == Decimal("4.31")
    assert len(engine.contexts[0][0].hourly_bars) == 205


async def test_runtime_filters_noise_and_does_not_publish_duplicate_transition() -> None:
    order: list[str] = []
    engine = _Engine(_evaluation())
    publisher = _Publisher(order)
    runtime = PatreonCapsRuntime(
        engine=engine,  # type: ignore[arg-type]
        publisher=publisher,
        store=_Store(order, inserted=False),
        portfolio_data=_Portfolio(),
        allocations={},
        portfolio_capital_usd=Decimal("103000"),
        macro_symbols=("SPY",),
        analysis_settle_seconds=0,
    )
    bars = (
        *(_bar(BarTimeframe.DAY_1, index) for index in range(260)),
        *(_bar(BarTimeframe.WEEK_1, index) for index in range(220)),
        *(_bar(BarTimeframe.MINUTE_15, index) for index in range(160)),
    )
    await runtime.bootstrap(bars, symbols=("NVO", "SPY"))
    await runtime.handle_market(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=NOW,
            source="fixture",
            subject="OTHER",
            payload=_bar(BarTimeframe.DAY_1, 299, symbol="OTHER"),
        )
    )
    await runtime.handle_analysis(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=NOW,
            source="fixture",
            subject="NVO",
            payload=_analysis().model_dump(mode="json"),
        )
    )
    await runtime.complete_hydration()

    assert order == ["persisted"]
    assert publisher.events == []


async def test_runtime_publishes_assessment_without_transition_or_sizing() -> None:
    order: list[str] = []
    evaluation = _evaluation(PatreonCapsState.SUPPORT_TEST).model_copy(
        update={"transition": None}
    )
    engine = _Engine(evaluation)
    publisher = _Publisher(order)
    runtime = PatreonCapsRuntime(
        engine=engine,  # type: ignore[arg-type]
        publisher=publisher,
        store=_Store(order),
        portfolio_data=_Portfolio(),
        allocations={},
        portfolio_capital_usd=Decimal("103000"),
        macro_symbols=(),
        analysis_settle_seconds=0,
    )
    bars = (
        *(_bar(BarTimeframe.DAY_1, index) for index in range(260)),
        *(_bar(BarTimeframe.WEEK_1, index) for index in range(220)),
        *(_bar(BarTimeframe.MINUTE_15, index) for index in range(160)),
    )
    await runtime.bootstrap(bars, symbols=("NVO",))
    await runtime.handle_analysis(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=NOW,
            source="fixture",
            subject="NVO",
            payload=_analysis(),
        )
    )
    await runtime.complete_hydration()

    assert order == [PATREON_CAPS_ASSESSMENT_EVENT]
    assert publisher.events[0][1].payload == evaluation.assessment


async def test_runtime_restart_does_not_reprocess_the_same_market_snapshot() -> None:
    order: list[str] = []
    engine = _Engine(_evaluation())
    runtime = PatreonCapsRuntime(
        engine=engine,  # type: ignore[arg-type]
        publisher=_Publisher(order),
        store=_Store(order),
        portfolio_data=_Portfolio(),
        allocations={},
        portfolio_capital_usd=Decimal("103000"),
        macro_symbols=(),
        last_evaluated_at={"NVO": NOW},
        analysis_settle_seconds=0,
    )
    bars = (
        *(_bar(BarTimeframe.DAY_1, index) for index in range(260)),
        *(_bar(BarTimeframe.WEEK_1, index) for index in range(220)),
        *(_bar(BarTimeframe.MINUTE_15, index) for index in range(160)),
    )
    await runtime.bootstrap(bars, symbols=("NVO",))
    await runtime.handle_analysis(
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=NOW,
            source="fixture",
            subject="NVO",
            payload=_analysis(),
        )
    )
    await runtime.complete_hydration()

    assert engine.contexts == []
    assert order == []


async def test_runtime_requires_a_new_market_snapshot_for_live_evaluation() -> None:
    order: list[str] = []
    engine = _Engine(_evaluation())
    runtime = PatreonCapsRuntime(
        engine=engine,  # type: ignore[arg-type]
        publisher=_Publisher(order),
        store=_Store(order),
        portfolio_data=_Portfolio(),
        allocations={},
        portfolio_capital_usd=Decimal("103000"),
        macro_symbols=(),
        analysis_settle_seconds=0,
    )
    bars = (
        *(_bar(BarTimeframe.DAY_1, index) for index in range(260)),
        *(_bar(BarTimeframe.WEEK_1, index) for index in range(220)),
        *(_bar(BarTimeframe.MINUTE_15, index) for index in range(160)),
    )
    await runtime.bootstrap(bars, symbols=("NVO",))
    initial = _analysis()
    initial_envelope = EventEnvelope(
        event_type=ANALYSIS_RESULT_EVENT,
        occurred_at=NOW,
        source="fixture",
        subject="NVO",
        payload=initial,
    )
    await runtime.handle_analysis(initial_envelope)
    await runtime.complete_hydration()
    assert len(engine.contexts) == 1

    await runtime.handle_analysis(initial_envelope)
    assert len(engine.contexts) == 1

    newer = initial.model_copy(update={"as_of": NOW + timedelta(minutes=15)})
    await runtime.handle_analysis(initial_envelope.model_copy(update={"payload": newer}))
    assert len(engine.contexts) == 1

    await runtime.handle_market(
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=NOW + timedelta(days=1),
            source="fixture",
            subject="NVO",
            payload=_bar(BarTimeframe.DAY_1, 301),
        )
    )
    newest = initial.model_copy(update={"as_of": NOW + timedelta(minutes=30)})
    await runtime.handle_analysis(initial_envelope.model_copy(update={"payload": newest}))
    assert len(engine.contexts) == 2


def test_terminal_formatting_exposes_scores_sizing_and_colors() -> None:
    assessment = _format_assessment(_assessment(PatreonCapsState.SUPPORT_TEST))
    transition = _transition(PatreonCapsState.CONFIRMED_V)

    assert "C/Q/A/L/P 80/75/100/0/83" in assessment
    assert "MACRO RISK_ON:75" in assessment
    assert "T1 USD 900 SH 18" in _format_transition(transition, color=False)
    assert _format_transition(transition, color=True).startswith("\033[32m")
    assert "BLOCK" in _format_assessment(
        _assessment(PatreonCapsState.WATCH_ZONE).model_copy(
            update={"macro_threshold": None, "support_sources": ()}
        )
    )


def test_transition_deduplication_key_is_stage_and_time_scoped() -> None:
    transition = _transition(PatreonCapsState.IMPULSE_RETEST)

    assert _deduplication_key(transition).endswith(
        f":IMPULSE_RETEST:1:{int(NOW.timestamp())}"
    )


class _SessionContext:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: Any) -> None:
        return None


async def test_postgres_store_readiness_and_recent_payloads() -> None:
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=["watches", "transitions"]),
        scalars=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        payload=_transition(
                            PatreonCapsState.WATCH_ZONE
                        ).model_dump(mode="json")
                    )
                ]
            )
        ),
    )
    store = PostgresPatreonCapsStore(lambda: _SessionContext(session))  # type: ignore[arg-type]

    assert await store.is_ready() is True
    recent = await store.recent(limit=1)

    assert recent[0].state is PatreonCapsState.WATCH_ZONE
    assert session.scalars.await_count == 1


async def test_postgres_store_loads_and_saves_full_engine_state(monkeypatch: Any) -> None:
    evaluation = _evaluation()
    repository = SimpleNamespace(
        load_active=AsyncMock(
            return_value=[SimpleNamespace(payload=evaluation.watch.model_dump(mode="json"))]
        ),
        save=AsyncMock(return_value=True),
    )

    class _Unit:
        patreon_caps = repository

        async def __aenter__(self) -> _Unit:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(store_module, "PersistenceUnitOfWork", lambda _factory: _Unit())
    store = PostgresPatreonCapsStore(lambda: None)  # type: ignore[arg-type]

    active = await store.load_active()
    inserted = await store.save(evaluation)

    assert active == (evaluation.watch,)
    assert inserted is True
    watch_record, transition_record = repository.save.await_args.args
    assert watch_record.symbol == "NVO"
    assert transition_record.state == PatreonCapsState.CONFIRMED_V.value


async def test_postgres_store_loads_latest_transition_watermarks() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(return_value=[("NVO", NOW), ("HIMS", NOW - timedelta(days=1))])
    )
    store = PostgresPatreonCapsStore(lambda: _SessionContext(session))  # type: ignore[arg-type]

    latest = await store.latest_transition_times(rule_version="1.1.0")

    assert latest == {"NVO": NOW, "HIMS": NOW - timedelta(days=1)}
    assert session.execute.await_count == 1


async def test_postgres_store_rejects_evaluation_without_transition() -> None:
    evaluation = _evaluation().model_copy(update={"transition": None})
    store = PostgresPatreonCapsStore(lambda: None)  # type: ignore[arg-type]

    assert await store.save(evaluation) is False
