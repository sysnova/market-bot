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
from typing import Protocol, cast

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.alert_engine import (
    AlertDispatcher,
    AlertEngineV31,
    AlertEngineV32,
    ConsoleAlertSink,
    NdjsonAlertSink,
)
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
    ENTRY_SETUP_ASSESSMENT_EVENT,
    ENTRY_SIGNAL_EVENT,
    ENTRY_WATCH_TRANSITION_EVENT,
    LOCAL_ALERT_EVENT,
    MARKET_ROTATION_EVENT,
    MARKET_ROTATION_SUBJECT,
    SERVICE_HEALTH_EVENT,
    UNIVERSE_CHANGED_EVENT,
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryOpportunityEvent,
    EntrySetupAssessment,
    EntrySignal,
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
    UniverseChanged,
    service_health_subject,
    universe_changed_subject,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_watcher import EntryWatcherPolicy
from app.event_bus import NatsJetStreamEventBus
from app.patreon_caps_engine import PatreonCapsPolicy
from app.persistence import create_database_engine, create_session_factory

from .alert_decision_state_store import PostgresAlertDecisionStateStore
from .alert_publisher import AlertEventPublisher
from .alert_sounds import (
    play_early_intraday_sound,
    play_entry_zone_watch_sound,
    play_swing_setup_watch_sound,
)
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_opportunity_store import PostgresEntryOpportunityStore
from .entry_signal_adapter import entry_signal_from_alert_watch, publish_entry_signal
from .entry_watch_store import PostgresEntryWatchStore
from .intraday_worker import IntradayWorker
from .long_term_worker import LongTermWorker
from .market_history_composition import load_market_history
from .outbox_relay import OutboxRelay
from .postgres_universe import (
    PostgresUniverseClient,
    UniverseSnapshot,
    fallback_universe,
)
from .swing_worker import SwingWorker
from .universe_events import UniverseEventPublisher
from .universe_policy import universe_health_details

HistoryRequest = MarketHistoryRequirement


