"""Production composition for the local, analysis-only MarketBot service."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.typing import FilteringBoundLogger

from app.alert_engine import AlertDispatcher, AlertEngine, ConsoleAlertSink, NdjsonAlertSink
from app.alpaca_market_data import build_alpaca_market_data_engine
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.entry_watcher import EntryWatcherPolicy, EntryWatcherV2, EntryWatcherV3
from app.event_bus import InMemoryEventBus, NatsJetStreamEventBus
from app.intraday_engine import IntradayEngineV2, IntradayEngineV3
from app.long_term_engine import LongTermEngineV2
from app.persistence import create_database_engine, create_session_factory
from app.swing_engine import SwingEngineV2, SwingEngineV3

from .alert_publisher import AlertEventPublisher
from .analysis_runtime import AnalysisRuntime
from .entry_watch_store import PostgresEntryWatchStore
from .event_fanout import EventFanoutPublisher, EventPublisher
from .live_analysis_service import LiveAnalysisService
from .market_bar_store import MarketBarStore
from .postgres_universe import (
    PostgresUniverseClient,
    fallback_universe,
)

_NEW_YORK = ZoneInfo("America/New_York")


async def run_live_analysis(
    *,
    once: bool,
    runtime_root: Path,
    bell: bool,
    mirror_to_nats: bool,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Run read-only market analysis; no execution adapter is composed here."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("live-analysis")
    clock = SystemClock()
    http_client = httpx.AsyncClient()
    local_bus = InMemoryEventBus(
        retain_history=False,
        deduplicate=False,
        synchronous_delivery=True,
    )
    entry_watch_database: AsyncEngine | None = None
    entry_watcher: EntryWatcherV2 | None = None
    if settings.entry_watcher_enabled:
        try:
            entry_watch_database = create_database_engine(
                settings.database_url.get_secret_value(),
                require_ssl=settings.environment is Environment.PRODUCTION,
            )
            entry_watch_store = PostgresEntryWatchStore(
                create_session_factory(entry_watch_database)
            )
            if await entry_watch_store.is_ready():
                watcher_type = (
                    EntryWatcherV3
                    if settings.entry_confirmation_rule_version == "3.0.0"
                    else EntryWatcherV2
                )
                entry_watcher = watcher_type(
                    store=entry_watch_store,
                    policy=EntryWatcherPolicy(
                        ttl=timedelta(days=settings.entry_watch_ttl_days)
                    ),
                )
            else:
                await logger.awarning(
                    "entry_watcher_schema_unavailable",
                    migration="20260726180000_entry_watches.sql",
                )
        except Exception as error:
            await logger.awarning(
                "entry_watcher_postgres_unavailable",
                error_type=type(error).__name__,
            )
            if entry_watch_database is not None:
                await entry_watch_database.dispose()
                entry_watch_database = None
    nats_bus: NatsJetStreamEventBus | None = None
    if mirror_to_nats:
        try:
            nats_bus = await NatsJetStreamEventBus.connect(
                servers=[settings.nats_url.get_secret_value()],
                prefix="marketbot",
                stream="MARKETBOT",
            )
        except Exception as error:
            await logger.awarning(
                "nats_unavailable_local_analysis_continues",
                error_type=type(error).__name__,
            )

    async def mirror_error(subject: str, error: Exception) -> None:
        await logger.awarning(
            "nats_mirror_failed",
            subject=subject,
            error_type=type(error).__name__,
        )

    mirrors: tuple[EventPublisher, ...] = (nats_bus,) if nats_bus is not None else ()
    publisher = EventFanoutPublisher(
        primary=local_bus,
        mirrors=mirrors,
        on_mirror_error=mirror_error,
    )
    alert_path = runtime_root / "alerts" / "marketbot-alerts.ndjson"
    alert_ledger = NdjsonAlertSink(alert_path)
    alert_dispatcher = AlertDispatcher(
        sinks=(
            ConsoleAlertSink(stream=sys.stdout, bell=bell),
            alert_ledger,
        ),
        publisher=AlertEventPublisher(publisher),
    )
    swing = (
        SwingEngineV3()
        if settings.entry_confirmation_rule_version == "3.0.0"
        else SwingEngineV2()
    )
    intraday = (
        IntradayEngineV3()
        if settings.entry_confirmation_rule_version == "3.0.0"
        else IntradayEngineV2()
    )
    runtime = AnalysisRuntime(
        store=MarketBarStore(capacity_per_series=10_000),
        publisher=publisher,
        long_term=LongTermEngineV2(),
        swing=swing,
        intraday=intraday,
        alert_engine=AlertEngine(),
        alert_dispatcher=alert_dispatcher,
        clock=clock,
        entry_watcher=entry_watcher,
    )
    subscription = await local_bus.subscribe(
        "marketbot.v1.market.bar.>", runtime.handle_market_event
    )
    market_data = build_alpaca_market_data_engine(
        settings,
        publisher=publisher,
        backfill_publisher=local_bus,
    )
    universe_provider: PostgresUniverseClient | None = None
    universe_database = entry_watch_database
    if symbols:
        universe = fallback_universe(symbols, source="manual-symbols")
    else:
        if universe_database is None:
            universe_database = create_database_engine(
                settings.database_url.get_secret_value(),
                require_ssl=settings.environment is Environment.PRODUCTION,
            )
        universe_provider = PostgresUniverseClient(
            universe_database,
        )
        universe = await universe_provider.get_universe()
    service = LiveAnalysisService(
        symbols=universe.symbols,
        market_data=market_data,
        local_bus=local_bus,
        runtime=runtime,
    )
    try:
        summary = await service.initialize(clock.now())
        await logger.ainfo(
            "analysis_initialized",
            symbols=summary.symbols,
            market_events=summary.market_events,
            nats_mirroring=nats_bus is not None,
            sec_enabled=False,
            universe_source=universe.source,
            execution_enabled=False,
            entry_watcher_enabled=entry_watcher is not None,
        )
        if once:
            return {
                "alert_path": str(alert_ledger.path_for(clock.now())),
                "execution_enabled": False,
                "entry_watcher_enabled": entry_watcher is not None,
                "market_events": summary.market_events,
                "nats_mirroring": nats_bus is not None,
                "sec_enabled": False,
                "symbols": list(summary.symbols),
                "universe_source": universe.source,
            }
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                service.stream_forever(
                    universe_provider=universe_provider,
                    universe_refresh_seconds=settings.universe_refresh_seconds,
                )
            )
            tasks.create_task(_refresh_weekly_periodically(service, clock, logger))
        return None
    finally:
        await subscription.unsubscribe()
        await market_data.close()
        await http_client.aclose()
        if nats_bus is not None:
            await nats_bus.close()
        if universe_database is not None:
            await universe_database.dispose()
        await local_bus.close()


async def _refresh_weekly_periodically(
    service: LiveAnalysisService,
    clock: SystemClock,
    logger: FilteringBoundLogger,
) -> None:
    while True:
        now = clock.now()
        refresh_at = _next_weekly_refresh(now)
        await asyncio.sleep((refresh_at - now).total_seconds())
        try:
            count = await service.refresh_weekly_context(clock.now())
            await logger.ainfo("weekly_context_refreshed", market_events=count)
        except Exception as error:
            await logger.awarning(
                "weekly_context_refresh_failed",
                error_type=type(error).__name__,
            )


def _next_weekly_refresh(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("weekly refresh scheduling requires a timezone-aware time")
    local_now = now.astimezone(_NEW_YORK)
    days_until_saturday = (5 - local_now.weekday()) % 7
    candidate_date = local_now.date() + timedelta(days=days_until_saturday)
    candidate = datetime.combine(candidate_date, time(hour=2), tzinfo=_NEW_YORK)
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC)
