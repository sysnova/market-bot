"""Stateful in-memory backtesting for entry signals across one or more sessions."""

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

from app.alert_engine import AlertDispatcher, SolidBuyOutcome, evaluate_solid_buy_outcomes
from app.alpaca_market_data import AlpacaEventNormalizer
from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport
from app.common.clock import FrozenClock
from app.common.market_session import is_regular_analytical_bar
from app.common.settings import AppSettings
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    ENTRY_SIGNAL_EVENT,
    ENTRY_WATCH_TRANSITION_EVENT,
    FUSION_TRANSITION_EVENT,
    GERI_ASSESSMENT_EVENT,
    GERI_TRANSITION_EVENT,
    MARKET_BAR_EVENT,
    SWING_CHANNEL_ASSESSMENT_EVENT,
    SWING_CHANNEL_TRANSITION_EVENT,
    SWING_TRADE_ASSESSMENT_EVENT,
    SWING_TRADE_TRANSITION_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryOpportunityEvent,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchTransition,
    EventEnvelope,
    EventHandler,
    FusionTransition,
    GeriAssessment,
    GeriMaturity,
    GeriTransition,
    LocalAlert,
    MarketBar,
    SwingChannelAssessment,
    SwingChannelMaturity,
    SwingChannelTransition,
    SwingTradeAssessment,
    SwingTradeTransition,
    entry_opportunity_subject,
    entry_watch_transition_subject,
    market_bar_subject,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.entry_watcher import EntryWatcherPolicy, InMemoryEntryWatchStore
from app.event_bus import InMemoryEventBus

