"""Watchlist-and-holdings Volume Structure composition over local infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    analysis_result_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.volume_structure_engine import VolumeStructureContext

from .distributed_composition import HistoryRequest, connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .postgres_universe import PostgresUniverseClient, UniverseSnapshot, fallback_universe
from .universe_policy import universe_health_details

VOLUME_STRUCTURE_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.WEEK_1,
        lookback=timedelta(days=365 * 8),
        max_bars_per_symbol=420,
    ),
)


class VolumeStructurePublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class VolumeStructureEnginePort(Protocol):
    def evaluate(self, context: VolumeStructureContext) -> AnalysisResult: ...


class VolumeStructureRuntime:
    def __init__(
        self,
        *,
        engine: VolumeStructureEnginePort,
        publisher: VolumeStructurePublisher,
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._bars = MarketBarStore(capacity_per_series=450)
        self._symbols: set[str] = set()
        self._latest: dict[str, AnalysisResult] = {}

    async def restore_result(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        if result.horizon is not AnalysisHorizon.VOLUME_STRUCTURE:
            return
        previous = self._latest.get(result.symbol)
        if previous is None or result.as_of >= previous.as_of:
            self._latest[result.symbol] = result

    async def bootstrap(self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]) -> int:
        self._symbols = {symbol.strip().upper() for symbol in symbols}
        for bar in bars:
            if bar.symbol in self._symbols and bar.timeframe is BarTimeframe.WEEK_1:
                self._bars.add(bar)
        published = 0
        for symbol in sorted(self._symbols):
            if await self._evaluate(symbol):
                published += 1
        return published

    async def handle_market(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = (
            envelope.payload
            if isinstance(envelope.payload, MarketBar)
            else MarketBar.model_validate(envelope.payload, strict=False)
        )
        if (
            not bar.is_final
            or bar.timeframe is not BarTimeframe.WEEK_1
            or bar.symbol not in self._symbols
        ):
            return
        self._bars.add(bar)
        await self._evaluate(bar.symbol)

    async def _evaluate(self, symbol: str) -> bool:
        weekly = self._bars.history(
            symbol, BarTimeframe.WEEK_1, limit=420, final_only=True
        )
        if len(weekly) < 12:
            return False
        result = self._engine.evaluate(
            VolumeStructureContext(
                symbol=symbol,
                weekly_bars=weekly,
                previous_result=self._latest.get(symbol),
            )
        )
        previous = self._latest.get(symbol)
        if previous is not None and _same_observation(previous, result):
            return False
        self._latest[symbol] = result
        await self._publisher.publish(
            analysis_result_subject(result.horizon, result.symbol),
            EventEnvelope(
                event_type=ANALYSIS_RESULT_EVENT,
                occurred_at=result.as_of,
                source="volume-structure-v1",
                subject=result.symbol,
                payload=result,
            ),
        )
        return True


def _same_observation(previous: AnalysisResult, current: AnalysisResult) -> bool:
    return (
        previous.context_hash == current.context_hash
        and previous.engine_version == current.engine_version
        and previous.verdict is current.verdict
        and previous.direction is current.direction
        and previous.score == current.score
        and previous.metrics == current.metrics
    )


async def run_volume_structure_process(
    *,
    ready_path: Path | None = None,
    once: bool = False,
    symbols: str | None = None,
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    provider = PostgresUniverseClient(database)
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        requested = tuple(
            dict.fromkeys(
                item.strip().upper()
                for item in (symbols or "").split(",")
                if item.strip()
            )
        )
        universe: UniverseSnapshot = (
            fallback_universe(requested, source="operator-symbols")
            if requested
            else await provider.get_universe()
        )
        bus = await connect_nats(settings)
        runtime = VolumeStructureRuntime(
            engine=assembly.build_volume_structure(),
            publisher=bus,
        )
        restore = await bus.subscribe(
            "marketbot.v1.analysis.result.VOLUME_STRUCTURE.>",
            runtime.restore_result,
            options=SubscriptionOptions(
                replay_latest_per_subject=True,
                ack_wait_seconds=60,
            ),
        )
        subscriptions.append(restore)
        await bus.wait_until_caught_up(restore, timeout_seconds=60)
        bars = await load_market_history(
            settings,
            database,
            engine_id="volume-structure-v1",
            symbols=universe.symbols,
            requirements=VOLUME_STRUCTURE_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
        )
        published = await runtime.bootstrap(bars, symbols=universe.symbols)
        summary: dict[str, object] = {
            **universe_health_details("volume-structure"),
            "service": "volume-structure-v1",
            "engine_version": assembly.spec(EngineSlot.VOLUME_STRUCTURE).implementation,
            "engine_strategy_version": assembly.spec(
                EngineSlot.VOLUME_STRUCTURE
            ).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "ACTIVE",
            "universe": "watchlist-plus-positive-holdings",
            "universe_source": universe.source,
            "symbols": list(universe.symbols),
            "assessments_published": published,
            "persistence": "nats-jetstream-7d",
            "execution_enabled": False,
        }
        if once:
            return summary
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.market.bar.1Week.>",
                runtime.handle_market,
                options=SubscriptionOptions(
                    durable_name="marketbot-volume-structure-v1-weekly",
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
