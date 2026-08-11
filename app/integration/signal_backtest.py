"""Isolated in-memory backtesting for Core entry signals and Signal Fusion."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.alert_engine import AlertDispatcher
from app.alpaca_market_data import AlpacaEventNormalizer
from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport
from app.common.clock import FrozenClock
from app.common.settings import AppSettings
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    ENTRY_SIGNAL_EVENT,
    ENTRY_WATCH_TRANSITION_EVENT,
    FUSION_TRANSITION_EVENT,
    MARKET_BAR_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryOpportunityEvent,
    EntrySignal,
    EntryWatchTransition,
    EventEnvelope,
    EventHandler,
    FusionTransition,
    LocalAlert,
    MarketBar,
    entry_opportunity_subject,
    entry_watch_transition_subject,
    market_bar_subject,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.entry_watcher import EntryWatcherPolicy, InMemoryEntryWatchStore
from app.event_bus import InMemoryEventBus

from .alert_publisher import AlertEventPublisher
from .elliott_wave_composition import ElliottWaveRuntime
from .engine_assembly import MarketBotAssembly
from .entry_signal_adapter import entry_signal_from_alert_watch, publish_entry_signal
from .intraday_worker import IntradayWorker
from .long_term_worker import LongTermWorker
from .signal_fusion_composition import FUSION_SOURCE_SUBJECTS, SignalFusionRuntime
from .support_confirmation_composition import SupportConfirmationRuntime
from .swing_worker import SwingWorker
from .volume_structure_composition import VolumeStructureRuntime

_NEW_YORK = ZoneInfo("America/New_York")
_HISTORY = {
    BarTimeframe.DAY_1: (timedelta(days=800), 520),
    BarTimeframe.WEEK_1: (timedelta(days=365 * 8), 420),
    BarTimeframe.HOUR_1: (timedelta(days=90), 500),
    BarTimeframe.MINUTE_15: (timedelta(days=14), 160),
    BarTimeframe.MINUTE_1: (timedelta(days=5), 500),
}


class HistoricalBarsClient(Protocol):
    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]: ...


class _OpportunityBarEngine(Protocol):
    async def ingest_bar(self, bar: MarketBar) -> tuple[EntryOpportunityEvent, ...]: ...


@dataclass(frozen=True, slots=True)
class SignalBacktestConfig:
    source_date: date
    simulated_date: date
    symbols: tuple[str, ...]
    cadence_seconds: float = 0
    default_holding_quantity: Decimal = Decimal("1")
    output_path: Path = Path(".runtime/backtests/result.json")
    run_id: str = field(default_factory=lambda: f"backtest-{uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        symbols = tuple(
            dict.fromkeys(item.strip().upper() for item in self.symbols if item.strip())
        )
        if not symbols:
            raise ValueError("at least one backtest symbol is required")
        if self.source_date >= self.simulated_date:
            raise ValueError("source_date must be earlier than simulated_date")
        if (
            isinstance(self.cadence_seconds, bool)
            or not math.isfinite(self.cadence_seconds)
            or self.cadence_seconds < 0
        ):
            raise ValueError("cadence_seconds must be a finite non-negative number")
        quantity = Decimal(str(self.default_holding_quantity))
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("default_holding_quantity must be positive")
        if not self.run_id.strip():
            raise ValueError("run_id cannot be blank")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "default_holding_quantity", quantity)

    @property
    def holding_quantities(self) -> dict[str, Decimal]:
        return {symbol: self.default_holding_quantity for symbol in self.symbols}


@dataclass(frozen=True, slots=True)
class BacktestMarketData:
    warmup_bars: tuple[MarketBar, ...]
    session_bars: tuple[MarketBar, ...]


async def load_backtest_market_data(
    rest: HistoricalBarsClient,
    *,
    config: SignalBacktestConfig,
    feed: str,
) -> BacktestMarketData:
    """Read only selected symbols and exclude every observation unavailable at open."""

    source_open = _session_instant(config.source_date, time(9, 30))
    source_close = _session_instant(config.source_date, time(16))
    normalizer = AlpacaEventNormalizer(feed=f"{feed}-backtest")
    warmup: list[MarketBar] = []
    for timeframe, (lookback, max_bars) in _HISTORY.items():
        records = await rest.fetch_bars(
            config.symbols,
            timeframe=timeframe.value,
            start=source_open - lookback,
            end=source_open,
            limit=10_000,
        )
        for symbol in config.symbols:
            normalized = _normalize_records(
                records.get(symbol, ()),
                symbol=symbol,
                timeframe=timeframe,
                normalizer=normalizer,
            )
            eligible = [
                bar
                for bar in normalized
                if _available_before_open(bar, source_date=config.source_date)
            ]
            warmup.extend(eligible[-max_bars:])

    session_records = await rest.fetch_bars(
        config.symbols,
        timeframe=BarTimeframe.MINUTE_1.value,
        start=source_open,
        end=source_close,
        limit=10_000,
    )
    session: list[MarketBar] = []
    for symbol in config.symbols:
        session.extend(
            bar
            for bar in _normalize_records(
                session_records.get(symbol, ()),
                symbol=symbol,
                timeframe=BarTimeframe.MINUTE_1,
                normalizer=normalizer,
            )
            if source_open <= bar.timestamp < source_close
        )
    if not session:
        raise RuntimeError(
            f"no regular-session minute bars found for {config.source_date.isoformat()}"
        )
    return BacktestMarketData(
        warmup_bars=tuple(
            sorted(
                (_rebase_bar(bar, config=config) for bar in warmup),
                key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe.value),
            )
        ),
        session_bars=tuple(
            sorted(
                (_rebase_bar(bar, config=config) for bar in session),
                key=lambda bar: (bar.timestamp, bar.symbol),
            )
        ),
    )


async def replay_bars_at_cadence(
    bars: Iterable[MarketBar],
    *,
    cadence_seconds: float,
    handle: Callable[[MarketBar], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Deliver one timestamp group atomically, then apply the requested cadence."""

    ordered = tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol)))
    count = 0
    previous_timestamp: datetime | None = None
    for bar in ordered:
        if (
            previous_timestamp is not None
            and bar.timestamp != previous_timestamp
            and cadence_seconds > 0
        ):
            await sleep(cadence_seconds)
        await handle(bar)
        previous_timestamp = bar.timestamp
        count += 1
    return count


