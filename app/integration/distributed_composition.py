"""Composition roots for independent market, horizon, and alert processes."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.alert_engine import AlertDispatcher, AlertEngineV2, ConsoleAlertSink, NdjsonAlertSink
from app.alpaca_market_data import AlpacaEventNormalizer, AlpacaMarketDataEngine
from app.alpaca_market_data.ports import EventPublisher, MarketDataRest
from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport, WebsocketsConnector
from app.alpaca_market_data.websocket import AlpacaMarketDataStream
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_WATCH_TRANSITION_EVENT,
    SERVICE_HEALTH_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryWatchTransition,
    EventEnvelope,
    MarketBar,
    NamedValue,
    ServiceHealth,
    ServiceStatus,
    Subscription,
    SubscriptionOptions,
    entry_watch_transition_subject,
    service_health_subject,
)
from app.entry_watcher import EntryWatcherPolicy, EntryWatcherV2
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .alert_publisher import AlertEventPublisher
from .entry_watch_store import PostgresEntryWatchStore
from .intraday_worker import IntradayWorker
from .long_term_worker import LongTermWorker
from .postgres_universe import (
    PostgresUniverseClient,
    UniverseSnapshot,
    fallback_universe,
)
from .swing_worker import SwingWorker

_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    timeframe: BarTimeframe
    lookback: timedelta
    max_bars_per_symbol: int


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
            HistoryRequest(BarTimeframe.DAY_1, timedelta(days=400), 260),
            HistoryRequest(BarTimeframe.WEEK_1, timedelta(days=365 * 5), 220),
        ),
        AnalysisHorizon.SWING: (
            HistoryRequest(BarTimeframe.DAY_1, timedelta(days=220), 120),
            HistoryRequest(BarTimeframe.MINUTE_15, timedelta(days=14), 160),
        ),
        AnalysisHorizon.INTRADAY: (
            HistoryRequest(BarTimeframe.MINUTE_1, timedelta(days=5), 500),
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
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger(f"{horizon.value.lower()}-worker")
    clock = SystemClock()
    http_client = httpx.AsyncClient()
    bus: NatsJetStreamEventBus | None = None
    rest: MarketDataRest | None = None
    subscriptions: list[Subscription] = []
    try:
        universe = await _resolve_universe(settings, symbols)
        bus = await _connect_nats(settings)
        rest = _build_rest(settings)
        normalizer = AlpacaEventNormalizer(feed=settings.alpaca_data_feed)
        as_of = clock.now()
        bars = await _load_history(
            rest,
            normalizer,
            universe.symbols,
            engine_history_requests(horizon),
            as_of=as_of,
            batch_size=settings.alpaca_rest_batch_size,
        )
        worker = _build_worker(horizon, bus)
        result_count = await worker.bootstrap(bars, symbols=universe.symbols)
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
        summary: dict[str, object] = {
            "service": _service_name(horizon),
            "horizon": horizon.value,
            "symbols": len(universe.symbols),
            "historical_bars": len(bars),
            "initial_results": result_count,
            "universe_source": universe.source,
            "live_subjects": list(engine_live_subjects(horizon)),
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
        if rest is not None:
            await rest.close()
        await http_client.aclose()
        if bus is not None:
            await bus.close()


async def run_market_stream_process(
    *,
    symbols: tuple[str, ...] | None = None,
) -> None:
    """Publish Alpaca WebSocket updates to NATS; this process runs no analysis engine."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("alpaca-live-stream")
    clock = SystemClock()
    http_client = httpx.AsyncClient()
    bus: NatsJetStreamEventBus | None = None
    engine: AlpacaMarketDataEngine | None = None
    try:
        universe = await _resolve_universe(settings, symbols)
        bus = await _connect_nats(settings)
        engine = _build_stream_engine(settings, bus)
        await _publish_health(
            bus,
            "alpaca-market-stream",
            {"symbols": len(universe.symbols), "universe_source": universe.source},
            clock.now(),
        )
        backoff = 1.0
        while True:
            try:
                count = await engine.stream_once(universe.symbols)
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
        if engine is not None:
            await engine.close()
        await http_client.aclose()
        if bus is not None:
            await bus.close()


