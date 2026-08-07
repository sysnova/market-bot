"""Composition roots for independent market, horizon, and alert processes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.alert_engine import AlertDispatcher, ConsoleAlertSink, NdjsonAlertSink
from app.alpaca_market_data import AlpacaEventNormalizer, AlpacaMarketDataEngine
from app.alpaca_market_data.ports import EventPublisher
from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport, WebsocketsConnector
from app.alpaca_market_data.websocket import AlpacaMarketDataStream
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    ENTRY_WATCH_TRANSITION_EVENT,
    LOCAL_ALERT_EVENT,
    MARKET_ROTATION_EVENT,
    MARKET_ROTATION_SUBJECT,
    SERVICE_HEALTH_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryOpportunityEvent,
    EntryWatchTransition,
    EventEnvelope,
    LocalAlert,
    MarketBar,
    MarketHistoryRequirement,
    MarketRotationReport,
    NamedValue,
    ServiceHealth,
    ServiceStatus,
    Subscription,
    SubscriptionOptions,
    entry_opportunity_subject,
    entry_watch_transition_subject,
    service_health_subject,
)
from app.entry_watcher import EntryWatcherPolicy
from app.event_bus import NatsJetStreamEventBus
from app.patreon_caps_engine import load_patreon_caps_policy
from app.persistence import create_database_engine, create_session_factory

from .alert_publisher import AlertEventPublisher
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_opportunity_store import PostgresEntryOpportunityStore
from .entry_watch_store import PostgresEntryWatchStore
from .intraday_worker import IntradayWorker
from .long_term_worker import LongTermWorker
from .market_history_composition import load_market_history
from .postgres_universe import (
    PostgresUniverseClient,
    UniverseSnapshot,
    fallback_universe,
)
from .swing_worker import SwingWorker

HistoryRequest = MarketHistoryRequirement


class HorizonWorker(Protocol):
    async def bootstrap(
        self,
        bars: Iterable[MarketBar],
        *,
        symbols: tuple[str, ...],
    ) -> int: ...

    async def handle_market_event(self, envelope: EventEnvelope) -> None: ...


def engine_history_requests(horizon: AnalysisHorizon) -> tuple[HistoryRequest, ...]:
    return {
        AnalysisHorizon.LONG_TERM: (
            HistoryRequest(
                timeframe=BarTimeframe.DAY_1, lookback=timedelta(days=400), max_bars_per_symbol=260
            ),
            HistoryRequest(
                timeframe=BarTimeframe.WEEK_1,
                lookback=timedelta(days=365 * 5),
                max_bars_per_symbol=220,
            ),
        ),
        AnalysisHorizon.SWING: (
            HistoryRequest(
                timeframe=BarTimeframe.DAY_1, lookback=timedelta(days=220), max_bars_per_symbol=120
            ),
            HistoryRequest(
                timeframe=BarTimeframe.MINUTE_15,
                lookback=timedelta(days=14),
                max_bars_per_symbol=160,
            ),
        ),
        AnalysisHorizon.INTRADAY: (
            HistoryRequest(
                timeframe=BarTimeframe.MINUTE_1,
                lookback=timedelta(days=5),
                max_bars_per_symbol=500,
            ),
        ),
    }.get(horizon, ())


def engine_live_subjects(horizon: AnalysisHorizon) -> tuple[str, ...]:
    return {
        AnalysisHorizon.LONG_TERM: (
            "marketbot.v1.market.bar.1Min.>",
            "marketbot.v1.market.bar.1Day.>",
            "marketbot.v1.market.bar.1Week.>",
        ),
        AnalysisHorizon.SWING: (
            "marketbot.v1.market.bar.1Min.>",
            "marketbot.v1.market.bar.1Day.>",
        ),
        AnalysisHorizon.INTRADAY: ("marketbot.v1.market.bar.1Min.>",),
    }.get(horizon, ())


def market_stream_subscription_options() -> dict[str, bool]:
    """Subscribe only to live records consumed by the analytical engines."""

    return {
        "trades": True,
        "quotes": True,
        "bars": True,
        "updated_bars": True,
        "daily_bars": True,
    }


async def run_engine_process(
    *,
    horizon: AnalysisHorizon,
    symbols: tuple[str, ...] | None = None,
    once: bool = False,
    ready_path: Path | None = None,
) -> dict[str, object] | None:
    """Bootstrap one horizon from REST, then consume only its live NATS subjects."""

    if horizon not in {
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }:
        raise ValueError("distributed worker requires Long, Swing, or Intraday horizon")
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger(f"{horizon.value.lower()}-worker")
    clock = SystemClock()
    http_client = httpx.AsyncClient()
    bus: NatsJetStreamEventBus | None = None
    database: AsyncEngine | None = None
    subscriptions: list[Subscription] = []
    try:
        universe = await _resolve_universe(settings, symbols)
        bus = await _connect_nats(settings)
        database = create_database_engine(
            settings.database_url.get_secret_value(),
            require_ssl=settings.environment is Environment.PRODUCTION,
        )
        as_of = clock.now()
        bars = await load_market_history(
            settings,
            database,
            engine_id=_service_name(horizon),
            symbols=universe.symbols,
            requirements=engine_history_requests(horizon),
            as_of=as_of,
        )
        worker = _build_worker(horizon, bus, assembly=assembly)
        result_count = await worker.bootstrap(bars, symbols=universe.symbols)
        current_symbols = set(universe.symbols)
        refresh_lock = asyncio.Lock()
        if not once:
            for index, subject in enumerate(engine_live_subjects(horizon), start=1):
                subscriptions.append(
                    await bus.subscribe(
                        subject,
                        worker.handle_market_event,
                        options=SubscriptionOptions(
                            durable_name=f"marketbot-{horizon.value.lower()}-v2-{index}",
                            replay_all=False,
                            ack_wait_seconds=60,
                        ),
                    )
                )
            if horizon in {AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING} and symbols is None:

                async def handle_rotation(envelope: EventEnvelope) -> None:
                    nonlocal current_symbols
                    if envelope.event_type != MARKET_ROTATION_EVENT:
                        return
                    report = (
                        envelope.payload
                        if isinstance(envelope.payload, MarketRotationReport)
                        else MarketRotationReport.model_validate(envelope.payload, strict=False)
                    )
                    if not report.watchlist_additions:
                        return
                    async with refresh_lock:
                        refreshed = await _resolve_universe(settings, None)
                        added = tuple(
                            symbol for symbol in refreshed.symbols if symbol not in current_symbols
                        )
                        current_symbols = set(refreshed.symbols)
                        if not added:
                            return
                        assert database is not None
                        refresh_bars = await load_market_history(
                            settings,
                            database,
                            engine_id=_service_name(horizon),
                            symbols=added,
                            requirements=engine_history_requests(horizon),
                            as_of=clock.now(),
                        )
                        refreshed_results = await worker.bootstrap(refresh_bars, symbols=added)
                        await logger.ainfo(
                            "rotation_universe_refreshed",
                            symbols=list(added),
                            historical_bars=len(refresh_bars),
                            initial_results=refreshed_results,
                            rotation_run_id=report.run_id,
                        )

                subscriptions.append(
                    await bus.subscribe(
                        MARKET_ROTATION_SUBJECT,
                        handle_rotation,
                        options=SubscriptionOptions(
                            durable_name=f"marketbot-{horizon.value.lower()}-rotation-v1",
                            replay_all=False,
                            ack_wait_seconds=120,
                        ),
                    )
                )
        summary: dict[str, object] = {
            "service": _service_name(horizon),
            "horizon": horizon.value,
            "symbols": len(universe.symbols),
            "historical_bars": len(bars),
            "initial_results": result_count,
            "universe_source": universe.source,
            "live_subjects": list(engine_live_subjects(horizon)),
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": assembly.spec(_slot_for_horizon(horizon)).implementation,
            "engine_strategy_version": assembly.spec(_slot_for_horizon(horizon)).strategy.version,
        }
        await _publish_health(bus, _service_name(horizon), summary, clock.now())
        if ready_path is not None:
            _write_ready(ready_path, summary)
        await logger.ainfo("distributed_engine_ready", **summary)
        if once:
            return summary
        await asyncio.Event().wait()
        return None
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if database is not None:
            await database.dispose()
        await http_client.aclose()
        if bus is not None:
            await bus.close()


async def run_market_stream_process(
    *,
    symbols: tuple[str, ...] | None = None,
) -> None:
    """Publish Alpaca WebSocket updates to NATS; this process runs no analysis engine."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("alpaca-live-stream")
    clock = SystemClock()
    http_client = httpx.AsyncClient()
    bus: NatsJetStreamEventBus | None = None
    engine: AlpacaMarketDataEngine | None = None
    rotation_subscription: Subscription | None = None
    rotation_refresh = asyncio.Event()
    macro_symbols = load_patreon_caps_policy(
        assembly.strategy_artifact(EngineSlot.PATREON_CAPS)
    ).macro_symbols
    try:
        bus = await _connect_nats(settings)
        engine = _build_stream_engine(settings, bus)

        async def handle_rotation(envelope: EventEnvelope) -> None:
            if envelope.event_type != MARKET_ROTATION_EVENT:
                return
            report = (
                envelope.payload
                if isinstance(envelope.payload, MarketRotationReport)
                else MarketRotationReport.model_validate(envelope.payload, strict=False)
            )
            if report.watchlist_additions:
                rotation_refresh.set()

        if symbols is None:
            rotation_subscription = await bus.subscribe(
                MARKET_ROTATION_SUBJECT,
                handle_rotation,
                options=SubscriptionOptions(
                    durable_name="marketbot-alpaca-stream-rotation-v1",
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        backoff = 1.0
        while True:
            try:
                rotation_refresh.clear()
                universe = await _resolve_universe(settings, symbols)
                holdings = await _resolve_holdings(settings)
                stream_symbols = _stream_symbols(universe.symbols, macro_symbols)
                await _publish_health(
                    bus,
                    "alpaca-market-stream",
                    {
                        "symbols": len(stream_symbols),
                        "patreon_macro_symbols": len(macro_symbols),
                        "portfolio_symbols": len(holdings.symbols),
                        "universe_source": universe.source,
                    },
                    clock.now(),
                )
                stream_task = asyncio.create_task(
                    engine.stream_once(
                        stream_symbols,
                        **market_stream_subscription_options(),
                        trade_symbols=holdings.symbols,
                        quote_symbols=holdings.symbols,
                    )
                )
                refresh_task = asyncio.create_task(rotation_refresh.wait())
                done, _ = await asyncio.wait(
                    (stream_task, refresh_task),
                    timeout=settings.universe_refresh_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stream_task in done:
                    refresh_task.cancel()
                    count = await stream_task
                else:
                    stream_task.cancel()
                    refresh_task.cancel()
                    await asyncio.gather(stream_task, refresh_task, return_exceptions=True)
                    backoff = 1.0
                    continue
                if count > 0:
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await logger.aexception(
                    "alpaca_stream_disconnected",
                    error_type=type(error).__name__,
                    reconnect_seconds=backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    finally:
        if rotation_subscription is not None:
            await rotation_subscription.unsubscribe()
        if engine is not None:
            await engine.close()
        await http_client.aclose()
        if bus is not None:
            await bus.close()


def _stream_symbols(
    universe_symbols: tuple[str, ...], macro_symbols: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*universe_symbols, *macro_symbols)))


async def run_alert_process(
    *,
    runtime_root: Path,
    bell: bool,
    ready_path: Path | None = None,
) -> None:
    """Consume every engine result and publish named human alerts for viewers."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    clock = SystemClock()
    bus = await _connect_nats(settings)
    engine = assembly.build_alert()
    dispatcher = AlertDispatcher(
        sinks=(
            ConsoleAlertSink(stream=sys.stdout, bell=bell),
            NdjsonAlertSink(runtime_root / "alerts" / "marketbot-alerts.ndjson"),
        ),
        publisher=AlertEventPublisher(bus),
    )

    async def handle_analysis(envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        alert = engine.ingest(result, now=clock.now())
        if alert is not None:
            await dispatcher.dispatch(alert)

    async def handle_entry_watch(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_WATCH_TRANSITION_EVENT:
            return
        transition = (
            envelope.payload
            if isinstance(envelope.payload, EntryWatchTransition)
            else EntryWatchTransition.model_validate(envelope.payload, strict=False)
        )
        alert = engine.ingest_entry_watch(transition, now=clock.now())
        await dispatcher.dispatch(alert)

    async def handle_entry_opportunity(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
            return
        event = (
            envelope.payload
            if isinstance(envelope.payload, EntryOpportunityEvent)
            else EntryOpportunityEvent.model_validate(envelope.payload, strict=False)
        )
        alert = engine.ingest_entry_opportunity(event, now=clock.now())
        await dispatcher.dispatch(alert)

    subscriptions = (
        await bus.subscribe(
            "marketbot.v1.analysis.result.>",
            handle_analysis,
            options=SubscriptionOptions(
                durable_name="marketbot-alert-v2-analysis",
                replay_all=False,
                ack_wait_seconds=60,
            ),
        ),
        await bus.subscribe(
            "marketbot.v1.entry-watch.transition.>",
            handle_entry_watch,
            options=SubscriptionOptions(
                durable_name="marketbot-alert-v2-entry-watch",
                replay_all=False,
                ack_wait_seconds=60,
            ),
        ),
        await bus.subscribe(
            "marketbot.v1.entry-opportunity.transition.>",
            handle_entry_opportunity,
            options=SubscriptionOptions(
                durable_name="marketbot-alert-v2-entry-opportunity",
                replay_all=False,
                ack_wait_seconds=60,
            ),
        ),
    )
    try:
        details = {
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": assembly.spec(EngineSlot.ALERT).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.ALERT).strategy.version,
            "analysis_subject": "marketbot.v1.analysis.result.>",
            "entry_watch_subject": "marketbot.v1.entry-watch.transition.>",
            "entry_opportunity_subject": "marketbot.v1.entry-opportunity.transition.>",
        }
        await _publish_health(
            bus,
            "alert-v2",
            details,
            clock.now(),
        )
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()


async def run_entry_watcher_process(*, ready_path: Path | None = None) -> None:
    """Detect and persist entry theses, then publish only watcher transitions."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    if not settings.entry_watcher_enabled:
        raise RuntimeError("entry watcher is disabled by configuration")
    clock = SystemClock()
    database: AsyncEngine = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        session_factory = create_session_factory(database)
        store = PostgresEntryWatchStore(session_factory)
        if not await store.is_ready():
            raise RuntimeError(
                "entry watcher schema is unavailable; apply 20260726180000_entry_watches.sql"
            )
        watcher = assembly.build_entry_watcher(
            store=store,
            policy=EntryWatcherPolicy(ttl=timedelta(days=settings.entry_watch_ttl_days)),
        )
        bus = await _connect_nats(settings)
        watcher_spec = assembly.spec(EngineSlot.ENTRY_WATCHER)
        watcher_version = watcher_spec.implementation
        watcher_service = f"entry-watcher-v{watcher_version.split('.', 1)[0]}"

        async def handle_analysis(envelope: EventEnvelope) -> None:
            if envelope.event_type != ANALYSIS_RESULT_EVENT:
                return
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
            transition = await watcher.ingest(result, now=clock.now())
            if transition is not None:
                await bus.publish(
                    entry_watch_transition_subject(transition.status, transition.symbol),
                    EventEnvelope(
                        event_type=ENTRY_WATCH_TRANSITION_EVENT,
                        occurred_at=transition.occurred_at,
                        source=watcher_service,
                        subject=transition.symbol,
                        payload=transition,
                    ),
                )

        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.analysis.result.>",
                handle_analysis,
                options=SubscriptionOptions(
                    durable_name=f"{watcher_service}-analysis",
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
        details = {
            "service": watcher_service,
            "engine_version": watcher_version,
            "engine_strategy_version": watcher_spec.strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "input_subject": "marketbot.v1.analysis.result.>",
            "output_subject": "marketbot.v1.entry-watch.transition.>",
            "persistence": "postgresql",
        }
        await _publish_health(bus, watcher_service, details, clock.now())
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()


async def run_entry_opportunity_process(*, ready_path: Path | None = None) -> None:
    """Track and audit paper opportunities as an independent NATS service."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    clock = SystemClock()
    database: AsyncEngine = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    reconcile_task: asyncio.Task[None] | None = None
    try:
        store = PostgresEntryOpportunityStore(create_session_factory(database))
        if not await store.is_ready():
            raise RuntimeError(
                "entry opportunity schema is unavailable; apply "
                "20260807010000_entry_opportunity_lifecycle.sql"
            )
        engine = assembly.build_entry_opportunity(store=store)
        spec = assembly.spec(EngineSlot.ENTRY_OPPORTUNITY)
        service = f"entry-opportunity-v{spec.implementation.split('.', 1)[0]}"
        bus = await _connect_nats(settings)

        async def publish(event: EntryOpportunityEvent) -> None:
            await bus.publish(
                entry_opportunity_subject(event.opportunity.status, event.opportunity.symbol),
                EventEnvelope(
                    event_type=ENTRY_OPPORTUNITY_EVENT,
                    occurred_at=event.occurred_at,
                    source=service,
                    subject=event.opportunity.symbol,
                    payload=event,
                    causation_id=event.event_id,
                ),
            )

        async def handle_analysis(envelope: EventEnvelope) -> None:
            if envelope.event_type != ANALYSIS_RESULT_EVENT:
                return
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
            for event in await engine.ingest_analysis(result, now=clock.now()):
                await publish(event)

        async def handle_transition(envelope: EventEnvelope) -> None:
            if envelope.event_type != ENTRY_WATCH_TRANSITION_EVENT:
                return
            transition = (
                envelope.payload
                if isinstance(envelope.payload, EntryWatchTransition)
                else EntryWatchTransition.model_validate(envelope.payload, strict=False)
            )
            for event in await engine.ingest_transition(transition):
                await publish(event)

        async def handle_bar(envelope: EventEnvelope) -> None:
            if envelope.event_type not in {"market.bar.received", "market.bar.updated"}:
                return
            bar = (
                envelope.payload
                if isinstance(envelope.payload, MarketBar)
                else MarketBar.model_validate(envelope.payload, strict=False)
            )
            if not bar.is_final or bar.timeframe is not BarTimeframe.MINUTE_1:
                return
            for event in await engine.ingest_bar(bar):
                await publish(event)

        async def handle_alert(envelope: EventEnvelope) -> None:
            if envelope.event_type != LOCAL_ALERT_EVENT:
                return
            alert = (
                envelope.payload
                if isinstance(envelope.payload, LocalAlert)
                else LocalAlert.model_validate(envelope.payload, strict=False)
            )
            for event in await engine.ingest_alert(alert):
                await publish(event)

        handlers = (
            ("marketbot.v1.analysis.result.>", handle_analysis, "analysis"),
            ("marketbot.v1.entry-watch.transition.>", handle_transition, "entry-watch"),
            ("marketbot.v1.market.bar.1Min.>", handle_bar, "bars"),
            ("marketbot.v1.alert.local.>", handle_alert, "alerts"),
        )
        for subject, handler, suffix in handlers:
            subscriptions.append(
                await bus.subscribe(
                    subject,
                    handler,
                    options=SubscriptionOptions(
                        durable_name=f"{service}-{suffix}",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                )
            )

        async def reconcile_opportunities() -> None:
            universe_client = PostgresUniverseClient(database)
            while True:
                await asyncio.sleep(60)
                try:
                    universe = await universe_client.get_universe()
                    for event in await engine.reconcile(
                        now=clock.now(), active_symbols=universe.symbols
                    ):
                        await publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await get_logger(service).aexception(
                        "entry_opportunity_reconcile_failed",
                        error_type=type(error).__name__,
                    )

        reconcile_task = asyncio.create_task(reconcile_opportunities())
        details = {
            "service": service,
            "engine_version": spec.implementation,
            "engine_strategy_version": spec.strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "analysis_subject": "marketbot.v1.analysis.result.>",
            "entry_watch_subject": "marketbot.v1.entry-watch.transition.>",
            "maturity_subject": "marketbot.v1.alert.local.>",
            "market_bar_subject": "marketbot.v1.market.bar.1Min.>",
            "output_subject": "marketbot.v1.entry-opportunity.transition.>",
            "persistence": "postgresql",
        }
        await _publish_health(bus, service, details, clock.now())
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        if reconcile_task is not None:
            reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reconcile_task
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()


async def _resolve_universe(
    settings: AppSettings,
    symbols: tuple[str, ...] | None,
) -> UniverseSnapshot:
    if symbols:
        return fallback_universe(symbols, source="manual-symbols")
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    try:
        return await PostgresUniverseClient(
            database,
        ).get_universe()
    finally:
        await database.dispose()


async def _resolve_holdings(settings: AppSettings) -> UniverseSnapshot:
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    try:
        return await PostgresUniverseClient(database).get_holdings()
    finally:
        await database.dispose()


async def _connect_nats(settings: AppSettings) -> NatsJetStreamEventBus:
    return await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )


async def connect_nats(settings: AppSettings) -> NatsJetStreamEventBus:
    """Connect the shared NATS adapter for integration composition roots."""
    return await _connect_nats(settings)


def _build_rest(settings: AppSettings) -> AlpacaRestClient:
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    return AlpacaRestClient(
        api_key_id=settings.alpaca_api_key_id.get_secret_value(),
        api_secret_key=settings.alpaca_api_secret_key.get_secret_value(),
        base_url=str(settings.alpaca_data_base_url),
        feed=settings.alpaca_data_feed,
        adjustment=settings.alpaca_adjustment,
        transport=HttpxTransport(),
    )


def build_rest(settings: AppSettings) -> AlpacaRestClient:
    """Build the shared Alpaca REST adapter for integration composition roots."""
    return _build_rest(settings)


def _build_stream_engine(
    settings: AppSettings,
    publisher: EventPublisher,
) -> AlpacaMarketDataEngine:
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    key = settings.alpaca_api_key_id.get_secret_value()
    secret = settings.alpaca_api_secret_key.get_secret_value()
    feed = settings.alpaca_data_feed
    return AlpacaMarketDataEngine(
        rest=None,
        stream=AlpacaMarketDataStream(
            api_key_id=key,
            api_secret_key=secret,
            base_url=str(settings.alpaca_market_data_stream_url),
            feed=feed,
            connector=WebsocketsConnector(),
        ),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed=feed),
        rest_batch_size=settings.alpaca_rest_batch_size,
    )


def _build_worker(
    horizon: AnalysisHorizon,
    publisher: EventPublisher,
    *,
    assembly: MarketBotAssembly,
) -> HorizonWorker:
    if horizon is AnalysisHorizon.LONG_TERM:
        return LongTermWorker(publisher=publisher, analyzer=assembly.build_long_term())
    if horizon is AnalysisHorizon.SWING:
        return SwingWorker(publisher=publisher, analyzer=assembly.build_swing())
    if horizon is AnalysisHorizon.INTRADAY:
        return IntradayWorker(publisher=publisher, analyzer=assembly.build_intraday())
    raise ValueError("unsupported distributed horizon")


def _slot_for_horizon(horizon: AnalysisHorizon) -> EngineSlot:
    return {
        AnalysisHorizon.LONG_TERM: EngineSlot.LONG_TERM,
        AnalysisHorizon.SWING: EngineSlot.SWING,
        AnalysisHorizon.INTRADAY: EngineSlot.INTRADAY,
    }[horizon]


async def _publish_health(
    publisher: EventPublisher,
    service: str,
    details: Mapping[str, object],
    observed_at: datetime,
) -> None:
    health = ServiceHealth(
        service=service,
        status=ServiceStatus.HEALTHY,
        observed_at=observed_at,
        version="2.0.0",
        details=tuple(NamedValue(name=key, value=value) for key, value in sorted(details.items())),
    )
    await publisher.publish(
        service_health_subject(service),
        EventEnvelope(
            event_type=SERVICE_HEALTH_EVENT,
            occurred_at=observed_at,
            source=service,
            subject=service,
            payload=health,
        ),
    )


def _service_name(horizon: AnalysisHorizon) -> str:
    return {
        AnalysisHorizon.LONG_TERM: "long-term-v2",
        AnalysisHorizon.SWING: "swing-v2",
        AnalysisHorizon.INTRADAY: "intraday-v2",
    }[horizon]


def _write_ready(path: Path, summary: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def write_ready(path: Path, summary: Mapping[str, object]) -> None:
    """Atomically write a process readiness summary."""
    _write_ready(path, summary)