class _AlertRecorder:
    def __init__(self) -> None:
        self.alerts: list[LocalAlert] = []

    def emit(self, alert: LocalAlert) -> None:
        self.alerts.append(alert)


async def run_signal_backtest(
    config: SignalBacktestConfig,
    *,
    settings: AppSettings | None = None,
    rest: HistoricalBarsClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, object]:
    """Run the buy-signal pipeline with ephemeral state and no broker or DB transport."""

    resolved_settings = settings or AppSettings()
    owned_rest = rest is None
    rest_client = rest or _build_read_only_rest(resolved_settings)
    data = await load_backtest_market_data(
        rest_client,
        config=config,
        feed=resolved_settings.alpaca_data_feed,
    )
    target_open = _session_instant(config.simulated_date, time(9, 30))
    clock = FrozenClock(target_open)
    bus = InMemoryEventBus(retain_history=False, synchronous_delivery=True)
    assembly = MarketBotAssembly.from_settings(resolved_settings)
    watch_store = InMemoryEntryWatchStore()
    opportunity_store = InMemoryEntryOpportunityStore()
    watcher = assembly.build_entry_watcher(
        store=watch_store,
        policy=EntryWatcherPolicy(ttl=timedelta(days=resolved_settings.entry_watch_ttl_days)),
    )
    opportunity = assembly.build_entry_opportunity(store=opportunity_store)
    alert_engine = assembly.build_alert()
    alert_recorder = _AlertRecorder()
    dispatcher = AlertDispatcher(
        sinks=(alert_recorder,),
        publisher=AlertEventPublisher(bus),
    )
    long_worker = LongTermWorker(publisher=bus, analyzer=assembly.build_long_term())
    swing_worker = SwingWorker(publisher=bus, analyzer=assembly.build_swing())
    intraday_worker = IntradayWorker(publisher=bus, analyzer=assembly.build_intraday())
    for worker in (long_worker, swing_worker, intraday_worker):
        worker.activate_universe(config.symbols)
    support = SupportConfirmationRuntime(
        engine=assembly.build_support_confirmation(), publisher=bus, clock=clock
    )
    wave = ElliottWaveRuntime(
        engine=assembly.build_elliott_wave(), publisher=bus, clock=clock
    )
    fusion = SignalFusionRuntime(
        engine=assembly.build_signal_fusion(),
        publisher=bus,
        symbols=config.symbols,
        holding_quantities=config.holding_quantities,
    )
    volume_structure = VolumeStructureRuntime(
        engine=assembly.build_volume_structure(),
        publisher=bus,
    )
    signals: list[EntrySignal] = []
    volume_structure_results: list[AnalysisResult] = []
    fusion_transitions: list[FusionTransition] = []
    handler_errors: list[Exception] = []

    async def handle_analysis(envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = _payload(envelope, AnalysisResult)
        if result.horizon is AnalysisHorizon.VOLUME_STRUCTURE:
            volume_structure_results.append(result)
        await _publish_opportunity_events(
            bus, await opportunity.ingest_analysis(result, now=clock.now())
        )
        alert = alert_engine.ingest(result, now=clock.now())
        transition = await watcher.ingest(result, now=clock.now())
        if transition is not None:
            await bus.publish(
                entry_watch_transition_subject(transition.status, transition.symbol),
                EventEnvelope(
                    event_type=ENTRY_WATCH_TRANSITION_EVENT,
                    occurred_at=transition.occurred_at,
                    source="entry-watcher-backtest",
                    subject=transition.symbol,
                    payload=transition,
                ),
            )
        if alert is not None:
            await dispatcher.dispatch(alert)

    async def handle_watch(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_WATCH_TRANSITION_EVENT:
            return
        transition = _payload(envelope, EntryWatchTransition)
        await _publish_opportunity_events(
            bus, await opportunity.ingest_transition(transition)
        )
        await dispatcher.dispatch(
            alert_engine.ingest_entry_watch(transition, now=clock.now())
        )
        signal = entry_signal_from_alert_watch(transition)
        if signal is not None:
            await publish_entry_signal(bus, signal, source="alert-backtest")

    async def handle_signal(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_SIGNAL_EVENT:
            return
        signal = _payload(envelope, EntrySignal)
        signals.append(signal)
        if isinstance(opportunity, EntryOpportunityEngineV2):
            await _publish_opportunity_events(bus, await opportunity.ingest_signal(signal))

    async def handle_opportunity(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
            return
        event = _payload(envelope, EntryOpportunityEvent)
        alert = alert_engine.ingest_entry_opportunity(event, now=clock.now())
        await dispatcher.dispatch(alert)

    async def collect_fusion(envelope: EventEnvelope) -> None:
        if envelope.event_type == FUSION_TRANSITION_EVENT:
            fusion_transitions.append(_payload(envelope, FusionTransition))

    async def handle_bar(bar: MarketBar) -> None:
        delta = bar.timestamp - clock.now()
        if delta.total_seconds() > 0:
            clock.advance(delta)
        envelope = EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=bar.timestamp,
            source="market-backtest",
            subject=bar.symbol,
            payload=bar,
        )
        await bus.publish(market_bar_subject(bar.timeframe, bar.symbol), envelope)

    try:
        for subject in FUSION_SOURCE_SUBJECTS:
            await _subscribe_checked(bus, subject, fusion.handle_source, handler_errors)
        await _subscribe_checked(
            bus, "marketbot.v1.analysis.result.>", handle_analysis, handler_errors
        )
        await _subscribe_checked(
            bus, "marketbot.v1.entry-watch.transition.>", handle_watch, handler_errors
        )
        await _subscribe_checked(
            bus, "marketbot.v1.entry-signal.>", handle_signal, handler_errors
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.entry-opportunity.transition.>",
            handle_opportunity,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.signal-fusion.transition.>",
            collect_fusion,
            handler_errors,
        )
        for worker in (long_worker, swing_worker, intraday_worker):
            await _subscribe_checked(
                bus,
                "marketbot.v1.market.bar.1Min.>",
                worker.handle_market_event,
                handler_errors,
            )
        await _subscribe_checked(
            bus,
            "marketbot.v1.market.bar.1Min.>",
            _opportunity_bar_handler(cast("_OpportunityBarEngine", opportunity), bus),
            handler_errors,
        )

        await volume_structure.bootstrap(data.warmup_bars, symbols=config.symbols)
        await support.bootstrap(data.warmup_bars, symbols=config.symbols)
        await wave.bootstrap(data.warmup_bars, symbols=config.symbols)
        await long_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await swing_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await intraday_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await fusion.complete_hydration()
        bars_replayed = await replay_bars_at_cadence(
            data.session_bars,
            cadence_seconds=config.cadence_seconds,
            handle=handle_bar,
            sleep=sleep,
        )
        await _publish_opportunity_events(
            bus,
            await opportunity.reconcile(now=clock.now(), active_symbols=config.symbols),
        )
        await bus.join()
        if handler_errors:
            raise RuntimeError("backtest event handler failed") from handler_errors[0]
        report: dict[str, object] = {
            "mode": "backtest",
            "run_id": config.run_id,
            "source_date": config.source_date.isoformat(),
            "simulated_date": config.simulated_date.isoformat(),
            "symbols": list(config.symbols),
            "holding_quantities": {
                symbol: str(quantity)
                for symbol, quantity in config.holding_quantities.items()
            },
            "cadence_seconds": config.cadence_seconds,
            "bars_replayed": bars_replayed,
            "transport": "in-memory",
            "persistence": "in-memory",
            "operational_nats_used": False,
            "operational_database_used": False,
            "alerts": [item.model_dump(mode="json") for item in alert_recorder.alerts],
            "entry_signals": [item.model_dump(mode="json") for item in signals],
            "volume_structure_results": [
                item.model_dump(mode="json") for item in volume_structure_results
            ],
            "fusion_transitions": [
                item.model_dump(mode="json") for item in fusion_transitions
            ],
            "opportunities": [
                item.model_dump(mode="json")
                for item in opportunity_store.opportunities.values()
            ],
            "opportunity_events": [
                item.model_dump(mode="json") for item in opportunity_store.events
            ],
            "output_path": str(config.output_path.resolve()),
        }
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report
    finally:
        await bus.close()
        if owned_rest:
            await cast("AlpacaRestClient", rest_client).close()


def _opportunity_bar_handler(
    opportunity: _OpportunityBarEngine, bus: InMemoryEventBus
) -> Callable[[EventEnvelope], Awaitable[None]]:
    async def handle(envelope: EventEnvelope) -> None:
        bar = _payload(envelope, MarketBar)
        if bar.is_final and bar.timeframe is BarTimeframe.MINUTE_1:
            events = await opportunity.ingest_bar(bar)
            await _publish_opportunity_events(bus, events)

    return handle


async def _publish_opportunity_events(
    bus: InMemoryEventBus, events: tuple[EntryOpportunityEvent, ...]
) -> None:
    for event in events:
        await bus.publish(
            entry_opportunity_subject(
                event.opportunity.status, event.opportunity.symbol
            ),
            EventEnvelope(
                event_type=ENTRY_OPPORTUNITY_EVENT,
                occurred_at=event.occurred_at,
                source="entry-opportunity-backtest",
                subject=event.opportunity.symbol,
                payload=event,
            ),
        )


async def _subscribe_checked(
    bus: InMemoryEventBus,
    subject: str,
    handler: EventHandler,
    errors: list[Exception],
) -> None:
    async def checked(envelope: EventEnvelope) -> None:
        try:
            await handler(envelope)
        except Exception as error:
            errors.append(error)

    await bus.subscribe(subject, checked)


def _normalize_records(
    records: Iterable[Mapping[str, object]],
    *,
    symbol: str,
    timeframe: BarTimeframe,
    normalizer: AlpacaEventNormalizer,
) -> tuple[MarketBar, ...]:
    bars: list[MarketBar] = []
    for record in records:
        payload = normalizer.rest_bar(symbol, timeframe.value, record).envelope.payload
        if isinstance(payload, MarketBar):
            bars.append(payload)
    return tuple(sorted(bars, key=lambda bar: bar.timestamp))


def _available_before_open(bar: MarketBar, *, source_date: date) -> bool:
    local_date = bar.timestamp.astimezone(_NEW_YORK).date()
    if bar.timeframe is BarTimeframe.DAY_1:
        return local_date < source_date
    if bar.timeframe is BarTimeframe.WEEK_1:
        week_start = source_date - timedelta(days=source_date.weekday())
        return local_date < week_start
    return bar.timestamp < _session_instant(source_date, time(9, 30))


def _rebase_bar(bar: MarketBar, *, config: SignalBacktestConfig) -> MarketBar:
    local = bar.timestamp.astimezone(_NEW_YORK)
    target_date = local.date() + (config.simulated_date - config.source_date)
    rebased = datetime.combine(target_date, local.timetz()).astimezone(UTC)
    return bar.model_copy(
        update={
            "timestamp": rebased,
            "source": "alpaca-backtest",
            "feed": f"{bar.feed.removesuffix('-backtest')}-backtest",
        }
    )


def _session_instant(session_date: date, local_time: time) -> datetime:
    return datetime.combine(session_date, local_time, tzinfo=_NEW_YORK).astimezone(UTC)


def _payload[T](envelope: EventEnvelope, model: type[T]) -> T:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)  # type: ignore[attr-defined,no-any-return]


def _build_read_only_rest(settings: AppSettings) -> AlpacaRestClient:
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
