"""Watchlist-only runtime for SwingTrade Fibonacci maturity ST1-ST4."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.common.clock import Clock, SystemClock
from app.common.market_session import is_regular_session
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    GERI_ASSESSMENT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    SWING_TRADE_ASSESSMENT_EVENT,
    SWING_TRADE_TRANSITION_EVENT,
    AnalysisHorizon,
    BarTimeframe,
    EntrySignal,
    EntrySignalFamily,
    EventEnvelope,
    GeriAssessment,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    SwingTradeAssessment,
    SwingTradeTransition,
    entry_signal_subject,
    swing_trade_assessment_subject,
    swing_trade_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.swing_trade_engine import SwingTradeContext

from .bar_aggregator import MinuteBarAggregator
from .distributed_composition import HistoryRequest, connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .postgres_universe import PostgresUniverseClient

SWING_TRADE_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.DAY_1,
        lookback=timedelta(days=190),
        max_bars_per_symbol=120,
    ),
    HistoryRequest(
        timeframe=BarTimeframe.MINUTE_15,
        lookback=timedelta(days=7),
        max_bars_per_symbol=180,
    ),
)


class SwingTradeEnginePort(Protocol):
    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment: ...


class Publisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class SwingTradeRuntime:
    """Evaluate exactly once per completed 15-minute Watchlist bar."""

    def __init__(
        self, *, engine: SwingTradeEnginePort, publisher: Publisher, clock: Clock | None = None
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._clock = clock or SystemClock()
        self._bars = MarketBarStore(capacity_per_series=160)
        self._minute = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,))
        self._symbols: set[str] = set()
        self._geri: dict[str, GeriAssessment] = {}
        self._support: dict[str, SupportAssessment] = {}
        self._latest: dict[str, SwingTradeAssessment] = {}
        self._evaluated: set[tuple[str, datetime]] = set()
        self._rejected_evaluations: dict[str, int] = {}

    def diagnostics(self) -> dict[str, int]:
        return dict(self._rejected_evaluations)

    async def restore_assessment(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SWING_TRADE_ASSESSMENT_EVENT:
            return
        item = _payload(envelope, SwingTradeAssessment)
        previous = self._latest.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._latest[item.symbol] = item

    async def restore_geri(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != GERI_ASSESSMENT_EVENT:
            return
        item = _payload(envelope, GeriAssessment)
        previous = self._geri.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._geri[item.symbol] = item

    async def restore_support(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        item = _payload(envelope, SupportAssessment)
        previous = self._support.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._support[item.symbol] = item

    async def bootstrap(self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]) -> int:
        self._symbols = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        latest_fifteen: dict[str, MarketBar] = {}
        for bar in sorted(bars, key=lambda value: (value.timestamp, value.symbol)):
            if bar.symbol not in self._symbols or not bar.is_final:
                continue
            if bar.timeframe is BarTimeframe.DAY_1:
                self._bars.add(bar)
            elif bar.timeframe is BarTimeframe.MINUTE_15 and is_regular_session(bar.timestamp):
                self._bars.add(bar)
                latest_fifteen[bar.symbol] = bar
        published = 0
        for bar in latest_fifteen.values():
            published += int(await self._accept_fifteen(bar))
        return published

    async def handle_market(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = _payload(envelope, MarketBar)
        if bar.symbol not in self._symbols or not bar.is_final:
            return
        if bar.timeframe is BarTimeframe.DAY_1:
            self._bars.add(bar)
            return
        if bar.timeframe is BarTimeframe.MINUTE_15:
            if is_regular_session(bar.timestamp):
                await self._accept_fifteen(bar)
            return
        if bar.timeframe is BarTimeframe.MINUTE_1:
            for fifteen in self._minute.add(bar):
                await self._accept_fifteen(fifteen)

    async def _accept_fifteen(self, bar: MarketBar) -> bool:
        key = (bar.symbol, bar.timestamp)
        if key in self._evaluated:
            return False
        self._evaluated.add(key)
        self._bars.add(bar)
        daily = self._bars.history(bar.symbol, BarTimeframe.DAY_1, limit=120, final_only=True)
        try:
            assessment = self._engine.analyze(
                SwingTradeContext(
                    symbol=bar.symbol,
                    as_of=bar.timestamp + timedelta(minutes=15),
                    current_price=bar.close,
                    daily_bars=daily,
                    geri=self._geri.get(bar.symbol),
                    support=self._support.get(bar.symbol),
                    confirmation_bars=self._bars.history(
                        bar.symbol,
                        BarTimeframe.MINUTE_15,
                        limit=160,
                        final_only=True,
                    ),
                    current_price_at=bar.timestamp + timedelta(minutes=15),
                )
            ).model_copy(update={"assessed_at": self._clock.now()})
        except ValueError as error:
            reason = str(error) or type(error).__name__
            self._rejected_evaluations[reason] = self._rejected_evaluations.get(reason, 0) + 1
            return False
        previous = self._latest.get(bar.symbol)
        if previous is not None and not _material_change(previous, assessment):
            return False
        self._latest[bar.symbol] = assessment
        await self._publish(assessment, previous)
        return True

    async def _publish(
        self, item: SwingTradeAssessment, previous: SwingTradeAssessment | None
    ) -> None:
        occurred_at = item.assessed_at or item.occurred_at
        await self._publisher.publish(
            swing_trade_assessment_subject(item.symbol),
            EventEnvelope(
                event_type=SWING_TRADE_ASSESSMENT_EVENT,
                occurred_at=occurred_at,
                source="swing-trade-v1",
                subject=item.symbol,
                payload=item,
            ),
        )
        previous_maturity = previous.maturity if previous is not None else None
        if item.maturity is None and previous_maturity is None:
            return
        transition = SwingTradeTransition(
            assessment_id=item.assessment_id,
            symbol=item.symbol,
            occurred_at=occurred_at,
            engine_version=item.engine_version,
            strategy_version=item.strategy_version,
            previous_maturity=previous_maturity,
            maturity=item.maturity,
            current_price=item.current_price,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            invalidation=item.invalidation,
            primary_target=item.primary_target,
            reward_risk=item.reward_risk,
            eligible=item.eligible,
            reasons=item.reasons,
            context_hash=item.context_hash,
        )
        await self._publisher.publish(
            swing_trade_transition_subject(item.maturity, item.symbol),
            EventEnvelope(
                event_type=SWING_TRADE_TRANSITION_EVENT,
                occurred_at=occurred_at,
                source="swing-trade-v1",
                subject=item.symbol,
                payload=transition,
            ),
        )
        signal_basis = item if item.maturity is not None else previous
        if signal_basis is None:
            raise AssertionError("SwingTrade thesis loss requires a previous assessment")
        setup_id = str(
            next(metric.value for metric in signal_basis.metrics if metric.name == "setup_id")
        )
        signal = EntrySignal(
            family=EntrySignalFamily.SWING_TRADE,
            swing_trade_maturity=item.maturity,
            symbol=item.symbol,
            created_at=occurred_at,
            setup_id=setup_id,
            entry_price=item.current_price,
            horizons=(AnalysisHorizon.SWING,),
            zone_low=signal_basis.zone_low,
            zone_high=signal_basis.zone_high,
            invalidation=signal_basis.invalidation,
            targets=(signal_basis.primary_target, signal_basis.extended_target),
            policy_id="swing-trade",
            policy_version=signal_basis.strategy_version,
            reasons=item.reasons,
            source_event_ids=(item.assessment_id, transition.transition_id),
        )
        await self._publisher.publish(
            entry_signal_subject(signal.family, signal.symbol),
            EventEnvelope(
                event_type=ENTRY_SIGNAL_EVENT,
                occurred_at=occurred_at,
                source="swing-trade-v1",
                subject=item.symbol,
                payload=signal,
            ),
        )


def _payload[ModelT: BaseModel](envelope: EventEnvelope, model: type[ModelT]) -> ModelT:
    return (
        envelope.payload
        if isinstance(envelope.payload, model)
        else model.model_validate(envelope.payload, strict=False)
    )


def _material_change(previous: SwingTradeAssessment, current: SwingTradeAssessment) -> bool:
    return (
        previous.maturity is not current.maturity
        or previous.impulse_low_at != current.impulse_low_at
        or previous.impulse_high_at != current.impulse_high_at
        or previous.zone_low != current.zone_low
        or previous.zone_high != current.zone_high
        or previous.invalidation != current.invalidation
        or previous.primary_target != current.primary_target
        or previous.geri_confluence is not current.geri_confluence
        or _metric(previous, "setup_id") != _metric(current, "setup_id")
        or _metric(previous, "geri_zone_source") != _metric(current, "geri_zone_source")
        or _metric(previous, "support_assessment_id") != _metric(current, "support_assessment_id")
    )


def _metric(item: SwingTradeAssessment, name: str) -> object | None:
    return next((metric.value for metric in item.metrics if metric.name == name), None)


async def run_swing_trade_process(
    *, ready_path: Path | None = None, once: bool = False, symbols: tuple[str, ...] | None = None
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        if symbols is None:
            universe = await PostgresUniverseClient(database).get_watchlist()
            selected, universe_source = universe.symbols, universe.source
        else:
            selected = tuple(
                dict.fromkeys(item.strip().upper() for item in symbols if item.strip())
            )
            if not selected:
                raise ValueError("SwingTrade requires at least one Watchlist symbol")
            universe_source = "operator-watchlist-override"
        bus = await connect_nats(settings)
        runtime = SwingTradeRuntime(engine=assembly.build_swing_trade(), publisher=bus)
        for subject, handler, durable in (
            (
                "marketbot.v1.swing-trade.assessment.>",
                runtime.restore_assessment,
                "marketbot-swing-trade-restore-v1",
            ),
            (
                "marketbot.v1.4hgeri.assessment.>",
                runtime.restore_geri,
                "marketbot-swing-trade-geri-v1",
            ),
            (
                "marketbot.v1.support-confirmation.assessment.>",
                runtime.restore_support,
                "marketbot-swing-trade-support-v1",
            ),
        ):
            subscription = await bus.subscribe(
                subject,
                handler,
                options=SubscriptionOptions(
                    durable_name=durable, replay_latest_per_subject=True, ack_wait_seconds=60
                ),
            )
            subscriptions.append(subscription)
            await bus.wait_until_caught_up(subscription, timeout_seconds=60)
        bars = await load_market_history(
            settings,
            database,
            engine_id="swing-trade-v1",
            symbols=selected,
            requirements=SWING_TRADE_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
        )
        published = await runtime.bootstrap(bars, symbols=selected)
        summary: dict[str, object] = {
            "service": "swing-trade-v1",
            "engine_version": assembly.spec(EngineSlot.SWING_TRADE).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.SWING_TRADE).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "symbols": len(selected),
            "universe_source": universe_source,
            "historical_bars": len(bars),
            "assessments_published": published,
            "rejected_evaluations": runtime.diagnostics(),
            "evaluation_bar": "15Min_FINAL_RTH",
            "places_orders": False,
        }
        if once:
            return summary
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.market.bar.1Min.>",
                runtime.handle_market,
                options=SubscriptionOptions(
                    durable_name="marketbot-swing-trade-market-v1",
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
        if ready_path is not None:
            write_ready(ready_path, summary)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()
    return None
