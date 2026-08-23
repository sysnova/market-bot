"""Independent runtime for horizontal-level 4HGERI observations."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

from app.common.clock import Clock, SystemClock
from app.common.market_session import is_regular_session, is_regular_session_close_minute
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    ENTRY_SIGNAL_EVENT,
    GERI_ASSESSMENT_EVENT,
    GERI_TRANSITION_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryMaturityLevel,
    EntryOpportunityEvent,
    EntrySignal,
    EntrySignalFamily,
    EventEnvelope,
    GeriAssessment,
    GeriCountertrendMaturity,
    GeriMaturity,
    GeriTransition,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    TradeSide,
    entry_signal_subject,
    geri_assessment_subject,
    geri_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.swing_4h_geri_engine import Swing4HGeriContext

from .bar_aggregator import MinuteBarAggregator, RegularSessionFourHourAggregator
from .distributed_composition import HistoryRequest, connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .postgres_universe import PostgresUniverseClient
from .universe_policy import universe_health_details

GERI_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.MINUTE_15,
        lookback=timedelta(days=35),
        max_bars_per_symbol=600,
    ),
)


class GeriEngine(Protocol):
    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment: ...


class GeriPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class Swing4HGeriRuntime:
    """Aggregate RTH bars and publish only the independent 4HGERI stream."""

    def __init__(
        self,
        *,
        engine: GeriEngine,
        publisher: GeriPublisher,
        clock: Clock | None = None,
        emit_countertrend_signals: bool = False,
    ) -> None:
        self._engine = engine
        self._standalone = getattr(engine, "engine_version", "") in {
            "1.2.0",
            "1.3.0",
            "1.4.0",
            "1.5.0",
        }
        self._publisher = publisher
        self._clock = clock or SystemClock()
        self._emit_countertrend_signals = emit_countertrend_signals
        self._bars = MarketBarStore(capacity_per_series=80)
        self._minute = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,))
        self._four_hour = RegularSessionFourHourAggregator()
        self._symbols: set[str] = set()
        self._prices: dict[str, Decimal] = {}
        self._price_at: dict[str, datetime] = {}
        self._daily_swing: dict[str, AnalysisResult] = {}
        self._existing_maturity: dict[str, EntryMaturityLevel] = {}
        self._opportunity_at: dict[str, datetime] = {}
        self._latest: dict[str, GeriAssessment] = {}
        self._support: dict[str, SupportAssessment] = {}
        self._last_countertrend_signal: dict[
            str, tuple[str, GeriCountertrendMaturity | None, tuple[str, ...]]
        ] = {}

    async def restore_assessment(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != GERI_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, GeriAssessment)
            else GeriAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._latest.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._latest[item.symbol] = item

    async def restore_support(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, SupportAssessment)
            else SupportAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._support.get(item.symbol)
        if previous is not None and item.occurred_at < previous.occurred_at:
            return
        self._support[item.symbol] = item
        if item.symbol in self._symbols:
            await self.evaluate(item.symbol)

    async def bootstrap(self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]) -> int:
        self._symbols = {item.strip().upper() for item in symbols if item.strip()}
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            if bar.symbol not in self._symbols or not bar.is_final:
                continue
            if bar.timeframe is BarTimeframe.HOUR_4:
                self._bars.add(bar)
                self._prices[bar.symbol] = bar.close
                self._price_at[bar.symbol] = bar.timestamp
            elif bar.timeframe is BarTimeframe.MINUTE_15 and is_regular_session(bar.timestamp):
                self._bars.add(bar)
                self._prices[bar.symbol] = bar.close
                self._price_at[bar.symbol] = bar.timestamp + timedelta(minutes=15)
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
            self._price_at[bar.symbol] = bar.timestamp
            await self.evaluate(
                bar.symbol,
                market_at=bar.timestamp if is_regular_session(bar.timestamp) else None,
            )
            return
        if bar.timeframe is BarTimeframe.MINUTE_15:
            if is_regular_session(bar.timestamp):
                await self._accept_fifteen(bar)
            return
        if bar.timeframe is not BarTimeframe.MINUTE_1:
            return
        aggregated = self._minute.add(bar)
        if is_regular_session(bar.timestamp):
            self._prices[bar.symbol] = bar.close
            self._price_at[bar.symbol] = bar.timestamp
            for fifteen in aggregated:
                if is_regular_session(fifteen.timestamp):
                    await self._accept_fifteen(fifteen, evaluate=False)
            await self.evaluate(
                bar.symbol,
                current_price=bar.close,
                market_at=bar.timestamp,
            )
            return

    async def handle_analysis(self, envelope: EventEnvelope) -> None:
        if self._standalone:
            return
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
        if self._standalone:
            return
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

    async def evaluate(
        self,
        symbol: str,
        *,
        current_price: Decimal | None = None,
        market_at: datetime | None = None,
    ) -> bool:
        normalized = symbol.strip().upper()
        bars = self._bars.history(normalized, BarTimeframe.HOUR_4, limit=60, final_only=True)
        value = current_price if current_price is not None else self._prices.get(normalized)
        price_at = self._price_at.get(normalized)
        if value is None or price_at is None or not bars:
            return False
        try:
            assessment = self._engine.analyze(
                Swing4HGeriContext(
                    symbol=normalized,
                    bars=bars,
                    current_price=value,
                    confirmation_bars=self._bars.history(
                        normalized,
                        BarTimeframe.MINUTE_15,
                        limit=32,
                        final_only=True,
                    ),
                    daily_swing=self._daily_swing.get(normalized),
                    existing_maturity=self._existing_maturity.get(normalized),
                    active_structure=self._latest.get(normalized),
                    support=self._support.get(normalized),
                    as_of=self._clock.now(),
                    current_price_at=price_at,
                )
            )
        except ValueError:
            return False
        assessment = assessment.model_copy(update={"assessed_at": self._clock.now()})
        previous = self._latest.get(normalized)
        if previous is not None and _same_observation(previous, assessment):
            return await self._publish_countertrend_signal(assessment, market_at=market_at)
        self._latest[normalized] = assessment
        await self._publish_assessment(assessment)
        await self._publish_countertrend_signal(
            assessment,
            market_at=market_at,
        )
        if previous is None or _material_transition(previous, assessment):
            await self._publish_transition(assessment, previous)
        return True

    async def _accept_fifteen(self, bar: MarketBar, *, evaluate: bool = True) -> None:
        self._bars.add(bar)
        self._prices[bar.symbol] = bar.close
        self._price_at[bar.symbol] = bar.timestamp + timedelta(minutes=15)
        for aggregated in self._four_hour.add(bar):
            self._bars.add(aggregated)
        if evaluate:
            await self.evaluate(bar.symbol, current_price=bar.close)

    async def _publish_assessment(self, item: GeriAssessment) -> None:
        await self._publisher.publish(
            geri_assessment_subject(item.symbol),
            EventEnvelope(
                event_type=GERI_ASSESSMENT_EVENT,
                occurred_at=item.assessed_at or item.occurred_at,
                source="4hgeri-v1",
                subject=item.symbol,
                payload=item,
            ),
        )

    async def _publish_countertrend_signal(
        self,
        item: GeriAssessment,
        *,
        market_at: datetime | None,
    ) -> bool:
        if (
            not self._emit_countertrend_signals
            or market_at is None
            or not is_regular_session(market_at)
        ):
            return False
        signal = _countertrend_signal(item)
        if signal is None:
            return False
        reasons = signal.reasons
        if is_regular_session_close_minute(market_at):
            reasons = (*reasons, "regular_session_close")
        signal = signal.model_copy(
            update={
                "created_at": market_at,
                "reasons": reasons,
            }
        )
        signature = (signal.setup_id, signal.countertrend_maturity, signal.reasons)
        if self._last_countertrend_signal.get(signal.symbol) == signature:
            return False
        self._last_countertrend_signal[signal.symbol] = signature
        await self._publisher.publish(
            entry_signal_subject(signal.family, signal.symbol),
            EventEnvelope(
                event_type=ENTRY_SIGNAL_EVENT,
                occurred_at=signal.created_at,
                source="4hgeri-v1",
                subject=signal.symbol,
                payload=signal,
            ),
        )
        return True

    async def _publish_transition(
        self, item: GeriAssessment, previous: GeriAssessment | None
    ) -> None:
        transition = GeriTransition(
            assessment_id=item.assessment_id,
            symbol=item.symbol,
            occurred_at=item.assessed_at or item.occurred_at,
            engine_version=item.engine_version,
            previous_maturity=previous.maturity if previous is not None else None,
            maturity=item.maturity,
            active_level_sequence=item.active_level_sequence,
            active_level_kind=item.active_level_kind,
            active_level_price=item.active_level_price,
            current_price=item.current_price,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            invalidation=item.invalidation,
            trade_side=item.trade_side,
            standalone_swing=item.standalone_swing,
            reasons=item.reasons,
            context_hash=item.context_hash,
        )
        await self._publisher.publish(
            geri_transition_subject(transition.maturity, transition.symbol),
            EventEnvelope(
                event_type=GERI_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="4hgeri-v1",
                subject=transition.symbol,
                payload=transition,
            ),
        )


def _same_observation(previous: GeriAssessment, current: GeriAssessment) -> bool:
    return (
        previous.engine_version == current.engine_version
        and previous.maturity is current.maturity
        and previous.active_level_sequence == current.active_level_sequence
        and previous.active_level_kind is current.active_level_kind
        and previous.active_level_price == current.active_level_price
        and previous.zone_low == current.zone_low
        and previous.zone_high == current.zone_high
        and previous.daily_swing_aligned is current.daily_swing_aligned
        and previous.existing_maturity_aligned is current.existing_maturity_aligned
        and previous.trade_side is current.trade_side
        and previous.fast_confirmation is current.fast_confirmation
        and previous.four_hour_confirmation is current.four_hour_confirmation
        and previous.continuation_confirmation is current.continuation_confirmation
        and _countertrend_observation(previous) == _countertrend_observation(current)
    )


def _countertrend_observation(item: GeriAssessment) -> tuple[tuple[str, object], ...]:
    material_names = {
        "countertrend_side",
        "countertrend_state",
        "countertrend_level_kind",
        "countertrend_level_price",
        "countertrend_level_source_at",
        "countertrend_eligible",
        "countertrend_expired",
        "countertrend_fast_confirmation",
        "countertrend_four_hour_confirmation",
        "countertrend_continuation_confirmation",
        "support_assessment_id",
        "support_contribution",
        "support_state",
        "support_zone_match",
    }
    return tuple(
        (metric.name, metric.value) for metric in item.metrics if metric.name in material_names
    )


def _countertrend_signal(item: GeriAssessment) -> EntrySignal | None:
    metrics = {metric.name: metric.value for metric in item.metrics}
    side = metrics.get("countertrend_side")
    if getattr(side, "value", side) != TradeSide.LONG.value:
        return None
    required = (
        "countertrend_state",
        "countertrend_level_source_at",
        "countertrend_zone_low",
        "countertrend_zone_high",
        "countertrend_invalidation",
        "countertrend_target",
    )
    if any(metrics.get(name) is None for name in required):
        return None
    state_value = getattr(metrics["countertrend_state"], "value", metrics["countertrend_state"])
    maturity = {
        GeriMaturity.ARMED.value: GeriCountertrendMaturity.CT0,
        GeriMaturity.IN_ZONE_4H.value: GeriCountertrendMaturity.CT1,
        GeriMaturity.L2_4H.value: GeriCountertrendMaturity.CT2,
        GeriMaturity.L3.value: GeriCountertrendMaturity.CT3,
        GeriMaturity.L4.value: GeriCountertrendMaturity.CT4,
    }.get(str(state_value))
    source_at = metrics["countertrend_level_source_at"]
    if not isinstance(source_at, datetime):
        source_at = datetime.fromisoformat(str(source_at).replace("Z", "+00:00"))
    reasons = _countertrend_signal_reasons(
        metrics, state_value, maturity, current_price=item.current_price
    )
    return EntrySignal(
        family=EntrySignalFamily.GERI_COUNTERTREND,
        countertrend_maturity=maturity,
        symbol=item.symbol,
        created_at=item.assessed_at or item.occurred_at,
        setup_id=(f"geri-countertrend:{item.symbol}:{source_at.isoformat()}:{item.engine_version}"),
        entry_price=item.current_price,
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal(str(metrics["countertrend_zone_low"])),
        zone_high=Decimal(str(metrics["countertrend_zone_high"])),
        invalidation=Decimal(str(metrics["countertrend_invalidation"])),
        targets=(Decimal(str(metrics["countertrend_target"])),),
        policy_id="geri-countertrend",
        policy_version=item.engine_version,
        reasons=reasons,
        source_event_ids=(item.assessment_id,),
    )


def _countertrend_signal_reasons(
    metrics: dict[str, object],
    state: object,
    maturity: GeriCountertrendMaturity | None,
    *,
    current_price: Decimal,
) -> tuple[str, ...]:
    if bool(metrics.get("countertrend_expired")):
        return ("countertrend_expired",)
    state_value = str(state)
    if state_value == GeriMaturity.INVALIDATED.value:
        return ("countertrend_invalidated",)
    target = Decimal(str(metrics["countertrend_target"]))
    side = getattr(metrics.get("countertrend_side"), "value", metrics.get("countertrend_side"))
    if side == TradeSide.LONG.value and current_price >= target:
        return ("countertrend_target_reached",)
    if maturity is not None:
        support_reason = _support_signal_reason(metrics, zone="TACTICAL")
        return tuple(
            reason
            for reason in (f"countertrend_{maturity.value.lower()}", support_reason)
            if reason is not None
        )
    if state_value == GeriMaturity.RECLAIM_REQUIRED.value:
        return ("countertrend_reclaim_required",)
    if state_value == GeriMaturity.EXTENDED.value:
        return ("countertrend_extended",)
    eligibility = metrics.get("countertrend_eligibility_reasons", ())
    if isinstance(eligibility, tuple):
        eligibility_values = cast(tuple[object, ...], eligibility)
    elif isinstance(eligibility, list):
        eligibility_values = tuple(cast(list[object], eligibility))
    else:
        eligibility_values = (eligibility,)
    return (
        *tuple(str(reason) for reason in eligibility_values),
        "countertrend_ineligible",
    )


def _support_signal_reason(metrics: dict[str, object], *, zone: str) -> str | None:
    if metrics.get("support_zone_match") != zone:
        return None
    contribution = metrics.get("support_contribution")
    if contribution is None:
        return None
    return f"support_confirmation_{str(contribution).lower()}_confluence"


def _material_transition(previous: GeriAssessment, current: GeriAssessment) -> bool:
    return (
        previous.maturity is not current.maturity
        or previous.active_level_sequence != current.active_level_sequence
        or previous.active_level_kind is not current.active_level_kind
        or previous.trade_side is not current.trade_side
    )


async def run_swing_4h_geri_process(
    *,
    ready_path: Path | None = None,
    once: bool = False,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, object] | None:
    """Bootstrap from PostgreSQL and publish the separate 4HGERI shadow stream."""

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
                raise ValueError("4HGERI requires at least one symbol")
            universe_source = "operator-override"
        bus = await connect_nats(settings)
        runtime = Swing4HGeriRuntime(
            engine=assembly.build_4hgeri(),
            publisher=bus,
            emit_countertrend_signals=(
                assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).implementation == "5.0.0"
            ),
        )
        engine_version = assembly.spec(EngineSlot.GERI_4H).implementation
        replay_specs = (
            (
                "marketbot.v1.4hgeri.assessment.>",
                runtime.restore_assessment,
                "marketbot-4hgeri-restore-v1",
            ),
        )
        if engine_version == "1.5.0":
            replay_specs += (
                (
                    "marketbot.v1.support-confirmation.assessment.>",
                    runtime.restore_support,
                    "marketbot-4hgeri-support-v1",
                ),
            )
        if engine_version not in {"1.2.0", "1.3.0", "1.4.0", "1.5.0"}:
            replay_specs += (
                (
                    "marketbot.v1.analysis.result.SWING.>",
                    runtime.handle_analysis,
                    "marketbot-4hgeri-swing-v1",
                ),
                (
                    "marketbot.v1.entry-opportunity.transition.>",
                    runtime.handle_opportunity,
                    "marketbot-4hgeri-opportunity-v1",
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
            engine_id="4hgeri-v1",
            symbols=selected,
            requirements=GERI_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
        )
        published = await runtime.bootstrap(bars, symbols=selected)
        historical_bars = len(bars)
        del bars
        summary: dict[str, object] = {
            **universe_health_details("4hgeri"),
            "service": "4hgeri-v1",
            "engine_version": engine_version,
            "engine_strategy_version": assembly.spec(EngineSlot.GERI_4H).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "SHADOW",
            "monitoring": "TMUX_MANUAL_ONLY",
            "symbols": len(selected),
            "universe_source": universe_source,
            "historical_bars": historical_bars,
            "assessments_published": published,
            "bar_source": "15Min_RTH_aggregated_09:30_ET",
            "feeds_core_opportunities": False,
            "feeds_countertrend_opportunities": (
                assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).implementation == "5.0.0"
            ),
            "emits_buy_signals": False,
            "places_orders": False,
        }
        if once:
            return summary
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.market.bar.1Min.>",
                runtime.handle_market,
                options=SubscriptionOptions(
                    durable_name="marketbot-4hgeri-market-v1",
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
