"""NATS/PostgreSQL composition for the PatreonCaps analysis engine."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    LOCAL_ALERT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    PATREON_CAPS_ASSESSMENT_EVENT,
    PATREON_CAPS_TRANSITION_EVENT,
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    EventHandler,
    LocalAlert,
    MarketBar,
    NamedValue,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    Subscription,
    SubscriptionOptions,
    analysis_result_subject,
    local_alert_subject,
    patreon_caps_assessment_subject,
    patreon_caps_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.patreon_caps_engine import (
    PatreonCapsContext,
    PatreonCapsEngine,
    PatreonCapsEvaluation,
    PatreonCapsPolicy,
)
from app.patreon_caps_engine.macro import classify_macro_regime
from app.persistence import create_database_engine, create_session_factory

from .bar_aggregator import MinuteBarAggregator
from .distributed_composition import (
    HistoryRequest,
    connect_nats,
    write_ready,
)
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_signal_adapter import entry_signal_from_alert, publish_entry_signal
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .patreon_caps_store import PostgresPatreonCapsStore
from .postgres_universe import PostgresUniverseClient, fallback_universe
from .universe_policy import universe_health_details

PATREON_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.DAY_1, lookback=timedelta(days=400), max_bars_per_symbol=260
    ),
    HistoryRequest(
        timeframe=BarTimeframe.WEEK_1, lookback=timedelta(days=365 * 5), max_bars_per_symbol=220
    ),
    HistoryRequest(
        timeframe=BarTimeframe.HOUR_1, lookback=timedelta(days=60), max_bars_per_symbol=220
    ),
    HistoryRequest(
        timeframe=BarTimeframe.MINUTE_15, lookback=timedelta(days=14), max_bars_per_symbol=160
    ),
    HistoryRequest(
        timeframe=BarTimeframe.MINUTE_1, lookback=timedelta(days=7), max_bars_per_symbol=500
    ),
)
PATREON_ANALYSIS_HORIZONS = (
    AnalysisHorizon.LONG_TERM,
    AnalysisHorizon.SWING,
    AnalysisHorizon.INTRADAY,
)


class PatreonCapsStore(Protocol):
    async def save(self, evaluation: PatreonCapsEvaluation) -> bool: ...

    async def latest_transition_times(self, *, rule_version: str) -> dict[str, datetime]: ...


class PortfolioData(Protocol):
    async def get_holding_quantity(self, symbol: str) -> Decimal: ...


class LatestEventReader(Protocol):
    async def get_last(self, subject: str) -> EventEnvelope | None: ...


class AnalysisHandler(Protocol):
    async def handle_analysis(self, envelope: EventEnvelope) -> None: ...


class LiveEventSubscriber(Protocol):
    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        *,
        options: SubscriptionOptions | None = None,
    ) -> Subscription: ...


class PatreonCapsRuntime:
    """Join raw bars and fresh upstream analyses without importing another engine."""

    def __init__(
        self,
        *,
        engine: PatreonCapsEngine,
        publisher: EventPublisher,
        store: PatreonCapsStore,
        portfolio_data: PortfolioData,
        allocations: dict[str, Decimal],
        portfolio_capital_usd: Decimal,
        macro_symbols: tuple[str, ...],
        require_hourly: bool = False,
        last_evaluated_at: dict[str, datetime] | None = None,
        analysis_settle_seconds: float = 0.25,
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._store = store
        self._portfolio_data = portfolio_data
        self._allocations = allocations
        self._portfolio_capital_usd = portfolio_capital_usd
        self._macro_symbols = frozenset(macro_symbols)
        self._bars = MarketBarStore(capacity_per_series=500)
        self._aggregator = MinuteBarAggregator(
            targets=(BarTimeframe.MINUTE_15, BarTimeframe.HOUR_1)
        )
        self._analyses: dict[str, dict[AnalysisHorizon, AnalysisResult]] = {}
        self._macro = classify_macro_regime({})
        self._symbols: set[str] = set()
        self._require_hourly = require_hourly
        self._last_evaluated_at = dict(last_evaluated_at or {})
        self._analysis_settle_seconds = analysis_settle_seconds
        self._analysis_generations: dict[str, int] = {}
        self._hydrating = True
        self._state_lock = asyncio.Lock()

    async def bootstrap(
        self,
        bars: Iterable[MarketBar],
        *,
        symbols: tuple[str, ...],
    ) -> None:
        self._symbols = set(symbols) - self._macro_symbols
        for bar in bars:
            self._bars.add(bar)
        self._refresh_macro()

    async def handle_analysis(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        if result.symbol not in self._symbols or result.horizon not in PATREON_ANALYSIS_HORIZONS:
            return
        async with self._state_lock:
            values = self._analyses.setdefault(result.symbol, {})
            previous = values.get(result.horizon)
            if previous is not None and result.as_of <= previous.as_of:
                return
            values[result.horizon] = result
            if self._hydrating:
                return
            generation = self._next_generation(result.symbol)

        await self._evaluate_after_settle(result.symbol, generation)

    async def handle_market(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = (
            envelope.payload
            if isinstance(envelope.payload, MarketBar)
            else MarketBar.model_validate(envelope.payload, strict=False)
        )
        if not bar.is_final:
            return
        generation: int | None = None
        async with self._state_lock:
            self._bars.add(bar)
            if bar.symbol in self._macro_symbols:
                if bar.timeframe is BarTimeframe.DAY_1:
                    self._refresh_macro()
                return
            if bar.symbol not in self._symbols:
                return
            if bar.timeframe is BarTimeframe.MINUTE_1:
                aggregates = self._aggregator.add(bar)
                for aggregate in aggregates:
                    self._bars.add(aggregate)
                if aggregates and not self._hydrating:
                    generation = self._next_generation(bar.symbol)
            elif (
                bar.timeframe
                in {
                    BarTimeframe.DAY_1,
                    BarTimeframe.WEEK_1,
                    BarTimeframe.HOUR_1,
                    BarTimeframe.MINUTE_15,
                }
                and not self._hydrating
            ):
                generation = self._next_generation(bar.symbol)

        if generation is not None:
            await self._evaluate_after_settle(bar.symbol, generation)

    async def complete_hydration(self) -> None:
        """Evaluate one coherent latest snapshot, then enable live transitions."""

        async with self._state_lock:
            for symbol in sorted(self._analyses):
                await self._evaluate(symbol)
            self._hydrating = False

    def _next_generation(self, symbol: str) -> int:
        generation = self._analysis_generations.get(symbol, 0) + 1
        self._analysis_generations[symbol] = generation
        return generation

    async def _evaluate_after_settle(self, symbol: str, generation: int) -> None:
        if self._analysis_settle_seconds:
            await asyncio.sleep(self._analysis_settle_seconds)
        async with self._state_lock:
            if self._analysis_generations.get(symbol) != generation:
                return
            await self._evaluate(symbol)

    def _refresh_macro(self) -> None:
        series = {
            symbol: self._bars.history(symbol, BarTimeframe.DAY_1, limit=260, final_only=True)
            for symbol in self._macro_symbols
        }
        self._macro = classify_macro_regime(series)

    async def _evaluate(self, symbol: str) -> None:
        daily = self._bars.history(symbol, BarTimeframe.DAY_1, limit=260, final_only=True)
        weekly = self._bars.history(symbol, BarTimeframe.WEEK_1, limit=220, final_only=True)
        intraday = self._bars.history(symbol, BarTimeframe.MINUTE_15, limit=160, final_only=True)
        hourly = self._bars.history(symbol, BarTimeframe.HOUR_1, limit=220, final_only=True)
        if (
            len(daily) < 260
            or len(weekly) < 220
            or len(intraday) < 160
            or (self._require_hourly and len(hourly) < 205)
        ):
            return
        held_quantity = await self._portfolio_data.get_holding_quantity(symbol)
        timestamps = [daily[-1].timestamp, weekly[-1].timestamp, intraday[-1].timestamp]
        if hourly:
            timestamps.append(hourly[-1].timestamp)
        occurred_at = max(timestamps)
        previous_evaluation = self._last_evaluated_at.get(symbol)
        if previous_evaluation is not None and occurred_at <= previous_evaluation:
            return
        evaluation = self._engine.evaluate(
            PatreonCapsContext(
                symbol=symbol,
                as_of=occurred_at,
                daily_bars=daily,
                weekly_bars=weekly,
                hourly_bars=hourly,
                intraday_bars=intraday,
                analyses=tuple(self._analyses.get(symbol, {}).values()),
                macro_regime=self._macro.regime,
                macro_signals=(*self._macro.adverse_signals, *self._macro.shock_signals),
                macro_metrics=self._macro.metrics,
                portfolio_capital_usd=self._portfolio_capital_usd,
                target_weight_percent=self._allocations.get(symbol),
                held_quantity=held_quantity,
            ),
            now=occurred_at,
        )
        self._last_evaluated_at[symbol] = occurred_at
        if evaluation is None:
            return
        if evaluation.transition is not None and not await self._store.save(evaluation):
            return
        await self._publish_assessment(evaluation.assessment)
        if evaluation.transition is not None:
            await self._publish_transition(evaluation.transition)
            await self._publish_alert(evaluation)

    async def _publish_assessment(self, assessment: PatreonCapsAssessment) -> None:
        await self._publisher.publish(
            patreon_caps_assessment_subject(assessment.symbol),
            EventEnvelope(
                event_type=PATREON_CAPS_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="patreon-caps-v1",
                subject=assessment.symbol,
                payload=assessment,
            ),
        )

    async def _publish_transition(self, transition: PatreonCapsTransition) -> None:
        await self._publisher.publish(
            patreon_caps_transition_subject(transition.state, transition.symbol),
            EventEnvelope(
                event_type=PATREON_CAPS_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="patreon-caps-v1",
                subject=transition.symbol,
                payload=transition,
            ),
        )

    async def _publish_alert(self, evaluation: PatreonCapsEvaluation) -> None:
        transition = evaluation.transition
        if transition is None or not transition.source_analysis_ids:
            return
        alert = _local_alert(transition)
        await self._publisher.publish(
            local_alert_subject(alert.severity, alert.symbol),
            EventEnvelope(
                event_type=LOCAL_ALERT_EVENT,
                occurred_at=alert.created_at,
                source="patreon-caps-v1",
                subject=alert.symbol,
                payload=alert,
            ),
        )
        signal = entry_signal_from_alert(alert)
        if signal is not None:
            await publish_entry_signal(self._publisher, signal, source="patreon-caps")


async def run_patreon_caps_process(
    *,
    config_path: Path | None = None,
    ready_path: Path | None = None,
    once: bool = False,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    policy = cast(
        "PatreonCapsPolicy",
        assembly.resolve_strategy(
            EngineSlot.PATREON_CAPS,
            artifact_override=config_path,
        ),
    )
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    portfolio_data = PostgresUniverseClient(database)
    store = PostgresPatreonCapsStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError(
            "PatreonCaps schema is unavailable; apply 20260801120000_patreon_caps.sql"
        )
    universe = (
        fallback_universe(symbols, source="manual-symbols")
        if symbols
        else await portfolio_data.get_universe()
    )
    allocations = await portfolio_data.get_portfolio_allocations()
    allocation_map = {item.symbol: item.weight_percent for item in allocations}
    runtime_symbols = tuple(dict.fromkeys((*universe.symbols, *policy.macro_symbols)))
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        bus = await connect_nats(settings)
        clock = SystemClock()
        bars = await load_market_history(
            settings,
            database,
            engine_id="patreon-caps-v1",
            symbols=runtime_symbols,
            requirements=PATREON_HISTORY_REQUESTS,
            as_of=clock.now(),
        )
        restored = tuple(
            item for item in await store.load_active() if item.rule_version == policy.rule_version
        )
        last_evaluated_at = await store.latest_transition_times(rule_version=policy.rule_version)
        engine = assembly.build_patreon_caps(
            restored_watches=restored,
            strategy_artifact_override=config_path,
        )
        runtime = PatreonCapsRuntime(
            engine=engine,
            publisher=bus,
            store=store,
            portfolio_data=portfolio_data,
            allocations=allocation_map,
            portfolio_capital_usd=policy.portfolio_capital_usd,
            macro_symbols=policy.macro_symbols,
            require_hourly=policy.lesson_enabled,
            last_evaluated_at=last_evaluated_at,
        )
        await runtime.bootstrap(bars, symbols=runtime_symbols)
        subscriptions.extend(await _subscribe_live_analyses(bus, runtime))
        await _hydrate_latest_analyses(bus, runtime, universe.symbols)
        for index, subject in enumerate(
            (
                "marketbot.v1.market.bar.1Min.>",
                "marketbot.v1.market.bar.1Day.>",
                "marketbot.v1.market.bar.1Week.>",
            ),
            start=1,
        ):
            subscriptions.append(
                await bus.subscribe(
                    subject,
                    runtime.handle_market,
                    options=SubscriptionOptions(
                        durable_name=f"marketbot-patreon-caps-market-v1-{index}",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                )
            )
        await runtime.complete_hydration()
        summary: dict[str, object] = {
            **universe_health_details("patreon-caps"),
            "service": "patreon-caps-v1",
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": assembly.spec(EngineSlot.PATREON_CAPS).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.PATREON_CAPS).strategy.version,
            "rule_version": policy.rule_version,
            "mode": "ACTIVE",
            "symbols": len(universe.symbols),
            "universe_symbols": list(universe.symbols),
            "macro_symbols": list(policy.macro_symbols),
            "persistence": "postgresql",
        }
        if ready_path is not None:
            write_ready(ready_path, summary)
        if once:
            return summary
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()
    return None


async def _hydrate_latest_analyses(
    bus: LatestEventReader,
    runtime: AnalysisHandler,
    symbols: tuple[str, ...],
) -> None:
    """Restore only the exact latest analysis inputs required by PatreonCaps."""

    subjects = tuple(
        analysis_result_subject(horizon, symbol)
        for symbol in symbols
        for horizon in PATREON_ANALYSIS_HORIZONS
    )
    envelopes = await asyncio.gather(*(bus.get_last(subject) for subject in subjects))
    for envelope in envelopes:
        if envelope is not None:
            await runtime.handle_analysis(envelope)


async def _subscribe_live_analyses(
    bus: LiveEventSubscriber,
    runtime: AnalysisHandler,
) -> tuple[Subscription, ...]:
    """Listen to new analyses using one lightweight consumer per horizon."""

    subscriptions: list[Subscription] = []
    for horizon in PATREON_ANALYSIS_HORIZONS:
        subscriptions.append(
            await bus.subscribe(
                f"marketbot.v1.analysis.result.{horizon.value}.>",
                runtime.handle_analysis,
                options=SubscriptionOptions(
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
    return tuple(subscriptions)


def _local_alert(transition: PatreonCapsTransition) -> LocalAlert:
    buy_states = {
        PatreonCapsState.CONFIRMED_V,
        PatreonCapsState.CONFIRMED_BASE,
        PatreonCapsState.IMPULSE_RETEST,
    }
    if transition.state in buy_states:
        kind = AlertKind.PATREON_CAPS_BUY
        severity = AlertSeverity.ACTION
    elif transition.state is PatreonCapsState.INVALIDATED:
        kind = AlertKind.PATREON_CAPS_INVALIDATED
        severity = AlertSeverity.WATCH
    else:
        kind = AlertKind.PATREON_CAPS_WATCH
        severity = AlertSeverity.WATCH
    metrics = [
        NamedValue(name="patreon_caps_rule_version", value=transition.rule_version),
        NamedValue(name="patreon_caps_state", value=transition.state.value),
        NamedValue(name="current_price", value=transition.current_price),
        NamedValue(name="zone_low", value=transition.zone_low),
        NamedValue(name="zone_high", value=transition.zone_high),
        NamedValue(name="invalidation", value=transition.invalidation),
        NamedValue(name="macro_regime", value=transition.macro_regime.value),
        NamedValue(name="lesson_score", value=transition.lesson_score),
        NamedValue(name="lesson_gate_passed", value=transition.lesson_gate_passed),
        NamedValue(name="tranche_stage", value=transition.tranche_stage),
        NamedValue(name="suggested_tranche_usd", value=transition.suggested_tranche_usd),
        NamedValue(name="suggested_whole_shares", value=transition.suggested_whole_shares),
    ]
    return LocalAlert(
        symbol=transition.symbol,
        created_at=transition.occurred_at,
        severity=severity,
        title=f"PATREON CAPS {transition.state.value} {transition.symbol}",
        message=(
            f"PatreonCaps analytical score {transition.patreon_score}; "
            f"lesson {transition.lesson_score} "
            f"({'OK' if transition.lesson_gate_passed else 'BLOCK'}); "
            f"zona {transition.zone_low}-{transition.zone_high}; "
            f"invalidación {transition.invalidation}."
        ),
        horizons=(
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
        component_analysis_ids=transition.source_analysis_ids,
        metrics=tuple(metrics),
        score=transition.patreon_score,
        reasons=transition.reasons,
        deduplication_key=(
            f"patreon-caps-alert:{transition.rule_version}:{transition.transition_id}"
        ),
        kind=kind,
        expires_at=transition.expires_at,
    )