from .alert_publisher import AlertEventPublisher
from .confirmed_signal_projection import project_confirmed_signal
from .elliott_wave_composition import ElliottWaveRuntime
from .engine_assembly import MarketBotAssembly
from .entry_opportunity_report import build_entry_opportunity_report
from .entry_signal_adapter import entry_signal_from_alert_watch, publish_entry_signal
from .intraday_worker import IntradayWorker
from .long_term_worker import LongTermWorker
from .signal_fusion_composition import FUSION_SOURCE_SUBJECTS, SignalFusionRuntime
from .support_confirmation_composition import SupportConfirmationRuntime
from .swing_4h_geri_composition import Swing4HGeriRuntime
from .swing_channel_4h_composition import SwingChannel4HRuntime
from .swing_trade_composition import SwingTradeRuntime
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
    source_end_date: date | None = None
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
        source_end_date = self.source_end_date or self.source_date
        if source_end_date < self.source_date:
            raise ValueError("source_end_date cannot be earlier than source_date")
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
        object.__setattr__(self, "source_end_date", source_end_date)

    @property
    def holding_quantities(self) -> dict[str, Decimal]:
        return {symbol: self.default_holding_quantity for symbol in self.symbols}

    @property
    def simulated_end_date(self) -> date:
        assert self.source_end_date is not None
        return self.source_end_date + (self.simulated_date - self.source_date)


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
    assert config.source_end_date is not None
    source_close = _session_instant(config.source_end_date, time(16))
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
                if is_regular_analytical_bar(bar)
                and _available_before_open(bar, source_date=config.source_date)
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
            "no regular-session minute bars found for "
            f"{config.source_date.isoformat()} through {config.source_end_date.isoformat()}"
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
    flush: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Deliver and settle one timestamp group before advancing simulated time."""

    ordered = tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol)))
    count = 0
    previous_timestamp: datetime | None = None
    for bar in ordered:
        if previous_timestamp is not None and bar.timestamp != previous_timestamp:
            if flush is not None:
                await flush()
            if cadence_seconds > 0:
                await sleep(cadence_seconds)
        await handle(bar)
        previous_timestamp = bar.timestamp
        count += 1
    if previous_timestamp is not None and flush is not None:
        await flush()
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
    wave = ElliottWaveRuntime(engine=assembly.build_elliott_wave(), publisher=bus, clock=clock)
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
    swing_channel = SwingChannel4HRuntime(
        engine=assembly.build_swing_channel_4h(),
        publisher=bus,
        clock=clock,
    )
    geri = Swing4HGeriRuntime(
        engine=assembly.build_4hgeri(),
        publisher=bus,
        clock=clock,
        emit_countertrend_signals=True,
    )
    swing_trade = SwingTradeRuntime(
        engine=assembly.build_swing_trade(),
        publisher=bus,
        clock=clock,
    )
    signals: list[EntrySignal] = []
    swing_results: list[AnalysisResult] = []
    volume_structure_results: list[AnalysisResult] = []
    fusion_transitions: list[FusionTransition] = []
    swing_channel_assessments: list[SwingChannelAssessment] = []
    swing_channel_transitions: list[SwingChannelTransition] = []
    geri_assessments: list[GeriAssessment] = []
    geri_transitions: list[GeriTransition] = []
    swing_trade_assessments: list[SwingTradeAssessment] = []
    swing_trade_transitions: list[SwingTradeTransition] = []
    handler_errors: list[Exception] = []

    async def handle_analysis(envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = _payload(envelope, AnalysisResult)
        if result.horizon is AnalysisHorizon.SWING:
            swing_results.append(result)
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
        await _publish_opportunity_events(bus, await opportunity.ingest_transition(transition))
        await dispatcher.dispatch(alert_engine.ingest_entry_watch(transition, now=clock.now()))
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

    async def collect_swing_channel(envelope: EventEnvelope) -> None:
        if envelope.event_type == SWING_CHANNEL_ASSESSMENT_EVENT:
            swing_channel_assessments.append(_payload(envelope, SwingChannelAssessment))
        elif envelope.event_type == SWING_CHANNEL_TRANSITION_EVENT:
            swing_channel_transitions.append(_payload(envelope, SwingChannelTransition))

    async def collect_geri(envelope: EventEnvelope) -> None:
        if envelope.event_type == GERI_ASSESSMENT_EVENT:
            geri_assessments.append(_payload(envelope, GeriAssessment))
        elif envelope.event_type == GERI_TRANSITION_EVENT:
            geri_transitions.append(_payload(envelope, GeriTransition))

    async def collect_swing_trade(envelope: EventEnvelope) -> None:
        if envelope.event_type == SWING_TRADE_ASSESSMENT_EVENT:
            swing_trade_assessments.append(_payload(envelope, SwingTradeAssessment))
        elif envelope.event_type == SWING_TRADE_TRANSITION_EVENT:
            swing_trade_transitions.append(_payload(envelope, SwingTradeTransition))

    async def handle_bar(bar: MarketBar) -> None:
        delta = bar.timestamp - clock.now()
        if delta.total_seconds() > 0:
            clock.advance(delta)
        await _ingest_opportunity_then_publish_bar(
            cast("_OpportunityBarEngine", opportunity),
            bus,
            bar,
        )

    try:
        for subject in FUSION_SOURCE_SUBJECTS:
            await _subscribe_checked(bus, subject, fusion.handle_source, handler_errors)
        await _subscribe_checked(
            bus, "marketbot.v1.analysis.result.>", handle_analysis, handler_errors
        )
        await _subscribe_checked(
            bus, "marketbot.v1.entry-watch.transition.>", handle_watch, handler_errors
        )
        await _subscribe_checked(bus, "marketbot.v1.entry-signal.>", handle_signal, handler_errors)
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
        await _subscribe_checked(
            bus,
            "marketbot.v1.swing-channel-4h.assessment.>",
            collect_swing_channel,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.swing-channel-4h.transition.>",
            collect_swing_channel,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.analysis.result.SWING.>",
            swing_channel.handle_analysis,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.entry-opportunity.transition.>",
            swing_channel.handle_opportunity,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.market.bar.1Min.>",
            swing_channel.handle_market,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.4hgeri.assessment.>",
            collect_geri,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.4hgeri.assessment.>",
            swing_trade.restore_geri,
            handler_errors,
        )
        for handler in (
            swing_worker.handle_support_event,
            geri.restore_support,
            swing_trade.restore_support,
        ):
            await _subscribe_checked(
                bus,
                "marketbot.v1.support-confirmation.assessment.>",
                handler,
                handler_errors,
            )
        await _subscribe_checked(
            bus,
            "marketbot.v1.4hgeri.transition.>",
            collect_geri,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.swing-trade.assessment.>",
            collect_swing_trade,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.swing-trade.transition.>",
            collect_swing_trade,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.analysis.result.SWING.>",
            geri.handle_analysis,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.entry-opportunity.transition.>",
            geri.handle_opportunity,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.market.bar.1Min.>",
            geri.handle_market,
            handler_errors,
        )
        await _subscribe_checked(
            bus,
            "marketbot.v1.market.bar.1Min.>",
            swing_trade.handle_market,
            handler_errors,
        )
        for worker in (long_worker, swing_worker, intraday_worker):
            await _subscribe_checked(
                bus,
                "marketbot.v1.market.bar.1Min.>",
                worker.handle_market_event,
                handler_errors,
            )
        await volume_structure.bootstrap(data.warmup_bars, symbols=config.symbols)
        await support.bootstrap(data.warmup_bars, symbols=config.symbols)
        await wave.bootstrap(data.warmup_bars, symbols=config.symbols)
        await swing_channel.bootstrap(data.warmup_bars, symbols=config.symbols)
        await geri.bootstrap(data.warmup_bars, symbols=config.symbols)
        await swing_trade.bootstrap(data.warmup_bars, symbols=config.symbols)
        await long_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await swing_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await intraday_worker.bootstrap(data.warmup_bars, symbols=config.symbols)
        await fusion.complete_hydration()
        bars_replayed = await replay_bars_at_cadence(
            data.session_bars,
            cadence_seconds=config.cadence_seconds,
            handle=handle_bar,
            sleep=sleep,
            flush=bus.join,
        )
        await _publish_opportunity_events(
            bus,
            await opportunity.reconcile(now=clock.now(), active_symbols=config.symbols),
        )
        await bus.join()
        if handler_errors:
            raise RuntimeError("backtest event handler failed") from handler_errors[0]
        opportunities = tuple(opportunity_store.opportunities.values())
        minute_bars = _minute_bars_by_symbol(data.session_bars)
        solid_buy_outcomes = evaluate_solid_buy_outcomes(
            tuple(alert_recorder.alerts),
            minute_bars,
        )
        swing_channel_outcomes = _swing_channel_outcomes(
            tuple(swing_channel_transitions),
            minute_bars,
        )
        geri_outcomes = _geri_outcomes(tuple(geri_transitions), minute_bars)
        swing_trade_outcomes = _swing_trade_outcomes(tuple(swing_trade_transitions), minute_bars)
        confirmed_signals = _confirmed_entry_signals(tuple(signals))
        source_end_date = config.source_end_date
        if source_end_date is None:
            raise AssertionError("normalized backtest config requires source_end_date")
        report: dict[str, object] = {
            "mode": "backtest",
            "run_id": config.run_id,
            "marketbot_definition_version": assembly.definition.version,
            "marketbot_definition_source": str(assembly.definition.source),
            "source_date": config.source_date.isoformat(),
            "source_end_date": source_end_date.isoformat(),
            "simulated_date": config.simulated_date.isoformat(),
            "simulated_end_date": config.simulated_end_date.isoformat(),
            "symbols": list(config.symbols),
            "holding_quantities": {
                symbol: str(quantity) for symbol, quantity in config.holding_quantities.items()
            },
            "cadence_seconds": config.cadence_seconds,
            "bars_replayed": bars_replayed,
            "transport": "in-memory",
            "persistence": "in-memory",
            "operational_nats_used": False,
            "operational_database_used": False,
            "alerts": [item.model_dump(mode="json") for item in alert_recorder.alerts],
            "solid_buy_outcomes": [_solid_buy_outcome_payload(item) for item in solid_buy_outcomes],
            "entry_signals": [item.model_dump(mode="json") for item in signals],
            "confirmed_entry_signals": [item.model_dump(mode="json") for item in confirmed_signals],
            "confirmed_signal_counts": _confirmed_signal_counts(confirmed_signals),
            "swing_results": [item.model_dump(mode="json") for item in swing_results],
            "volume_structure_results": [
                item.model_dump(mode="json") for item in volume_structure_results
            ],
            "fusion_transitions": [item.model_dump(mode="json") for item in fusion_transitions],
            "swing_channel_4h_assessments": [
                item.model_dump(mode="json") for item in swing_channel_assessments
            ],
            "swing_channel_4h_transitions": [
                item.model_dump(mode="json") for item in swing_channel_transitions
            ],
            "swing_channel_4h_outcomes": swing_channel_outcomes,
            "swing_channel_4h_vs_swing": _swing_channel_comparisons(
                tuple(swing_channel_assessments)
            ),
            "4hgeri_assessments": [item.model_dump(mode="json") for item in geri_assessments],
            "4hgeri_transitions": [item.model_dump(mode="json") for item in geri_transitions],
            "4hgeri_outcomes": geri_outcomes,
            "swing_trade_assessments": [
                item.model_dump(mode="json") for item in swing_trade_assessments
            ],
            "swing_trade_transitions": [
                item.model_dump(mode="json") for item in swing_trade_transitions
            ],
            "swing_trade_outcomes": swing_trade_outcomes,
            "swing_trade_diagnostics": swing_trade.diagnostics(),
            "three_swing_model_comparison": _three_swing_model_comparison(
                tuple(swing_channel_assessments), tuple(geri_assessments)
            ),
            "four_swing_model_comparison": _four_swing_model_comparison(
                tuple(swing_results),
                tuple(swing_channel_assessments),
                tuple(geri_assessments),
                tuple(swing_trade_assessments),
            ),
            "swing_model_confirmation_summary": _swing_model_confirmation_summary(
                tuple(swing_results),
                tuple(swing_channel_assessments),
                tuple(geri_assessments),
                tuple(swing_trade_assessments),
                window_start=target_open,
                window_end=_session_instant(config.simulated_end_date, time(16)),
            ),
            "opportunities": [item.model_dump(mode="json") for item in opportunities],
            "opportunity_evidence_audit": build_entry_opportunity_report(opportunities)[
                "evidence_audit"
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


def _minute_bars_by_symbol(
    bars: tuple[MarketBar, ...],
) -> dict[str, tuple[MarketBar, ...]]:
    grouped: dict[str, list[MarketBar]] = {}
    for bar in bars:
        if bar.timeframe is BarTimeframe.MINUTE_1:
            grouped.setdefault(bar.symbol, []).append(bar)
    return {
        symbol: tuple(sorted(values, key=lambda item: item.timestamp))
        for symbol, values in grouped.items()
    }


def _swing_channel_outcomes(
    transitions: tuple[SwingChannelTransition, ...],
    bars_by_symbol: Mapping[str, tuple[MarketBar, ...]],
) -> list[dict[str, object]]:
    """Measure each actionable 4h reference against later bars in the same replay."""

    actionable = {
        SwingChannelMaturity.IN_ZONE_4H,
        SwingChannelMaturity.L2_4H,
        SwingChannelMaturity.L3,
        SwingChannelMaturity.L4,
    }
    outcomes: list[dict[str, object]] = []
    for transition in transitions:
        if transition.maturity not in actionable:
            continue
        future = tuple(
            bar
            for bar in bars_by_symbol.get(transition.symbol, ())
            if bar.timestamp >= transition.occurred_at
        )
        entry = transition.current_price
        outcomes.append(
            {
                "transition_id": str(transition.transition_id),
                "symbol": transition.symbol,
                "maturity": transition.maturity.value,
                "occurred_at": transition.occurred_at.isoformat(),
                "entry_price": str(entry),
                "support": str(transition.support),
                "invalidation": str(transition.invalidation),
                "observed_bars": len(future),
                "mfe_percent": _excursion_percent(future, entry=entry, favorable=True),
                "mae_percent": _excursion_percent(future, entry=entry, favorable=False),
                "return_15m": _forward_return(future, entry=entry, minutes=15),
                "return_30m": _forward_return(future, entry=entry, minutes=30),
                "return_60m": _forward_return(future, entry=entry, minutes=60),
                "return_close": (str(_percent(future[-1].close, entry)) if future else None),
            }
        )
    return outcomes


def _swing_channel_comparisons(
    assessments: tuple[SwingChannelAssessment, ...],
) -> list[dict[str, object]]:
    """Expose how much lower or higher the 4h support is than current Swing's zone."""

    comparisons: list[dict[str, object]] = []
    for item in assessments:
        swing_low = item.current_swing_zone_low
        swing_high = item.current_swing_zone_high
        swing_center = (
            (swing_low + swing_high) / Decimal("2")
            if swing_low is not None and swing_high is not None
            else None
        )
        comparisons.append(
            {
                "assessment_id": str(item.assessment_id),
                "symbol": item.symbol,
                "maturity": item.maturity.value,
                "occurred_at": item.occurred_at.isoformat(),
                "channel_zone_low": str(item.zone_low),
                "channel_zone_high": str(item.zone_high),
                "channel_support": str(item.support),
                "current_swing_zone_low": str(swing_low) if swing_low is not None else None,
                "current_swing_zone_high": (str(swing_high) if swing_high is not None else None),
                "zones_overlap": item.daily_swing_aligned,
                "channel_support_vs_swing_center_percent": (
                    str(_percent(item.support, swing_center)) if swing_center is not None else None
                ),
            }
        )
    return comparisons


