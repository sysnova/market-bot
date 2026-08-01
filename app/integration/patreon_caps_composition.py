"""NATS/PostgreSQL composition for the PatreonCaps shadow engine."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from app.alpaca_market_data import AlpacaEventNormalizer
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
    LocalAlert,
    MarketBar,
    NamedValue,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    Subscription,
    SubscriptionOptions,
    local_alert_subject,
    patreon_caps_assessment_subject,
    patreon_caps_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.patreon_caps_engine import (
    PatreonCapsContext,
    PatreonCapsEngine,
    PatreonCapsEvaluation,
    load_patreon_caps_policy,
)
from app.patreon_caps_engine.macro import classify_macro_regime
from app.persistence import create_database_engine, create_session_factory

from .bar_aggregator import MinuteBarAggregator
from .distributed_composition import (
    HistoryRequest,
    build_rest,
    connect_nats,
    load_history,
    write_ready,
)
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore
from .patreon_caps_store import PostgresPatreonCapsStore
from .postgres_universe import PostgresUniverseClient

PATREON_HISTORY_REQUESTS = (
    HistoryRequest(BarTimeframe.DAY_1, timedelta(days=400), 260),
    HistoryRequest(BarTimeframe.WEEK_1, timedelta(days=365 * 5), 220),
    HistoryRequest(BarTimeframe.HOUR_1, timedelta(days=60), 220),
    HistoryRequest(BarTimeframe.MINUTE_15, timedelta(days=14), 160),
    HistoryRequest(BarTimeframe.MINUTE_1, timedelta(days=7), 500),
)


class PatreonCapsStore(Protocol):
    async def save(self, evaluation: PatreonCapsEvaluation) -> bool: ...


class PortfolioData(Protocol):
    async def get_holding_quantity(self, symbol: str) -> Decimal: ...


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
        if result.symbol not in self._symbols or result.horizon not in {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }:
            return
        values = self._analyses.setdefault(result.symbol, {})
        previous = values.get(result.horizon)
        if previous is not None and result.as_of < previous.as_of:
            return
        values[result.horizon] = result
        await self._evaluate(result.symbol)

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
        self._bars.add(bar)
        if bar.symbol in self._macro_symbols:
            if bar.timeframe is BarTimeframe.DAY_1:
                self._refresh_macro()
            return
        if bar.symbol not in self._symbols:
            return
        if bar.timeframe is BarTimeframe.MINUTE_1:
            emitted = self._aggregator.add(bar)
            for aggregate in emitted:
                self._bars.add(aggregate)
                await self._evaluate(bar.symbol)
        elif bar.timeframe in {
            BarTimeframe.DAY_1,
            BarTimeframe.WEEK_1,
            BarTimeframe.HOUR_1,
            BarTimeframe.MINUTE_15,
        }:
            await self._evaluate(bar.symbol)

    def _refresh_macro(self) -> None:
        series = {
            symbol: self._bars.history(
                symbol, BarTimeframe.DAY_1, limit=260, final_only=True
            )
            for symbol in self._macro_symbols
        }
        self._macro = classify_macro_regime(series)

    async def _evaluate(self, symbol: str) -> None:
        daily = self._bars.history(symbol, BarTimeframe.DAY_1, limit=260, final_only=True)
        weekly = self._bars.history(symbol, BarTimeframe.WEEK_1, limit=220, final_only=True)
        intraday = self._bars.history(
            symbol, BarTimeframe.MINUTE_15, limit=160, final_only=True
        )
        hourly = self._bars.history(
            symbol, BarTimeframe.HOUR_1, limit=220, final_only=True
        )
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
                source="patreon-caps-v1-shadow",
                subject=alert.symbol,
                payload=alert,
            ),
        )


async def run_patreon_caps_process(
    *,
    config_path: Path = Path("configs/rules/patreon_caps/1.1.0.yaml"),
    ready_path: Path | None = None,
) -> None:
    settings = AppSettings()
    policy = load_patreon_caps_policy(config_path)
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
    universe = await portfolio_data.get_universe()
    allocations = await portfolio_data.get_portfolio_allocations()
    allocation_map = {item.symbol: item.weight_percent for item in allocations}
    symbols = tuple(dict.fromkeys((*universe.symbols, *policy.macro_symbols)))
    bus: NatsJetStreamEventBus | None = None
    rest = None
    subscriptions: list[Subscription] = []
    try:
        bus = await connect_nats(settings)
        rest = build_rest(settings)
        clock = SystemClock()
        bars = await load_history(
            rest,
            AlpacaEventNormalizer(feed=settings.alpaca_data_feed),
            symbols,
            PATREON_HISTORY_REQUESTS,
            as_of=clock.now(),
            batch_size=settings.alpaca_rest_batch_size,
        )
        restored = tuple(
            item
            for item in await store.load_active()
            if item.rule_version == policy.rule_version
        )
        engine = PatreonCapsEngine(policy, restored_watches=restored)
        runtime = PatreonCapsRuntime(
            engine=engine,
            publisher=bus,
            store=store,
            portfolio_data=portfolio_data,
            allocations=allocation_map,
            portfolio_capital_usd=policy.portfolio_capital_usd,
            macro_symbols=policy.macro_symbols,
            require_hourly=policy.lesson_enabled,
        )
        await runtime.bootstrap(bars, symbols=symbols)
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.analysis.result.>",
                runtime.handle_analysis,
                options=SubscriptionOptions(
                    durable_name="marketbot-patreon-caps-analysis-v1",
                    replay_all=True,
                    ack_wait_seconds=60,
                ),
            )
        )
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
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "patreon-caps-v1",
                    "rule_version": policy.rule_version,
                    "mode": "SHADOW",
                    "symbols": len(universe.symbols),
                    "macro_symbols": list(policy.macro_symbols),
                    "persistence": "postgresql",
                },
            )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if rest is not None:
            await rest.close()
        if bus is not None:
            await bus.close()
        await database.dispose()


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
            f"PatreonCaps shadow score {transition.patreon_score}; "
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
