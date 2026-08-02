"""Local PostgreSQL/Alpaca/NATS composition for Elliott Wave shadow analysis."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.alpaca_market_data import AlpacaEventNormalizer
from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ELLIOTT_WAVE_ASSESSMENT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    WaveAssessment,
    elliott_wave_assessment_subject,
)
from app.elliott_wave_engine import ElliottWaveEngine, WaveContext
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine

from .distributed_composition import (
    HistoryRequest,
    build_rest,
    connect_nats,
    load_history,
    write_ready,
)
from .market_bar_store import MarketBarStore
from .postgres_universe import PostgresUniverseClient, UniverseSnapshot

ELLIOTT_HISTORY_REQUESTS = (
    HistoryRequest(BarTimeframe.DAY_1, timedelta(days=600), 400),
    HistoryRequest(BarTimeframe.HOUR_1, timedelta(days=90), 500),
)


class HoldingsProvider(Protocol):
    async def get_holdings(self) -> UniverseSnapshot: ...


class WavePublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


async def load_held_symbols(provider: HoldingsProvider) -> UniverseSnapshot:
    """Resolve only authoritative active positions; never merge the watchlist."""

    snapshot = await provider.get_holdings()
    if not snapshot.symbols:
        raise RuntimeError("Elliott Wave requires at least one positive local holding")
    return snapshot


class ElliottWaveRuntime:
    def __init__(self, *, engine: ElliottWaveEngine, publisher: WavePublisher) -> None:
        self._engine = engine
        self._publisher = publisher
        self._bars = MarketBarStore(capacity_per_series=600)
        self._symbols: set[str] = set()
        self._last_evaluated_at: dict[str, datetime] = {}

    async def bootstrap(
        self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]
    ) -> int:
        self._symbols = {symbol.strip().upper() for symbol in symbols}
        for bar in bars:
            if bar.symbol in self._symbols:
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
            or bar.symbol not in self._symbols
            or bar.timeframe not in {BarTimeframe.DAY_1, BarTimeframe.HOUR_1}
        ):
            return
        self._bars.add(bar)
        if bar.timeframe is BarTimeframe.DAY_1:
            await self._evaluate(bar.symbol)

    async def _evaluate(self, symbol: str) -> bool:
        daily = self._bars.history(symbol, BarTimeframe.DAY_1, limit=400, final_only=True)
        if len(daily) < 60:
            return False
        hourly = self._bars.history(symbol, BarTimeframe.HOUR_1, limit=500, final_only=True)
        latest = daily[-1].timestamp
        if self._last_evaluated_at.get(symbol) == latest:
            return False
        assessment = self._engine.evaluate(
            WaveContext(symbol=symbol, daily_bars=daily, hourly_bars=hourly)
        )
        await self._publish(assessment)
        self._last_evaluated_at[symbol] = latest
        return True

    async def _publish(self, assessment: WaveAssessment) -> None:
        await self._publisher.publish(
            elliott_wave_assessment_subject(assessment.symbol),
            EventEnvelope(
                event_type=ELLIOTT_WAVE_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="elliott-wave-v0",
                subject=assessment.symbol,
                payload=assessment,
            ),
        )


async def run_elliott_wave_process(
    *, ready_path: Path | None = None, once: bool = False
) -> dict[str, object] | None:
    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    provider = PostgresUniverseClient(database)
    bus: NatsJetStreamEventBus | None = None
    rest = None
    subscriptions: list[Subscription] = []
    try:
        holdings = await load_held_symbols(provider)
        bus = await connect_nats(settings)
        rest = build_rest(settings)
        bars = await load_history(
            rest,
            AlpacaEventNormalizer(feed=settings.alpaca_data_feed),
            holdings.symbols,
            ELLIOTT_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
            batch_size=settings.alpaca_rest_batch_size,
        )
        runtime = ElliottWaveRuntime(engine=ElliottWaveEngine(), publisher=bus)
        published = await runtime.bootstrap(bars, symbols=holdings.symbols)
        summary: dict[str, object] = {
            "service": "elliott-wave-v0",
            "engine_version": ElliottWaveEngine.engine_version,
            "mode": "SHADOW",
            "universe": "positive-holdings-only",
            "universe_source": holdings.source,
            "symbols": list(holdings.symbols),
            "assessments_published": published,
            "persistence": "nats-jetstream-15d",
        }
        if once:
            return summary
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.market.bar.1Day.>",
                runtime.handle_market,
                options=SubscriptionOptions(
                    durable_name="marketbot-elliott-wave-day-v0",
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
        if rest is not None:
            await rest.close()
        if bus is not None:
            await bus.close()
        await database.dispose()
    return None