def _geri_outcomes(
    transitions: tuple[GeriTransition, ...],
    bars_by_symbol: Mapping[str, tuple[MarketBar, ...]],
) -> list[dict[str, object]]:
    actionable = {
        GeriMaturity.IN_ZONE_4H,
        GeriMaturity.L2_4H,
        GeriMaturity.L3,
        GeriMaturity.L4,
    }
    outcomes: list[dict[str, object]] = []
    for transition in transitions:
        if transition.maturity not in actionable:
            continue
        future = tuple(
            bar
            for bar in bars_by_symbol.get(transition.symbol, ())
            if bar.timestamp >= transition.occurred_at
        )
        entry = transition.current_price
        outcomes.append(
            {
                "transition_id": str(transition.transition_id),
                "symbol": transition.symbol,
                "maturity": transition.maturity.value,
                "structural_level": transition.active_level_sequence,
                "occurred_at": transition.occurred_at.isoformat(),
                "entry_price": str(entry),
                "support": str(transition.active_level_price),
                "invalidation": (
                    str(transition.invalidation) if transition.invalidation is not None else None
                ),
                "observed_bars": len(future),
                "mfe_percent": _excursion_percent(future, entry=entry, favorable=True),
                "mae_percent": _excursion_percent(future, entry=entry, favorable=False),
                "return_15m": _forward_return(future, entry=entry, minutes=15),
                "return_30m": _forward_return(future, entry=entry, minutes=30),
                "return_60m": _forward_return(future, entry=entry, minutes=60),
                "return_close": (str(_percent(future[-1].close, entry)) if future else None),
            }
        )
    return outcomes