class HorizonWorker(Protocol):
    def activate_universe(self, symbols: tuple[str, ...]) -> None: ...

    async def bootstrap(
        self,
        bars: Iterable[MarketBar],
        *,
        symbols: tuple[str, ...],
    ) -> int: ...

    async def handle_market_event(self, envelope: EventEnvelope) -> None: ...

    async def handle_universe_changed(self, change: UniverseChanged) -> int: ...


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
            HistoryRequest(
                timeframe=BarTimeframe.MINUTE_1,
                lookback=timedelta(days=5),
                max_bars_per_symbol=500,
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
            HistoryRequest(
                timeframe=BarTimeframe.MINUTE_1,
                lookback=timedelta(days=5),
                max_bars_per_symbol=500,
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
        worker.activate_universe(universe.symbols)
        current_symbols = set(universe.symbols)
        refresh_lock = asyncio.Lock()
        if not once:
            for index, subject in enumerate(engine_live_subjects(horizon), start=1):
                subscriptions.append(
                    await bus.subscribe(
                        subject,
                        worker.handle_market_event,
                        options=SubscriptionOptions(
                            durable_name=_horizon_durable_name(horizon, index),
                            replay_all=False,
                            ack_wait_seconds=60,
                        ),
                    )
                )
            if symbols is None:

                async def handle_universe(envelope: EventEnvelope) -> None:
                    nonlocal current_symbols
                    if envelope.event_type != UNIVERSE_CHANGED_EVENT:
                        return
                    change = (
                        envelope.payload
                        if isinstance(envelope.payload, UniverseChanged)
                        else UniverseChanged.model_validate(envelope.payload, strict=False)
                    )
                    async with refresh_lock:
                        added = tuple(
                            symbol for symbol in change.symbols if symbol not in current_symbols
                        )
                        removed = tuple(
                            symbol for symbol in current_symbols if symbol not in change.symbols
                        )
                        if not added and not removed:
                            return
                        consumer_change = change.model_copy(
                            update={
                                "previous_symbols": tuple(sorted(current_symbols)),
                                "added_symbols": added,
                                "removed_symbols": removed,
                            }
                        )
                        assert database is not None
                        refresh_bars: tuple[MarketBar, ...] = ()
                        if added:
                            refresh_bars = await load_market_history(
                                settings,
                                database,
                                engine_id=_service_name(horizon),
                                symbols=added,
                                requirements=engine_history_requests(horizon),
                                as_of=clock.now(),
                            )
                            await worker.bootstrap(refresh_bars, symbols=added)
                        initial_results = await worker.handle_universe_changed(consumer_change)
                        current_symbols = set(change.symbols)
                        await logger.ainfo(
                            "core_universe_changed",
                            added=list(added),
                            removed=list(removed),
                            historical_bars=len(refresh_bars),
                            initial_results=initial_results,
                            source=change.source,
                        )

                subscriptions.append(
                    await bus.subscribe(
                        universe_changed_subject(),
                        handle_universe,
                        options=SubscriptionOptions(
                            durable_name=f"marketbot-{_service_name(horizon)}-universe-v1",
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
            **universe_health_details(_service_name(horizon)),
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
    previous_core_symbols: tuple[str, ...] = ()
    macro_symbols = cast(
        "PatreonCapsPolicy",
        assembly.resolve_strategy(EngineSlot.PATREON_CAPS),
    ).macro_symbols
    try:
        bus = await _connect_nats(settings)
        engine = _build_stream_engine(settings, bus)
        universe_publisher = UniverseEventPublisher(bus)

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
                if universe.symbols != previous_core_symbols:
                    await universe_publisher.publish_universe_changed(
                        UniverseChanged(
                            occurred_at=clock.now(),
                            source=universe.source,
                            previous_symbols=previous_core_symbols,
                            symbols=universe.symbols,
                            added_symbols=tuple(
                                value
                                for value in universe.symbols
                                if value not in previous_core_symbols
                            ),
                            removed_symbols=tuple(
                                value
                                for value in previous_core_symbols
                                if value not in universe.symbols
                            ),
                        )
                    )
                    previous_core_symbols = universe.symbols
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
    logger = get_logger("alert")
    clock = SystemClock()
    database: AsyncEngine = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    session_factory = create_session_factory(database)
    alert_spec = assembly.spec(EngineSlot.ALERT)
    state_store = PostgresAlertDecisionStateStore(
        session_factory,
        implementation_version=alert_spec.implementation,
    )
    restored_state = None
    try:
        if alert_spec.implementation in {
            "3.1.0",
            "3.2.0",
            "3.3.0",
            "3.4.0",
            "3.5.0",
        }:
            if not await state_store.is_ready():
                raise RuntimeError(
                    "alert decision state schema is unavailable; apply the decision-state migration"
                )
            restored_state = await state_store.load()
        bus = await _connect_nats(settings)
    except Exception:
        await database.dispose()
        raise
    engine = assembly.build_alert(restored_state=restored_state)
    stateful_engine = engine if isinstance(engine, AlertEngineV31) else None
    checkpoint_requested = asyncio.Event()

    async def checkpoint_alert_state() -> None:
        if stateful_engine is None:
            return
        while True:
            await checkpoint_requested.wait()
            await asyncio.sleep(settings.alert_checkpoint_interval_seconds)
            checkpoint_requested.clear()
            try:
                await state_store.save_if_changed(stateful_engine.snapshot_state())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                checkpoint_requested.set()
                await logger.aexception(
                    "alert_checkpoint_failed",
                    error_type=type(error).__name__,
                )

    checkpoint_task: asyncio.Task[None] | None = None
    dispatcher = AlertDispatcher(
        sinks=(
            ConsoleAlertSink(stream=sys.stdout, bell=bell, color=True),
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
            if bell and alert.kind is AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION:
                play_early_intraday_sound(fallback=sys.stdout)
            elif bell and alert.kind is AlertKind.SWING_SETUP:
                play_swing_setup_watch_sound(fallback=sys.stdout)
        if stateful_engine is not None:
            checkpoint_requested.set()

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
        if bell and ("IN_ZONE" in alert.title.upper() or "BREAKAWAY WATCH" in alert.title.upper()):
            play_entry_zone_watch_sound(fallback=sys.stdout)
        signal = entry_signal_from_alert_watch(transition)
        if signal is not None:
            await publish_entry_signal(bus, signal, source="alert")

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

    async def handle_entry_setup(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_SETUP_ASSESSMENT_EVENT:
            return
        if not isinstance(engine, AlertEngineV32):
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, EntrySetupAssessment)
            else EntrySetupAssessment.model_validate(envelope.payload, strict=False)
        )
        alert = engine.ingest_setup_assessment(assessment, now=clock.now())
        if alert is not None:
            await dispatcher.dispatch(alert)

    subscriptions: list[Subscription] = []
    try:
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.analysis.result.>",
                handle_analysis,
                options=SubscriptionOptions(
                    durable_name=_alert_durable_name("analysis"),
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
        if isinstance(engine, AlertEngineV32):
            subscriptions.append(
                await bus.subscribe(
                    "marketbot.v1.entry-setup.>",
                    handle_entry_setup,
                    options=SubscriptionOptions(
                        durable_name=_alert_durable_name("entry-setup"),
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                )
            )
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.entry-watch.transition.>",
                handle_entry_watch,
                options=SubscriptionOptions(
                    durable_name=_alert_durable_name("entry-watch"),
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.entry-opportunity.transition.>",
                handle_entry_opportunity,
                options=SubscriptionOptions(
                    durable_name=_alert_durable_name("entry-opportunity"),
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
    except Exception:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()
        await database.dispose()
        raise
    if stateful_engine is not None:
        checkpoint_task = asyncio.create_task(
            checkpoint_alert_state(),
            name="alert-checkpoint",
        )
    try:
        details = {
            "service": "alert",
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": alert_spec.implementation,
            "engine_strategy_version": alert_spec.strategy.version,
            "analysis_subject": "marketbot.v1.analysis.result.>",
            "entry_watch_subject": "marketbot.v1.entry-watch.transition.>",
            "entry_opportunity_subject": "marketbot.v1.entry-opportunity.transition.>",
            "entry_setup_subject": (
                "marketbot.v1.entry-setup.>" if isinstance(engine, AlertEngineV32) else "disabled"
            ),
            "decision_state": (
                "postgresql"
                if alert_spec.implementation in {"3.1.0", "3.2.0", "3.3.0", "3.4.0", "3.5.0"}
                else "memory"
            ),
            "decision_checkpoint_interval_seconds": (
                settings.alert_checkpoint_interval_seconds if stateful_engine is not None else None
            ),
            **universe_health_details("alert"),
        }
        await _publish_health(
            bus,
            "alert",
            details,
            clock.now(),
        )
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if checkpoint_task is not None and stateful_engine is not None:
            checkpoint_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await checkpoint_task
            try:
                await state_store.save_if_changed(stateful_engine.snapshot_state())
            except Exception as error:
                await logger.aexception(
                    "alert_checkpoint_final_flush_failed",
                    error_type=type(error).__name__,
                )
        await bus.close()
        await database.dispose()


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
        watcher_spec = assembly.spec(EngineSlot.ENTRY_WATCHER)
        watcher_version = watcher_spec.implementation
        watcher_service = "entry-watcher"
        store = PostgresEntryWatchStore(session_factory, source=watcher_service)
        if not await store.is_ready():
            raise RuntimeError(
                "entry watcher schema is unavailable; apply 20260726180000_entry_watches.sql"
            )
        watcher = assembly.build_entry_watcher(
            store=store,
            policy=EntryWatcherPolicy(ttl=timedelta(days=settings.entry_watch_ttl_days)),
        )
        bus = await _connect_nats(settings)

        async def handle_analysis(envelope: EventEnvelope) -> None:
            if envelope.event_type != ANALYSIS_RESULT_EVENT:
                return
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
            await watcher.ingest(result, now=clock.now())

        subscription = await bus.subscribe(
            "marketbot.v1.analysis.result.>",
            handle_analysis,
            options=_entry_watcher_subscription_options(),
        )
        subscriptions.append(subscription)
        await bus.wait_until_caught_up(subscription, timeout_seconds=60)
        details = {
            "service": watcher_service,
            "engine_version": watcher_version,
            "engine_strategy_version": watcher_spec.strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "input_subject": "marketbot.v1.analysis.result.>",
            "output_subject": "marketbot.v1.entry-watch.transition.>",
            "persistence": "postgresql",
            "delivery": "transactional-outbox",
            **universe_health_details("entry-watcher"),
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
        spec = assembly.spec(EngineSlot.ENTRY_OPPORTUNITY)
        service = "entry-opportunity"
        store = PostgresEntryOpportunityStore(
            create_session_factory(database),
            source=service,
        )
        if not await store.is_ready():
            raise RuntimeError(
                "entry opportunity schema is unavailable; apply "
                "20260807010000_entry_opportunity_lifecycle.sql"
            )
        engine = assembly.build_entry_opportunity(store=store)
        bus = await _connect_nats(settings)

        async def handle_analysis(envelope: EventEnvelope) -> None:
            if envelope.event_type != ANALYSIS_RESULT_EVENT:
                return
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
            await engine.ingest_analysis(result, now=clock.now())

        async def handle_transition(envelope: EventEnvelope) -> None:
            if envelope.event_type != ENTRY_WATCH_TRANSITION_EVENT:
                return
            transition = (
                envelope.payload
                if isinstance(envelope.payload, EntryWatchTransition)
                else EntryWatchTransition.model_validate(envelope.payload, strict=False)
            )
            await engine.ingest_transition(transition)

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
            await engine.ingest_bar(bar)

        async def handle_alert(envelope: EventEnvelope) -> None:
            if envelope.event_type != LOCAL_ALERT_EVENT:
                return
            alert = (
                envelope.payload
                if isinstance(envelope.payload, LocalAlert)
                else LocalAlert.model_validate(envelope.payload, strict=False)
            )
            await engine.ingest_alert(alert)

        async def handle_signal(envelope: EventEnvelope) -> None:
            if envelope.event_type != ENTRY_SIGNAL_EVENT:
                return
            if not isinstance(engine, EntryOpportunityEngineV2):
                return
            signal = (
                envelope.payload
                if isinstance(envelope.payload, EntrySignal)
                else EntrySignal.model_validate(envelope.payload, strict=False)
            )
            await engine.ingest_signal(signal)

        handlers = [
            ("marketbot.v1.analysis.result.>", handle_analysis, "analysis"),
            ("marketbot.v1.entry-watch.transition.>", handle_transition, "entry-watch"),
            ("marketbot.v1.market.bar.1Min.>", handle_bar, "bars"),
        ]
        if spec.implementation == "2.0.0":
            handlers.append(("marketbot.v1.entry-signal.>", handle_signal, "entry-signal"))
        else:
            handlers.append(("marketbot.v1.alert.local.>", handle_alert, "alerts"))
        for subject, handler, suffix in handlers:
            subscriptions.append(
                await bus.subscribe(
                    subject,
                    handler,
                    options=SubscriptionOptions(
                        durable_name=f"marketbot-{service}-{suffix}-v1",
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
                    await engine.reconcile(now=clock.now(), active_symbols=universe.symbols)
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
            "delivery": "transactional-outbox",
            **universe_health_details("entry-opportunity"),
        }
        if spec.implementation == "2.0.0":
            details.pop("maturity_subject")
            details["entry_signal_subject"] = "marketbot.v1.entry-signal.>"
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


async def run_outbox_relay_process(*, ready_path: Path | None = None) -> None:
    """Relay committed PostgreSQL outbox envelopes to NATS outside transactions."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    clock = SystemClock()
    database: AsyncEngine = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    try:
        session_factory = create_session_factory(database)
        async with session_factory() as session:
            outbox = await session.scalar(text("select to_regclass('market_bot.outbox_events')"))
        if outbox is None:
            raise RuntimeError("outbox schema is unavailable; apply the foundation migration")
        bus = await _connect_nats(settings)
        relay = OutboxRelay(session_factory, bus, clock=clock.now)
        details = {
            "service": "outbox-relay",
            "persistence": "postgresql",
            "transport": "nats-jetstream",
            "claim": "for-update-skip-locked",
            "delivery": "at-least-once",
        }
        await _publish_health(bus, "outbox-relay", details, clock.now())
        if ready_path is not None:
            _write_ready(ready_path, details)
        await relay.run()
    finally:
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


async def publish_health(
    publisher: EventPublisher,
    service: str,
    details: Mapping[str, object],
    observed_at: datetime,
) -> None:
    """Publish a shared readiness payload from an integration composition root."""

    await _publish_health(publisher, service, details, observed_at)


def _service_name(horizon: AnalysisHorizon) -> str:
    return {
        AnalysisHorizon.LONG_TERM: "long-term",
        AnalysisHorizon.SWING: "swing",
        AnalysisHorizon.INTRADAY: "intraday",
    }[horizon]


def _horizon_durable_name(horizon: AnalysisHorizon, subscription_index: int) -> str:
    return f"marketbot-{_service_name(horizon)}-market-v1-{subscription_index}"


def _alert_durable_name(input_name: str) -> str:
    return f"marketbot-alert-{input_name}-v1"


def _entry_watcher_subscription_options() -> SubscriptionOptions:
    return SubscriptionOptions(
        replay_latest_per_subject=True,
        ack_wait_seconds=60,
    )


def _write_ready(path: Path, summary: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def write_ready(path: Path, summary: Mapping[str, object]) -> None:
    """Atomically write a process readiness summary."""
    _write_ready(path, summary)
