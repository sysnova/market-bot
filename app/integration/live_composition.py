"""Production composition for the local, analysis-only MarketBot service."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from structlog.typing import FilteringBoundLogger

from app.alert_engine import AlertDispatcher, AlertEngine, ConsoleAlertSink, NdjsonAlertSink
from app.alpaca_market_data import build_alpaca_market_data_engine
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings
from app.dilution_sec_engine import (
    DilutionSecEngine,
    SecEdgarAdapter,
    SecEdgarConfig,
    SecTickerResolver,
)
from app.event_bus import InMemoryEventBus, NatsJetStreamEventBus
from app.intraday_engine import IntradayEngine
from app.long_term_engine import LongTermEngine
from app.swing_engine import SwingEngine

from .alert_publisher import AlertEventPublisher
from .analysis_runtime import AnalysisRuntime
from .event_fanout import EventFanoutPublisher, EventPublisher
from .live_analysis_service import LiveAnalysisService
from .market_bar_store import MarketBarStore
from .sec_refresher import SecAnalysisRefresher
from .supabase_universe import (
    SupabaseUniverseClient,
    SupabaseUniverseConfig,
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
    local_bus = InMemoryEventBus()
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
    alert_dispatcher = AlertDispatcher(
        sinks=(
            ConsoleAlertSink(stream=sys.stdout, bell=bell),
            NdjsonAlertSink(alert_path),
        ),
        publisher=AlertEventPublisher(publisher),
    )
    runtime = AnalysisRuntime(
        store=MarketBarStore(capacity_per_series=10_000),
        publisher=publisher,
        long_term=LongTermEngine(),
        swing=SwingEngine(),
        intraday=IntradayEngine(),
        alert_engine=AlertEngine(),
        alert_dispatcher=alert_dispatcher,
        clock=clock,
    )
    subscription = await local_bus.subscribe(
        "marketbot.v1.market.bar.>", runtime.handle_market_event
    )
    market_data = build_alpaca_market_data_engine(settings, publisher=publisher)
    universe_provider: SupabaseUniverseClient | None = None
    if symbols:
        universe = fallback_universe(symbols, source="manual-symbols")
    elif settings.supabase_universe_configured:
        if settings.supabase_url is None or settings.supabase_desktop_api_key is None:
            raise RuntimeError("Supabase universe configuration is incomplete")
        universe_provider = SupabaseUniverseClient(
            SupabaseUniverseConfig(
                base_url=str(settings.supabase_url),
                desktop_api_key=settings.supabase_desktop_api_key.get_secret_value(),
                fallback_symbols=settings.alpaca_symbols,
            ),
            client=http_client,
        )
        try:
            universe = await universe_provider.get_universe()
        except Exception as error:
            await logger.awarning(
                "supabase_universe_unavailable_using_fallback",
                error_type=type(error).__name__,
            )
            universe = fallback_universe(settings.alpaca_symbols)
    else:
        universe = fallback_universe(settings.alpaca_symbols)
    sec_refresher: SecAnalysisRefresher | None = None
    if settings.sec_enabled:
        if settings.sec_user_agent is None:
            raise ValueError("SEC user agent is required when SEC analysis is enabled")
        sec_config = SecEdgarConfig(user_agent=settings.sec_user_agent)
        sec_refresher = SecAnalysisRefresher(
            resolver=SecTickerResolver(sec_config, client=http_client),
            loader=SecEdgarAdapter(sec_config, client=http_client),
            engine=DilutionSecEngine(),
            runtime=runtime,
            on_error=lambda symbol, error: logger.warning(
                "sec_refresh_failed",
                symbol=symbol,
                error_type=type(error).__name__,
            ),
        )
    service = LiveAnalysisService(
        symbols=universe.symbols,
        market_data=market_data,
        local_bus=local_bus,
        runtime=runtime,
        sec_refresher=sec_refresher,
    )
    try:
        summary = await service.initialize(clock.now())
        await logger.ainfo(
            "analysis_initialized",
            symbols=summary.symbols,
            market_events=summary.market_events,
            nats_mirroring=nats_bus is not None,
            sec_enabled=sec_refresher is not None,
            universe_source=universe.source,
            execution_enabled=False,
        )
        if once:
            return {
                "alert_path": str(alert_path.resolve()),
                "execution_enabled": False,
                "market_events": summary.market_events,
                "nats_mirroring": nats_bus is not None,
                "sec_enabled": sec_refresher is not None,
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
            if sec_refresher is not None:
                tasks.create_task(
                    _refresh_sec_periodically(
                        sec_refresher,
                        service,
                        settings.sec_refresh_hours * 3600,
                        clock,
                    )
                )
        return None
    finally:
        await subscription.unsubscribe()
        await market_data.close()
        await http_client.aclose()
        if nats_bus is not None:
            await nats_bus.close()
        await local_bus.close()


async def _refresh_sec_periodically(
    refresher: SecAnalysisRefresher,
    service: LiveAnalysisService,
    interval_seconds: int,
    clock: SystemClock,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await refresher.refresh(service.symbols, clock.now())


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