def _swing_trade_outcomes(
    transitions: tuple[SwingTradeTransition, ...],
    bars_by_symbol: Mapping[str, tuple[MarketBar, ...]],
) -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for transition in transitions:
        if transition.maturity is None:
            continue
        future = tuple(
            bar
            for bar in bars_by_symbol.get(transition.symbol, ())
            if bar.timestamp > transition.occurred_at
        )
        entry = transition.current_price
        first_level_hit = _first_level_hit(
            future,
            invalidation=transition.invalidation,
            target=transition.primary_target,
        )
        outcomes.append(
            {
                "transition_id": str(transition.transition_id),
                "symbol": transition.symbol,
                "maturity": transition.maturity.value,
                "occurred_at": transition.occurred_at.isoformat(),
                "entry_price": str(entry),
                "invalidation": str(transition.invalidation),
                "target": str(transition.primary_target),
                "reward_risk": str(transition.reward_risk),
                "first_level_hit": first_level_hit,
                "observed_bars": len(future),
                "mfe_percent": _excursion_percent(future, entry=entry, favorable=True),
                "mae_percent": _excursion_percent(future, entry=entry, favorable=False),
                "return_15m": _forward_return(future, entry=entry, minutes=15),
                "return_30m": _forward_return(future, entry=entry, minutes=30),
                "return_60m": _forward_return(future, entry=entry, minutes=60),
                "return_close": (str(_percent(future[-1].close, entry)) if future else None),
            }
        )
    return outcomes


