"""Independent runtime for four-hour Swing channel observations."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from app.common.clock import Clock, SystemClock
from app.common.market_session import is_regular_session
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    SWING_CHANNEL_ASSESSMENT_EVENT,
    SWING_CHANNEL_TRANSITION_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryMaturityLevel,
    EntryOpportunityEvent,
    EventEnvelope,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    SwingChannelAssessment,
    SwingChannelTransition,
    swing_channel_assessment_subject,
    swing_channel_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.swing_channel_4h_engine import SwingChannel4HContext

from .bar_aggregator import MinuteBarAggregator, RegularSessionFourHourAggregator
from .distributed_composition import HistoryRequest, connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .postgres_universe import PostgresUniverseClient
from .universe_policy import universe_health_details

SWING_CHANNEL_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.MINUTE_15,
        lookback=timedelta(days=35),
        max_bars_per_symbol=600,
    ),
)


class SwingChannelEngine(Protocol):
    def analyze(self, context: SwingChannel4HContext) -> SwingChannelAssessment: ...


class SwingChannelPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class SwingChannel4HRuntime:
    """Aggregate RTH bars, compare daily Swing, and publish only independent events."""

    def __init__(
        self,
        *,
        engine: SwingChannelEngine,
        publisher: SwingChannelPublisher,
        clock: Clock | None = None,
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._clock = clock or SystemClock()
        self._bars = MarketBarStore(capacity_per_series=80)
        self._minute = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,))
        self._four_hour = RegularSessionFourHourAggregator()
        self._symbols: set[str] = set()
        self._prices: dict[str, Decimal] = {}
        self._daily_swing: dict[str, AnalysisResult] = {}
        self._existing_maturity: dict[str, EntryMaturityLevel] = {}
        self._opportunity_at: dict[str, datetime] = {}
        self._latest: dict[str, SwingChannelAssessment] = {}

    async def restore_assessment(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SWING_CHANNEL_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, SwingChannelAssessment)
            else SwingChannelAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._latest.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._latest[item.symbol] = item

    async def bootstrap(
        self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]
    ) -> int:
        self._symbols = {item.strip().upper() for item in symbols if item.strip()}
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            if bar.symbol not in self._symbols or not bar.is_final:
                continue
            if bar.timeframe is BarTimeframe.HOUR_4:
                self._bars.add(bar)
                self._prices[bar.symbol] = bar.close
            elif bar.timeframe is BarTimeframe.MINUTE_15 and is_regular_session(bar.timestamp):
                self._prices[bar.symbol] = bar.close
                for aggregated in self._four_hour.add(bar):
                    self._bars.add(aggregated)
        published = 0
        for symbol in sorted(self._symbols):
            published += int(await self.evaluate(symbol))
        return published

    async def handle_market(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = (
            envelope.payload
            if isinstance(envelope.payload, MarketBar)
            else MarketBar.model_validate(envelope.payload, strict=False)
        )
        if bar.symbol not in self._symbols or not bar.is_final:
            return
        if bar.timeframe is BarTimeframe.HOUR_4:
            self._bars.add(bar)
            self._prices[bar.symbol] = bar.close
            await self.evaluate(bar.symbol)
            return
        if bar.timeframe is BarTimeframe.MINUTE_15:
            if not is_regular_session(bar.timestamp):
                return
            await self._accept_fifteen(bar)
            return
        if bar.timeframe is not BarTimeframe.MINUTE_1:
            return
        aggregated = self._minute.add(bar)
        if is_regular_session(bar.timestamp):
            self._prices[bar.symbol] = bar.close
            for fifteen in aggregated:
                await self._accept_fifteen(fifteen, evaluate=False)
            await self.evaluate(bar.symbol, current_price=bar.close)
            return
        for fifteen in aggregated:
            await self._accept_fifteen(fifteen)

    async def handle_analysis(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        if result.horizon is not AnalysisHorizon.SWING or result.symbol not in self._symbols:
            return
        previous = self._daily_swing.get(result.symbol)
        if previous is not None and result.as_of < previous.as_of:
            return
        self._daily_swing[result.symbol] = result
        await self.evaluate(result.symbol)

    async def handle_opportunity(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
            return
        event = (
            envelope.payload
            if isinstance(envelope.payload, EntryOpportunityEvent)
            else EntryOpportunityEvent.model_validate(envelope.payload, strict=False)
        )
        opportunity = event.opportunity
        if opportunity.symbol not in self._symbols:
            return
        previous_at = self._opportunity_at.get(opportunity.symbol)
        if previous_at is not None and event.occurred_at < previous_at:
            return
        self._opportunity_at[opportunity.symbol] = event.occurred_at
        self._existing_maturity[opportunity.symbol] = opportunity.current_maturity
        await self.evaluate(opportunity.symbol)

    async def evaluate(self, symbol: str, *, current_price: Decimal | None = None) -> bool:
        normalized = symbol.strip().upper()
        bars = self._bars.history(
            normalized, BarTimeframe.HOUR_4, limit=60, final_only=True
        )
        value = current_price if current_price is not None else self._prices.get(normalized)
        if value is None or not bars:
            return False
        try:
            assessment = self._engine.analyze(
                SwingChannel4HContext(
                    symbol=normalized,
                    bars=bars,
                    current_price=value,
                    daily_swing=self._daily_swing.get(normalized),
                    existing_maturity=self._existing_maturity.get(normalized),
                )
            )
        except ValueError:
            return False
        assessment = assessment.model_copy(update={"assessed_at": self._clock.now()})
        previous = self._latest.get(normalized)
        if previous is not None and _same_observation(previous, assessment):
            return False
        self._latest[normalized] = assessment
        await self._publish_assessment(assessment)
        if previous is None or previous.maturity is not assessment.maturity:
            await self._publish_transition(assessment, previous)
        return True

    async def _accept_fifteen(self, bar: MarketBar, *, evaluate: bool = True) -> None:
        self._prices[bar.symbol] = bar.close
        for aggregated in self._four_hour.add(bar):
            self._bars.add(aggregated)
        if evaluate:
            await self.evaluate(bar.symbol, current_price=bar.close)

    async def _publish_assessment(self, item: SwingChannelAssessment) -> None:
        await self._publisher.publish(
            swing_channel_assessment_subject(item.symbol),
            EventEnvelope(
                event_type=SWING_CHANNEL_ASSESSMENT_EVENT,
                occurred_at=item.assessed_at or item.occurred_at,
                source="swing-channel-4h-v1",
                subject=item.symbol,
                payload=item,
            ),
        )

    async def _publish_transition(
        self,
        item: SwingChannelAssessment,
        previous: SwingChannelAssessment | None,
    ) -> None:
        transition = SwingChannelTransition(
            assessment_id=item.assessment_id,
            symbol=item.symbol,
            occurred_at=item.assessed_at or item.occurred_at,
            engine_version=item.engine_version,
            previous_maturity=previous.maturity if previous is not None else None,
            maturity=item.maturity,
            current_price=item.current_price,
            support=item.support,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            invalidation=item.invalidation,
            reasons=item.reasons,
            context_hash=item.context_hash,
        )
        await self._publisher.publish(
            swing_channel_transition_subject(transition.maturity, transition.symbol),
            EventEnvelope(
                event_type=SWING_CHANNEL_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="swing-channel-4h-v1",
                subject=transition.symbol,
                payload=transition,
            ),
        )


def _same_observation(
    previous: SwingChannelAssessment, current: SwingChannelAssessment
) -> bool:
    return (
        previous.maturity is current.maturity
        and previous.support == current.support
        and previous.zone_low == current.zone_low
        and previous.zone_high == current.zone_high
        and previous.daily_swing_aligned is current.daily_swing_aligned
        and previous.existing_maturity_aligned is current.existing_maturity_aligned
    )


async def run_swing_channel_4h_process(
    *,
    ready_path: Path | None = None,
    once: bool = False,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, object] | None:
    """Bootstrap from PostgreSQL and publish a separate shadow maturity stream."""

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
            universe = await PostgresUniverseClient(database).get_universe()
            selected = universe.symbols
            universe_source = universe.source
        else:
            selected = tuple(
                dict.fromkeys(item.strip().upper() for item in symbols if item.strip())
            )
            if not selected:
                raise ValueError("Swing Channel 4H requires at least one symbol")
            universe_source = "operator-override"
        bus = await connect_nats(settings)
        runtime = SwingChannel4HRuntime(
            engine=assembly.build_swing_channel_4h(), publisher=bus
        )
        replay_specs = (
            (
                "marketbot.v1.swing-channel-4h.assessment.>",
                runtime.restore_assessment,
                "marketbot-swing-channel-4h-restore-v1",
            ),
            (
                "marketbot.v1.analysis.result.SWING.>",
                runtime.handle_analysis,
                "marketbot-swing-channel-4h-swing-v1",
            ),
            (
                "marketbot.v1.entry-opportunity.transition.>",
                runtime.handle_opportunity,
                "marketbot-swing-channel-4h-opportunity-v1",
            ),
        )
        for subject, handler, durable in replay_specs:
            subscription = await bus.subscribe(
                subject,
                handler,
                options=SubscriptionOptions(
                    durable_name=durable,
                    replay_latest_per_subject=True,
                    ack_wait_seconds=60,
                ),
            )
            subscriptions.append(subscription)
            await bus.wait_until_caught_up(subscription, timeout_seconds=60)
        bars = await load_market_history(
            settings,
            database,
            engine_id="swing-channel-4h-v1",
            symbols=selected,
            requirements=SWING_CHANNEL_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
        )
        published = await runtime.bootstrap(bars, symbols=selected)
        historical_bars = len(bars)
        del bars
        summary: dict[str, object] = {
            **universe_health_details("swing-channel-4h"),
            "service": "swing-channel-4h-v1",
            "engine_version": assembly.spec(EngineSlot.SWING_CHANNEL_4H).implementation,
            "engine_strategy_version": assembly.spec(
                EngineSlot.SWING_CHANNEL_4H
            ).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "SHADOW",
            "symbols": len(selected),
            "universe_source": universe_source,
            "historical_bars": historical_bars,
            "assessments_published": published,
            "bar_source": "15Min_RTH_aggregated_09:30_ET",
            "feeds_core_opportunities": False,
            "places_orders": False,
        }
        if once:
            return summary
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.market.bar.1Min.>",
                runtime.handle_market,
                options=SubscriptionOptions(
                    durable_name="marketbot-swing-channel-4h-market-v1",
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