async def run_alert_process(
    *,
    runtime_root: Path,
    bell: bool,
    ready_path: Path | None = None,
) -> None:
    """Consume every engine result and publish named human alerts for viewers."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    clock = SystemClock()
    bus = await _connect_nats(settings)
    engine = AlertEngineV2()
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
    )
    try:
        details = {
            "analysis_subject": "marketbot.v1.analysis.result.>",
            "entry_watch_subject": "marketbot.v1.entry-watch.transition.>",
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
    """Persist entry theses and publish transitions as an independent service."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    if not settings.entry_watcher_enabled:
        raise RuntimeError("entry watcher is disabled by configuration")
    clock = SystemClock()
    database: AsyncEngine = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    subscription: Subscription | None = None
    try:
        store = PostgresEntryWatchStore(create_session_factory(database))
        if not await store.is_ready():
            raise RuntimeError(
                "entry watcher schema is unavailable; apply "
                "20260726180000_entry_watches.sql"
            )
        watcher = EntryWatcherV2(
            store=store,
            policy=EntryWatcherPolicy(
                ttl=timedelta(days=settings.entry_watch_ttl_days)
            ),
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
            transition = await watcher.ingest(result, now=clock.now())
            if transition is not None:
                await bus.publish(
                    entry_watch_transition_subject(
                        transition.status, transition.symbol
                    ),
                    EventEnvelope(
                        event_type=ENTRY_WATCH_TRANSITION_EVENT,
                        occurred_at=transition.occurred_at,
                        source="entry-watcher-v2",
                        subject=transition.symbol,
                        payload=transition,
                    ),
                )

        subscription = await bus.subscribe(
            "marketbot.v1.analysis.result.>",
            handle_analysis,
            options=SubscriptionOptions(
                durable_name="marketbot-entry-watcher-v2",
                replay_all=False,
                ack_wait_seconds=60,
            ),
        )
        details = {
            "input_subject": "marketbot.v1.analysis.result.>",
            "output_subject": "marketbot.v1.entry-watch.transition.>",
            "persistence": "postgresql",
        }
        await _publish_health(bus, "entry-watcher-v2", details, clock.now())
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        if subscription is not None:
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


async def _connect_nats(settings: AppSettings) -> NatsJetStreamEventBus:
    return await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )


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
        rest=_build_rest(settings),
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


async def _load_history(
    rest: MarketDataRest,
    normalizer: AlpacaEventNormalizer,
    symbols: tuple[str, ...],
    requests: tuple[HistoryRequest, ...],
    *,
    as_of: datetime,
    batch_size: int,
) -> tuple[MarketBar, ...]:
    output: list[MarketBar] = []
    for request in requests:
        for batch in _batches(symbols, batch_size):
            raw = await rest.fetch_bars(
                batch,
                timeframe=request.timeframe.value,
                start=as_of - request.lookback,
                end=as_of,
                limit=10_000,
            )
            for symbol in batch:
                records = raw.get(symbol, [])[-request.max_bars_per_symbol :]
                for record in records:
                    payload = normalizer.rest_bar(
                        symbol, request.timeframe.value, record
                    ).envelope.payload
                    if not isinstance(payload, MarketBar):
                        raise TypeError("normalized REST bar did not produce MarketBar")
                    if (
                        payload.timeframe is BarTimeframe.WEEK_1
                        and not _weekly_bar_is_complete(payload.timestamp, as_of)
                    ):
                        continue
                    output.append(payload)
    return tuple(output)


def _build_worker(
    horizon: AnalysisHorizon,
    publisher: EventPublisher,
) -> HorizonWorker:
    if horizon is AnalysisHorizon.LONG_TERM:
        return LongTermWorker(publisher=publisher)
    if horizon is AnalysisHorizon.SWING:
        return SwingWorker(publisher=publisher)
    if horizon is AnalysisHorizon.INTRADAY:
        return IntradayWorker(publisher=publisher)
    raise ValueError("unsupported distributed horizon")


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
        details=tuple(
            NamedValue(name=key, value=value)
            for key, value in sorted(details.items())
        ),
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


def _batches(symbols: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    return tuple(normalized[index : index + size] for index in range(0, len(normalized), size))


def _weekly_bar_is_complete(timestamp: datetime, as_of: datetime) -> bool:
    local_date = timestamp.astimezone(_NEW_YORK).date()
    week_start = local_date - timedelta(days=local_date.weekday())
    completion = datetime.combine(week_start + timedelta(days=5), time(), _NEW_YORK)
    return as_of.astimezone(_NEW_YORK) >= completion


def _write_ready(path: Path, summary: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
