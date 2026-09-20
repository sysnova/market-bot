"""Live composition for leveraged watches and trackable purchase signals."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from app.common.clock import SystemClock
from app.common.context_cache import context_store
from app.common.market_session import market_session
from app.common.settings import AppSettings
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    LEVERAGED_THESIS_ASSESSMENT_EVENT,
    LEVERAGED_THESIS_TRANSITION_EVENT,
    LOCAL_ALERT_EVENT,
    ORDER_FLOW_STATE_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    EntrySignal,
    EntrySignalFamily,
    EventEnvelope,
    LeveragedThesisAssessment,
    LeveragedThesisState,
    LocalAlert,
    NamedValue,
    OrderFlowState,
    PatternDirection,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    analysis_result_subject,
    entry_signal_subject,
    leveraged_thesis_assessment_subject,
    leveraged_thesis_transition_subject,
    local_alert_subject,
    order_flow_state_subject,
    support_assessment_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.leveraged_thesis_engine import (
    LeveragedPair,
    LeveragedThesisContext,
    LeveragedThesisEngine,
)
from app.leveraged_thesis_engine.v11 import LeveragedThesisEngineV11
from app.leveraged_thesis_engine.v12 import LeveragedThesisEngineV12
from app.leveraged_thesis_engine.v13 import LeveragedThesisEngineV13

from .deferred_long_runtime import DeferredLongRuntime, RedisLongStateStore
from .deferred_short_runtime import DeferredShortRuntime, RedisShortStateStore
from .distributed_composition import write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_signal_adapter import (
    entry_signal_from_leveraged_thesis,
    publish_entry_signal,
)
from .redis_ticker_cache import RedisTickerCache
from .ticker_cache_transport import shared_cache_client
from .universe_policy import universe_health_details


def leveraged_thesis_source_subjects(
    engine: LeveragedThesisEngine,
) -> tuple[str, ...]:
    """Consume only durable assessments for the fixed directional pairs."""

    underlyings = tuple(pair.underlying_symbol for pair in engine.pairs)
    return (
        *(
            (analysis_result_subject(AnalysisHorizon.SWING, symbol) for symbol in underlyings)
            if isinstance(engine, LeveragedThesisEngineV13)
            else ()
        ),
        *(analysis_result_subject(AnalysisHorizon.INTRADAY, symbol) for symbol in underlyings),
        *(order_flow_state_subject(symbol) for symbol in engine.required_symbols),
        *(support_assessment_subject(symbol) for symbol in underlyings),
        *(
            (local_alert_subject(AlertSeverity.ACTION, symbol) for symbol in underlyings)
            if isinstance(engine, LeveragedThesisEngineV11)
            and not isinstance(engine, LeveragedThesisEngineV13)
            else ()
        ),
        *(
            (
                entry_signal_subject(family, "ASTS")
                for family in (
                    EntrySignalFamily.CORE_ENTRY,
                    EntrySignalFamily.CORE_RECOVERY,
                    EntrySignalFamily.SWING_TRADE,
                )
            )
            if isinstance(engine, LeveragedThesisEngineV12)
            else ()
        ),
    )


async def run_leveraged_thesis_process(  # pragma: no cover - long-running process
    *, ready_path: Path | None = None
) -> None:
    """Observe fixed pairs and publish notices; never submit broker orders."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    engine = assembly.build_leveraged_thesis()
    url = settings.nats_url.get_secret_value()
    bus = await NatsJetStreamEventBus.connect(servers=[url], prefix="marketbot", stream="MARKETBOT")
    analyses = context_store(
        AnalysisResult, scope="integration:leveraged_thesis_composition:analyses"
    )
    swing_analyses = context_store(
        AnalysisResult, scope="integration:leveraged_thesis_composition:swing_analyses"
    )
    flows = context_store(OrderFlowState, scope="integration:leveraged_thesis_composition:flows")
    supports = context_store(
        SupportAssessment, scope="integration:leveraged_thesis_composition:supports"
    )
    previous = context_store(
        LeveragedThesisAssessment, scope="integration:leveraged_thesis_composition:previous"
    )
    deferred = None
    deferred_long = None
    owned_cache = None
    if isinstance(engine, LeveragedThesisEngineV11):
        cache = shared_cache_client()
        if not isinstance(cache, RedisTickerCache):
            owned_cache = RedisTickerCache.connect(settings.redis_url.get_secret_value())
            cache = owned_cache
        deferred = DeferredShortRuntime(
            engine, bus, RedisShortStateStore(cache.redis, cache.namespace)
        )
        await deferred.restore()
        if isinstance(engine, LeveragedThesisEngineV12):
            deferred_long = DeferredLongRuntime(
                engine, bus, RedisLongStateStore(cache.redis, cache.namespace)
            )
            await deferred_long.restore()
    subscriptions: list[Subscription] = []
    lock = asyncio.Lock()
    clock = SystemClock()
    published_blocked_short_alerts: set[UUID] = set()

    for pair in engine.pairs:
        envelope = await bus.get_last(leveraged_thesis_assessment_subject(pair.underlying_symbol))
        if envelope is not None and envelope.event_type == LEVERAGED_THESIS_ASSESSMENT_EVENT:
            assessment = _model(envelope, LeveragedThesisAssessment)
            previous[assessment.underlying_symbol] = assessment

    async def evaluate_pair(pair: LeveragedPair, *, causation_id: UUID) -> None:
        # ASTS v1.2 entries come exclusively from the retained SHORT and native Swing paths.
        if deferred_long is not None and pair.underlying_symbol == "ASTS":
            return
        underlying_flow = flows.get(pair.underlying_symbol)
        if underlying_flow is None:
            return
        pair_flows = {
            symbol: flow
            for symbol in {pair.bullish_instrument, pair.bearish_instrument}
            if (flow := flows.get(symbol)) is not None
        }
        evidence_times = [underlying_flow.occurred_at]
        analysis = analyses.get(pair.underlying_symbol)
        support = supports.get(pair.underlying_symbol)
        if analysis is not None:
            evidence_times.append(analysis.as_of)
        evidence_times.extend(item.occurred_at for item in pair_flows.values())
        evaluation = engine.evaluate(
            LeveragedThesisContext(
                pair=pair,
                as_of=max(evidence_times),
                session=market_session(max(evidence_times)),
                analysis=analysis,
                underlying_flow=underlying_flow,
                instrument_flows=pair_flows,
                support=support,
                previous_assessment=previous.get(pair.underlying_symbol),
            )
        )
        # In v1.1 inverse entries are driven only by retained SHORT confirmation alerts.
        if deferred is not None and evaluation.assessment.direction is PatternDirection.BEARISH:
            return
        if evaluation.transition is None:
            previous[pair.underlying_symbol] = evaluation.assessment
            return
        assessment = evaluation.assessment
        transition = evaluation.transition
        await bus.publish(
            leveraged_thesis_assessment_subject(assessment.underlying_symbol),
            EventEnvelope(
                event_id=assessment.assessment_id,
                event_type=LEVERAGED_THESIS_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="leveraged-thesis",
                subject=assessment.underlying_symbol,
                payload=assessment,
                causation_id=causation_id,
            ),
        )
        await bus.publish(
            leveraged_thesis_transition_subject(transition.state, transition.underlying_symbol),
            EventEnvelope(
                event_id=transition.transition_id,
                event_type=LEVERAGED_THESIS_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="leveraged-thesis",
                subject=transition.underlying_symbol,
                payload=transition,
                causation_id=causation_id,
            ),
        )
        alert, signal = leveraged_thesis_publications(assessment)
        if signal is not None:
            await publish_entry_signal(bus, signal, source="leveraged-thesis")
        if alert is not None and signal is None:
            await bus.publish(
                local_alert_subject(alert.severity, alert.symbol),
                EventEnvelope(
                    event_id=alert.alert_id,
                    event_type=LOCAL_ALERT_EVENT,
                    occurred_at=alert.created_at,
                    source="leveraged-thesis",
                    subject=alert.symbol,
                    payload=alert,
                    causation_id=transition.transition_id,
                ),
            )

        previous[pair.underlying_symbol] = evaluation.assessment

    async def evaluate_related(symbol: str, *, causation_id: UUID) -> None:
        for pair in engine.pairs:
            if symbol in {
                pair.underlying_symbol,
                pair.bullish_instrument,
                pair.bearish_instrument,
            }:
                await evaluate_pair(pair, causation_id=causation_id)

    async def evaluate_short(symbol: str, *, causation_id: UUID) -> None:
        if not isinstance(engine, LeveragedThesisEngineV13) or deferred is None:
            return
        swing = swing_analyses.get(symbol)
        intraday = analyses.get(symbol)
        if swing is None or intraday is None:
            return
        alert = engine.evaluate_short(
            swing=swing,
            intraday=intraday,
            support=supports.get(symbol),
            now=clock.now(),
        )
        if alert is None:
            return
        envelope = EventEnvelope(
            event_id=alert.alert_id,
            event_type=LOCAL_ALERT_EVENT,
            occurred_at=alert.created_at,
            source="leveraged-thesis",
            subject=alert.symbol,
            payload=alert,
            causation_id=causation_id,
        )
        if "short_entry_confirmed" in alert.reasons:
            current = deferred.states.get(symbol)
            if (
                current is not None
                and current.intent is not None
                and current.intent.alert.alert_id == alert.alert_id
            ):
                return
            # The durable alert reaches Opportunities before dependent Redis work.
            await bus.publish(local_alert_subject(alert.severity, alert.symbol), envelope)
            await deferred.handle(envelope)
            return
        if alert.alert_id in published_blocked_short_alerts:
            return
        await bus.publish(local_alert_subject(alert.severity, alert.symbol), envelope)
        published_blocked_short_alerts.add(alert.alert_id)

    async def handle_analysis(envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        analysis = _model(envelope, AnalysisResult)
        if engine.pair_for_underlying(analysis.symbol) is None:
            return
        accepted_horizons = (
            {AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY}
            if isinstance(engine, LeveragedThesisEngineV13)
            else {AnalysisHorizon.INTRADAY}
        )
        if analysis.horizon not in accepted_horizons:
            return
        async with lock:
            cache = swing_analyses if analysis.horizon is AnalysisHorizon.SWING else analyses
            current = cache.get(analysis.symbol)
            if current is None or analysis.as_of >= current.as_of:
                cache[analysis.symbol] = analysis
            await evaluate_short(analysis.symbol, causation_id=envelope.event_id)
            if analysis.horizon is AnalysisHorizon.INTRADAY:
                await evaluate_related(analysis.symbol, causation_id=envelope.event_id)

    async def handle_flow(envelope: EventEnvelope) -> None:
        if deferred_long is not None:
            await deferred_long.handle(envelope)
        if deferred is not None:
            await deferred.handle(envelope)
        if envelope.event_type != ORDER_FLOW_STATE_EVENT:
            return
        flow = _model(envelope, OrderFlowState)
        if flow.symbol not in engine.required_symbols:
            return
        async with lock:
            current = flows.get(flow.symbol)
            if current is None or flow.occurred_at >= current.occurred_at:
                flows[flow.symbol] = flow
            await evaluate_related(flow.symbol, causation_id=envelope.event_id)

    async def handle_support(envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        support = _model(envelope, SupportAssessment)
        if engine.pair_for_underlying(support.symbol) is None:
            return
        async with lock:
            current = supports.get(support.symbol)
            support_time = support.assessed_at or support.occurred_at
            current_time = (
                (current.assessed_at or current.occurred_at) if current is not None else None
            )
            if current_time is None or support_time >= current_time:
                supports[support.symbol] = support
            await evaluate_short(support.symbol, causation_id=envelope.event_id)
            await evaluate_related(support.symbol, causation_id=envelope.event_id)

    source_subjects = leveraged_thesis_source_subjects(engine)
    durable_generation = (
        "v13" if isinstance(engine, LeveragedThesisEngineV13) else "v1"
    )
    for index, subject in enumerate(source_subjects, start=1):
        if ".analysis.result." in subject:
            handler = handle_analysis
        elif ".entry-signal." in subject and deferred_long is not None:
            handler = deferred_long.handle
        elif ".alert.local." in subject and deferred is not None:
            handler = deferred.handle
        elif ".support-confirmation.assessment." in subject:
            handler = handle_support
        else:
            handler = handle_flow
        subscriptions.append(
            await bus.subscribe(
                subject,
                handler,
                options=SubscriptionOptions(
                    durable_name=(
                        f"marketbot-leveraged-thesis-source-{durable_generation}-{index}"
                    ),
                    replay_latest_per_subject=True,
                    ack_wait_seconds=30,
                ),
            )
        )
    try:
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.LEVERAGED_THESIS)
            write_ready(
                ready_path,
                {
                    **universe_health_details("leveraged-thesis"),
                    "service": "leveraged-thesis",
                    "mode": "ALERT_ONLY",
                    "symbols": engine.required_symbols,
                    "source_subjects": source_subjects,
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        while True:
            await asyncio.sleep(1)
            if deferred is not None:
                await deferred.tick()
            if deferred_long is not None:
                await deferred_long.tick()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()
        if owned_cache is not None:
            owned_cache.close()


def build_leveraged_alert(assessment: LeveragedThesisAssessment) -> LocalAlert | None:
    """Materialize only early and confirmed human notices."""

    if assessment.state not in {
        LeveragedThesisState.EARLY_FLOW,
        LeveragedThesisState.BUY_CONFIRMED,
        LeveragedThesisState.CANCELLED,
    }:
        return None
    if assessment.instrument_symbol is None or assessment.source_analysis_id is None:
        return None
    confirmed = assessment.state is LeveragedThesisState.BUY_CONFIRMED
    cancelled = assessment.state is LeveragedThesisState.CANCELLED
    direction = "alcista" if assessment.direction is PatternDirection.BULLISH else "bajista"
    stage = (
        "CANCELACIÓN POR SWEEP-RECLAIM"
        if cancelled
        else ("COMPRA CONFIRMADA" if confirmed else "FLUJO TEMPRANO")
    )
    price = assessment.instrument_ask or assessment.instrument_bid
    execution = (
        f"Ask {price}; spread {assessment.spread_bps} bps."
        if price is not None and assessment.spread_bps is not None
        else "Precio/spread del instrumento aún pendientes."
    )
    support = _support_summary(assessment)
    message = (
        (
            f"Cancelar tesis y seguimiento de {assessment.instrument_symbol}: "
            f"{assessment.underlying_symbol} recuperó con sweep confirmado la zona diaria. "
        )
        if cancelled
        else (
            f"{assessment.underlying_symbol} {direction}; candidato "
            f"{assessment.instrument_symbol} {assessment.exposure.value}. "
        )
    ) + (
        f"{support} {execution} Caduca {assessment.expires_at.isoformat()}. "
        "Sin ejecución automática."
    )
    score = assessment.structure_score or (
        (assessment.underlying_flow_confidence or Decimal("0")) * Decimal("100")
    )
    return LocalAlert(
        symbol=assessment.instrument_symbol,
        created_at=assessment.occurred_at,
        expires_at=assessment.expires_at,
        severity=(
            AlertSeverity.CRITICAL
            if cancelled
            else (AlertSeverity.ACTION if confirmed else AlertSeverity.WATCH)
        ),
        title=f"{assessment.instrument_symbol} | {stage} | {assessment.underlying_symbol}",
        message=message,
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(assessment.source_analysis_id,),
        metrics=(
            NamedValue(name="underlying_symbol", value=assessment.underlying_symbol),
            NamedValue(name="underlying_direction", value=assessment.direction.value),
            NamedValue(name="exposure", value=assessment.exposure.value),
            NamedValue(name="underlying_price", value=assessment.underlying_price),
            NamedValue(name="instrument_ask", value=assessment.instrument_ask),
            NamedValue(name="spread_bps", value=assessment.spread_bps),
            NamedValue(
                name="support_state",
                value=(assessment.support_state.value if assessment.support_state else None),
            ),
            NamedValue(name="support_zone_low", value=assessment.support_zone_low),
            NamedValue(name="support_zone_high", value=assessment.support_zone_high),
            NamedValue(name="support_invalidation", value=assessment.support_invalidation),
            NamedValue(
                name="support_distance_percent",
                value=assessment.support_distance_percent,
            ),
            NamedValue(
                name="support_actionability_score",
                value=assessment.support_actionability_score,
            ),
            NamedValue(
                name="underlying_flow_state",
                value=(
                    assessment.underlying_flow_state.value
                    if assessment.underlying_flow_state is not None
                    else None
                ),
            ),
            NamedValue(
                name="instrument_flow_state",
                value=(
                    assessment.instrument_flow_state.value
                    if assessment.instrument_flow_state is not None
                    else None
                ),
            ),
        ),
        score=min(Decimal("100"), score),
        reasons=assessment.reasons,
        deduplication_key=(
            f"leveraged-thesis:{assessment.underlying_symbol}:"
            f"{assessment.instrument_symbol}:{assessment.state.value}:"
            f"{assessment.context_hash}"
        ),
        kind=(
            AlertKind.LEVERAGED_THESIS_CANCELLED
            if cancelled
            else (AlertKind.LEVERAGED_THESIS_BUY if confirmed else AlertKind.LEVERAGED_THESIS_EARLY)
        ),
    )


def leveraged_thesis_publications(
    assessment: LeveragedThesisAssessment,
) -> tuple[LocalAlert | None, EntrySignal | None]:
    """Expose confirmed purchases and cancellations, keeping early flow internal."""

    signal = entry_signal_from_leveraged_thesis(assessment)
    if signal is not None:
        return None, signal
    if assessment.state is LeveragedThesisState.EARLY_FLOW:
        return None, None
    return build_leveraged_alert(assessment), None


def _support_summary(assessment: LeveragedThesisAssessment) -> str:
    if assessment.support_state is None:
        return "Soporte: assessment pendiente."
    if assessment.support_zone_low is None or assessment.support_zone_high is None:
        return f"Soporte: {assessment.support_state.value}, sin zona cercana."
    distance = (
        f", distancia {assessment.support_distance_percent}%"
        if assessment.support_distance_percent is not None
        else ""
    )
    invalidation = (
        f", invalida {assessment.support_invalidation}"
        if assessment.support_invalidation is not None
        else ""
    )
    return (
        f"Soporte: {assessment.support_state.value} "
        f"{assessment.support_zone_low}-{assessment.support_zone_high}"
        f"{distance}{invalidation}."
    )


def _model[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)