def _confirmed_entry_signals(signals: tuple[EntrySignal, ...]) -> tuple[EntrySignal, ...]:
    output: list[EntrySignal] = []
    analytical_stages: dict[tuple[str, str], str] = {}
    for signal in signals:
        if signal.family in {
            EntrySignalFamily.SWING_TRADE,
            EntrySignalFamily.GERI_COUNTERTREND,
        }:
            key = (signal.family.value, signal.setup_id)
            stage = _signal_stage(signal)
            previous = analytical_stages.get(key)
            analytical_stages[key] = stage
            if previous == stage:
                continue
        if project_confirmed_signal(signal, color=False) is None:
            continue
        output.append(signal)
    return tuple(output)


def _confirmed_signal_counts(signals: tuple[EntrySignal, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in signals:
        key = f"{signal.family.value}:{_signal_stage(signal)}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _signal_stage(signal: EntrySignal) -> str:
    maturity = signal.countertrend_maturity or signal.swing_trade_maturity or signal.maturity
    return maturity.value if maturity is not None else "CONFIRMED"


def _first_level_hit(
    bars: tuple[MarketBar, ...],
    *,
    invalidation: Decimal,
    target: Decimal,
) -> str | None:
    for bar in bars:
        invalidated = bar.low <= invalidation
        targeted = bar.high >= target
        if invalidated and targeted:
            return "AMBIGUOUS_SAME_BAR"
        if invalidated:
            return "INVALIDATION"
        if targeted:
            return "TARGET"
    return None


def _three_swing_model_comparison(
    channels: tuple[SwingChannelAssessment, ...],
    geri_assessments: tuple[GeriAssessment, ...],
) -> list[dict[str, object]]:
    """Align 4HGERI support with the latest parallel channel and daily Swing zone."""

    comparisons: list[dict[str, object]] = []
    for geri in geri_assessments:
        if geri.zone_low is None or geri.zone_high is None:
            continue
        channel = next(
            (
                item
                for item in reversed(channels)
                if item.symbol == geri.symbol and item.occurred_at <= geri.occurred_at
            ),
            None,
        )
        swing_low = geri.current_swing_zone_low
        swing_high = geri.current_swing_zone_high
        swing_center = (
            (swing_low + swing_high) / Decimal("2")
            if swing_low is not None and swing_high is not None
            else None
        )
        comparisons.append(
            {
                "symbol": geri.symbol,
                "occurred_at": geri.occurred_at.isoformat(),
                "daily_swing_zone": (
                    [str(swing_low), str(swing_high)]
                    if swing_low is not None and swing_high is not None
                    else None
                ),
                "parallel_4h_zone": (
                    [str(channel.zone_low), str(channel.zone_high)] if channel is not None else None
                ),
                "4hgeri_zone": [str(geri.zone_low), str(geri.zone_high)],
                "4hgeri_structural_level": geri.active_level_sequence,
                "4hgeri_vs_daily_swing_center_percent": (
                    str(_percent(geri.active_level_price, swing_center))
                    if swing_center is not None
                    else None
                ),
                "4hgeri_vs_parallel_support_percent": (
                    str(_percent(geri.active_level_price, channel.support))
                    if channel is not None
                    else None
                ),
            }
        )
    return comparisons


def _four_swing_model_comparison(
    swing_results: tuple[AnalysisResult, ...],
    channels: tuple[SwingChannelAssessment, ...],
    geri_assessments: tuple[GeriAssessment, ...],
    swing_trade_assessments: tuple[SwingTradeAssessment, ...],
) -> list[dict[str, object]]:
    """Align the latest causal state of all four Swing models on one timeline."""

    keys = {
        *((item.symbol, item.as_of) for item in swing_results),
        *((item.symbol, item.occurred_at) for item in channels),
        *((item.symbol, item.occurred_at) for item in geri_assessments),
        *((item.symbol, item.occurred_at) for item in swing_trade_assessments),
    }
    comparisons: list[dict[str, object]] = []
    for symbol, occurred_at in sorted(keys, key=lambda item: (item[1], item[0])):
        swing = _latest_swing_result(swing_results, symbol=symbol, at=occurred_at)
        channel = _latest_channel(channels, symbol=symbol, at=occurred_at)
        geri = _latest_geri(geri_assessments, symbol=symbol, at=occurred_at)
        swing_trade = _latest_swing_trade(
            swing_trade_assessments,
            symbol=symbol,
            at=occurred_at,
        )
        swing_metrics = _metrics_by_name(swing) if swing is not None else {}
        comparisons.append(
            {
                "symbol": symbol,
                "occurred_at": occurred_at.isoformat(),
                "daily_swing_as_of": swing.as_of.isoformat() if swing is not None else None,
                "daily_swing_verdict": swing.verdict.value if swing is not None else None,
                "daily_swing_direction": swing.direction.value if swing is not None else None,
                "daily_swing_score": str(swing.score) if swing is not None else None,
                "daily_swing_entry_gate_passed": swing_metrics.get("swing_entry_gate_passed"),
                "daily_swing_entry_lane": swing_metrics.get("entry_lane"),
                "daily_swing_classification": swing_metrics.get("classification"),
                "daily_swing_reference_price": _string_value(swing_metrics.get("reference_price")),
                "daily_swing_zone": _optional_zone(
                    swing_metrics.get("entry_zone_low"),
                    swing_metrics.get("entry_zone_high"),
                ),
                "daily_swing_invalidation": _string_value(swing_metrics.get("invalidation")),
                "daily_swing_structural_invalidation": _string_value(
                    swing_metrics.get("structural_invalidation")
                ),
                "daily_swing_resistance": _string_value(swing_metrics.get("resistance")),
                "daily_swing_reward_risk": _string_value(
                    swing_metrics.get("reward_risk_to_resistance")
                ),
                "swing_channel_4h_as_of": (
                    channel.occurred_at.isoformat() if channel is not None else None
                ),
                "swing_channel_4h_maturity": (
                    channel.maturity.value if channel is not None else None
                ),
                "swing_channel_4h_zone": (
                    [str(channel.zone_low), str(channel.zone_high)] if channel is not None else None
                ),
                "swing_channel_4h_invalidation": (
                    str(channel.invalidation) if channel is not None else None
                ),
                "4hgeri_as_of": geri.occurred_at.isoformat() if geri is not None else None,
                "4hgeri_maturity": geri.maturity.value if geri is not None else None,
                "4hgeri_side": geri.trade_side.value if geri is not None else None,
                "4hgeri_zone": (
                    [str(geri.zone_low), str(geri.zone_high)]
                    if geri is not None and geri.zone_low is not None and geri.zone_high is not None
                    else None
                ),
                "4hgeri_invalidation": (
                    str(geri.invalidation)
                    if geri is not None and geri.invalidation is not None
                    else None
                ),
                "swing_trade_as_of": (
                    swing_trade.occurred_at.isoformat() if swing_trade is not None else None
                ),
                "swing_trade_maturity": (
                    swing_trade.maturity.value
                    if swing_trade is not None and swing_trade.maturity is not None
                    else None
                ),
                "swing_trade_eligible": (swing_trade.eligible if swing_trade is not None else None),
                "swing_trade_zone": (
                    [str(swing_trade.zone_low), str(swing_trade.zone_high)]
                    if swing_trade is not None
                    else None
                ),
                "swing_trade_invalidation": (
                    str(swing_trade.invalidation) if swing_trade is not None else None
                ),
                "swing_trade_primary_target": (
                    str(swing_trade.primary_target) if swing_trade is not None else None
                ),
                "swing_trade_reward_risk": (
                    str(swing_trade.reward_risk) if swing_trade is not None else None
                ),
            }
        )
    return comparisons


def _swing_model_confirmation_summary(
    swing_results: tuple[AnalysisResult, ...],
    channels: tuple[SwingChannelAssessment, ...],
    geri_assessments: tuple[GeriAssessment, ...],
    swing_trade_assessments: tuple[SwingTradeAssessment, ...],
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, object]:
    """Count actual maturity and daily entry gates without conflating their meanings."""

    daily = tuple(
        item
        for item in swing_results
        if item.engine_id == "swing" and _inside_window(item.as_of, window_start, window_end)
    )
    channels = tuple(
        item for item in channels if _inside_window(item.occurred_at, window_start, window_end)
    )
    geri_assessments = tuple(
        item
        for item in geri_assessments
        if _inside_window(item.occurred_at, window_start, window_end)
    )
    swing_trade_assessments = tuple(
        item
        for item in swing_trade_assessments
        if _inside_window(item.occurred_at, window_start, window_end)
    )
    gate_passed = tuple(
        item for item in daily if _metrics_by_name(item).get("swing_entry_gate_passed") is True
    )
    gate_failed = tuple(
        item for item in daily if _metrics_by_name(item).get("swing_entry_gate_passed") is not True
    )
    favorable = tuple(item for item in daily if item.verdict is AnalysisVerdict.FAVORABLE)
    confirmed = tuple(item for item in gate_passed if item.verdict is AnalysisVerdict.FAVORABLE)
    return {
        "swing_daily": {
            "assessment_count": len(daily),
            "session_count": len({item.as_of.astimezone(_NEW_YORK).date() for item in daily}),
            "verdict_counts": _value_counts(item.verdict.value for item in daily),
            "favorable_verdict_count": len(favorable),
            "entry_gate_passed_count": len(gate_passed),
            "confirmed_buy_count": len(confirmed),
            "entry_lane_counts": _value_counts(
                str(_metrics_by_name(item).get("entry_lane", "UNSPECIFIED")) for item in confirmed
            ),
            "gate_failure_reason_counts": _value_counts(
                _reason_code(reason) for item in gate_failed for reason in item.reasons
            ),
            "risk_flag_counts": _value_counts(
                flag
                for item in gate_failed
                for flag in _text_values(_metrics_by_name(item).get("risk_flags"))
            ),
            "confirmed_buys": [
                {
                    "as_of": item.as_of.isoformat(),
                    "reference_price": _string_value(_metrics_by_name(item).get("reference_price")),
                    "verdict": item.verdict.value,
                    "entry_lane": _string_value(_metrics_by_name(item).get("entry_lane")),
                    "invalidation": _string_value(_metrics_by_name(item).get("invalidation")),
                    "structural_invalidation": _string_value(
                        _metrics_by_name(item).get("structural_invalidation")
                    ),
                    "reward_risk_to_resistance": _string_value(
                        _metrics_by_name(item).get("reward_risk_to_resistance")
                    ),
                }
                for item in confirmed
            ],
        },
        "swing_channel_4h": {
            "assessment_count": len(channels),
            "maturity_counts": _value_counts(item.maturity.value for item in channels),
        },
        "4hgeri": {
            "assessment_count": len(geri_assessments),
            "maturity_counts": _value_counts(item.maturity.value for item in geri_assessments),
        },
        "swing_trade": {
            "assessment_count": len(swing_trade_assessments),
            "maturity_counts": _value_counts(
                item.maturity.value if item.maturity is not None else "NONE"
                for item in swing_trade_assessments
            ),
            "eligible_count": sum(item.eligible for item in swing_trade_assessments),
        },
    }


def _inside_window(
    occurred_at: datetime,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    return (window_start is None or occurred_at >= window_start) and (
        window_end is None or occurred_at < window_end
    )


def _latest_swing_result(
    items: tuple[AnalysisResult, ...], *, symbol: str, at: datetime
) -> AnalysisResult | None:
    eligible = (item for item in items if item.symbol == symbol and item.as_of <= at)
    return max(eligible, key=lambda item: item.as_of, default=None)


def _latest_channel(
    items: tuple[SwingChannelAssessment, ...], *, symbol: str, at: datetime
) -> SwingChannelAssessment | None:
    eligible = (item for item in items if item.symbol == symbol and item.occurred_at <= at)
    return max(eligible, key=lambda item: item.occurred_at, default=None)


def _latest_geri(
    items: tuple[GeriAssessment, ...], *, symbol: str, at: datetime
) -> GeriAssessment | None:
    eligible = (item for item in items if item.symbol == symbol and item.occurred_at <= at)
    return max(eligible, key=lambda item: item.occurred_at, default=None)


def _latest_swing_trade(
    items: tuple[SwingTradeAssessment, ...], *, symbol: str, at: datetime
) -> SwingTradeAssessment | None:
    eligible = (item for item in items if item.symbol == symbol and item.occurred_at <= at)
    return max(eligible, key=lambda item: item.occurred_at, default=None)


def _metrics_by_name(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _string_value(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_zone(low: object, high: object) -> list[str] | None:
    if low is None or high is None:
        return None
    return [str(low), str(high)]


def _value_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast("list[object] | tuple[object, ...]", value)
    return tuple(item for item in values if isinstance(item, str))


def _reason_code(reason: str) -> str:
    return reason.partition(":")[0]


def _excursion_percent(
    bars: tuple[MarketBar, ...], *, entry: Decimal, favorable: bool
) -> str | None:
    if not bars:
        return None
    price = max(bar.high for bar in bars) if favorable else min(bar.low for bar in bars)
    return str(_percent(price, entry))


def _forward_return(bars: tuple[MarketBar, ...], *, entry: Decimal, minutes: int) -> str | None:
    if not bars:
        return None
    target = bars[0].timestamp + timedelta(minutes=minutes - 1)
    bar = next((item for item in bars if item.timestamp >= target), None)
    return str(_percent(bar.close, entry)) if bar is not None else None


def _percent(price: Decimal, reference: Decimal) -> Decimal:
    return ((price / reference) - Decimal("1")) * Decimal("100")


def _solid_buy_outcome_payload(outcome: SolidBuyOutcome) -> dict[str, object]:
    return {
        "alert_id": str(outcome.alert_id),
        "symbol": outcome.symbol,
        "alert_kind": outcome.alert_kind,
        "occurred_at": outcome.occurred_at.isoformat(),
        "entry_price": str(outcome.entry_price),
        "invalidation": str(outcome.invalidation) if outcome.invalidation is not None else None,
        "target": str(outcome.target) if outcome.target is not None else None,
        "first_level_hit": outcome.first_level_hit,
        "mfe_percent": (str(outcome.mfe_percent) if outcome.mfe_percent is not None else None),
        "mae_percent": (str(outcome.mae_percent) if outcome.mae_percent is not None else None),
        "return_15m": (str(outcome.return_15m) if outcome.return_15m is not None else None),
        "return_30m": (str(outcome.return_30m) if outcome.return_30m is not None else None),
        "return_60m": (str(outcome.return_60m) if outcome.return_60m is not None else None),
        "return_close": (str(outcome.return_close) if outcome.return_close is not None else None),
        "evaluated_through": (
            outcome.evaluated_through.isoformat() if outcome.evaluated_through is not None else None
        ),
        "engine_versions": list(outcome.engine_versions),
        "entry_confirmation_rule_versions": list(outcome.entry_confirmation_rule_versions),
    }


async def _ingest_opportunity_then_publish_bar(
    opportunity: _OpportunityBarEngine,
    bus: InMemoryEventBus,
    bar: MarketBar,
) -> None:
    """Advance paper evidence before analytical fan-out can replace its snapshot."""

    if bar.is_final and bar.timeframe is BarTimeframe.MINUTE_1:
        await _publish_opportunity_events(bus, await opportunity.ingest_bar(bar))
    await bus.publish(
        market_bar_subject(bar.timeframe, bar.symbol),
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=bar.timestamp,
            source="market-backtest",
            subject=bar.symbol,
            payload=bar,
        ),
    )


async def _publish_opportunity_events(
    bus: InMemoryEventBus, events: tuple[EntryOpportunityEvent, ...]
) -> None:
    for event in events:
        await bus.publish(
            entry_opportunity_subject(event.opportunity.status, event.opportunity.symbol),
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
