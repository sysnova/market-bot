"""Holdings-only Signal Fusion composition over PostgreSQL and NATS JetStream."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ELLIOTT_WAVE_ASSESSMENT_EVENT,
    FUSION_ASSESSMENT_EVENT,
    FUSION_BUY_CONFIRMED_EVENT,
    FUSION_RECOVERY_CONFIRMED_EVENT,
    FUSION_TRANSITION_EVENT,
    PATREON_CAPS_ASSESSMENT_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisHorizon,
    AnalysisResult,
    EventEnvelope,
    FusionAssessment,
    FusionState,
    FusionTransition,
    PatreonCapsAssessment,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    WaveAssessment,
    analysis_result_subject,
    elliott_wave_assessment_subject,
    fusion_assessment_subject,
    fusion_buy_confirmed_subject,
    fusion_recovery_confirmed_subject,
    fusion_transition_subject,
    patreon_caps_assessment_subject,
    support_assessment_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.signal_fusion_engine import SignalFusionContext

from .distributed_composition import connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_signal_adapter import entry_signal_from_fusion, publish_entry_signal
from .postgres_universe import PostgresUniverseClient, UniverseSnapshot
from .universe_policy import universe_health_details

FUSION_SOURCE_SUBJECTS = (
    "marketbot.v1.analysis.result.>",
    "marketbot.v1.support-confirmation.assessment.>",
    "marketbot.v1.elliott-wave.assessment.>",
    "marketbot.v1.patreon-caps.assessment.>",
)

FUSION_ANALYSIS_HORIZONS = (
    AnalysisHorizon.LONG_TERM,
    AnalysisHorizon.SWING,
    AnalysisHorizon.INTRADAY,
    AnalysisHorizon.DILUTION,
    AnalysisHorizon.VOLUME_STRUCTURE,
)


class HoldingsProvider(Protocol):
    async def get_holdings(self) -> UniverseSnapshot: ...


class FusionPortfolioProvider(HoldingsProvider, Protocol):
    async def get_holding_quantity(self, symbol: str) -> Decimal: ...


class FusionPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class FusionEngine(Protocol):
    def evaluate(self, context: SignalFusionContext) -> FusionAssessment: ...


async def load_fusion_holdings(provider: HoldingsProvider) -> UniverseSnapshot:
    """Use active positive holdings and never merge the watchlist."""

    snapshot = await provider.get_holdings()
    if not snapshot.symbols:
        raise RuntimeError("Signal Fusion requires at least one positive local holding")
    return snapshot


class SignalFusionRuntime:
    def __init__(
        self,
        *,
        engine: FusionEngine,
        publisher: FusionPublisher,
        symbols: tuple[str, ...],
        holding_quantities: dict[str, Decimal],
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._symbols = {item.strip().upper() for item in symbols}
        self._holding_quantities = holding_quantities
        self._supports: dict[str, SupportAssessment] = {}
        self._waves: dict[str, WaveAssessment] = {}
        self._patreon: dict[str, PatreonCapsAssessment] = {}
        self._analyses: dict[str, dict[AnalysisHorizon, AnalysisResult]] = {}
        self._latest: dict[str, FusionAssessment] = {}
        self._hydrating = True

    async def restore_fusion(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != FUSION_ASSESSMENT_EVENT:
            return
        item = _payload(envelope, FusionAssessment)
        previous = self._latest.get(item.symbol)
        if previous is None or item.occurred_at >= previous.occurred_at:
            self._latest[item.symbol] = item

    async def handle_source(self, envelope: EventEnvelope) -> None:
        symbol: str | None = None
        if envelope.event_type == SUPPORT_ASSESSMENT_EVENT:
            item = _payload(envelope, SupportAssessment)
            symbol = item.symbol
            _keep_latest(self._supports, item.symbol, item)
        elif envelope.event_type == ELLIOTT_WAVE_ASSESSMENT_EVENT:
            item = _payload(envelope, WaveAssessment)
            symbol = item.symbol
            _keep_latest(self._waves, item.symbol, item)
        elif envelope.event_type == PATREON_CAPS_ASSESSMENT_EVENT:
            item = _payload(envelope, PatreonCapsAssessment)
            symbol = item.symbol
            _keep_latest(self._patreon, item.symbol, item)
        elif envelope.event_type == ANALYSIS_RESULT_EVENT:
            item = _payload(envelope, AnalysisResult)
            if item.horizon not in FUSION_ANALYSIS_HORIZONS:
                return
            symbol = item.symbol
            current = self._analyses.setdefault(item.symbol, {}).get(item.horizon)
            if current is None or item.as_of >= current.as_of:
                self._analyses[item.symbol][item.horizon] = item
        else:
            return
        if symbol not in self._symbols or self._hydrating:
            return
        await self._evaluate(symbol)

    async def complete_hydration(self) -> int:
        self._hydrating = False
        published = 0
        for symbol in sorted(self._symbols):
            if await self._evaluate(symbol):
                published += 1
        return published

    async def _evaluate(self, symbol: str) -> bool:
        support = self._supports.get(symbol)
        wave = self._waves.get(symbol)
        if support is None and wave is None:
            return False
        previous = self._latest.get(symbol)
        quantity = Decimal(str(self._holding_quantities.get(symbol, 0)))
        assessment = self._engine.evaluate(
            SignalFusionContext(
                symbol=symbol,
                support=support,
                wave=wave,
                analyses=tuple(self._analyses.get(symbol, {}).values()),
                patreon=self._patreon.get(symbol),
                holding_quantity=quantity,
                previous_assessment=previous,
            )
        )
        if previous is not None and _same_observation(previous, assessment):
            return False
        self._latest[symbol] = assessment
        await self._publish_assessment(assessment)
        changed = previous is None or previous.state is not assessment.state
        if changed:
            await self._publish_transition(assessment, previous)
        if assessment.state is FusionState.BUY_CONFIRMED and changed:
            await self._publisher.publish(
                fusion_buy_confirmed_subject(assessment.symbol),
                EventEnvelope(
                    event_type=FUSION_BUY_CONFIRMED_EVENT,
                    occurred_at=assessment.occurred_at,
                    source="signal-fusion-v0",
                    subject=assessment.symbol,
                    payload=assessment,
                ),
            )
        if assessment.state is FusionState.RECOVERY_CONFIRMED and changed:
            await self._publisher.publish(
                fusion_recovery_confirmed_subject(assessment.symbol),
                EventEnvelope(
                    event_type=FUSION_RECOVERY_CONFIRMED_EVENT,
                    occurred_at=assessment.occurred_at,
                    source="signal-fusion-v0",
                    subject=assessment.symbol,
                    payload=assessment,
                ),
            )
        return True

    async def _publish_assessment(self, assessment: FusionAssessment) -> None:
        await self._publisher.publish(
            fusion_assessment_subject(assessment.symbol),
            EventEnvelope(
                event_type=FUSION_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="signal-fusion-v0",
                subject=assessment.symbol,
                payload=assessment,
            ),
        )

    async def _publish_transition(
        self, assessment: FusionAssessment, previous: FusionAssessment | None
    ) -> None:
        transition = FusionTransition(
            assessment_id=assessment.assessment_id,
            symbol=assessment.symbol,
            occurred_at=assessment.occurred_at,
            engine_version=assessment.engine_version,
            previous_state=previous.state if previous is not None else None,
            state=assessment.state,
            score=assessment.score,
            trigger_price=assessment.trigger_price,
            entry_price=assessment.entry_price,
            invalidation=assessment.invalidation,
            target_price=assessment.target_price,
            reward_risk_ratio=assessment.reward_risk_ratio,
            reasons=assessment.reasons,
            context_hash=assessment.context_hash,
        )
        await self._publisher.publish(
            fusion_transition_subject(transition.state, transition.symbol),
            EventEnvelope(
                event_type=FUSION_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="signal-fusion-v0",
                subject=transition.symbol,
                payload=transition,
            ),
        )
        signal = entry_signal_from_fusion(transition)
        if signal is not None:
            await publish_entry_signal(self._publisher, signal, source="signal-fusion")


async def run_signal_fusion_process(
    *, ready_path: Path | None = None, once: bool = False, symbol: str | None = None
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    provider = PostgresUniverseClient(database)
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        holdings = await load_fusion_holdings(provider)
        requested = symbol.strip().upper() if symbol is not None else None
        if requested is not None and requested not in holdings.symbols:
            return {
                "service": "signal-fusion-v0",
                "mode": "ACTIVE",
                "requested_symbol": requested,
                "eligible": False,
                "reason": "positive_holding_required",
                "assessments_published": 0,
                "execution_enabled": False,
            }
        selected_symbols = (requested,) if requested is not None else holdings.symbols
        quantities = dict(
            zip(
                selected_symbols,
                await asyncio.gather(
                    *(provider.get_holding_quantity(item) for item in selected_symbols)
                ),
                strict=True,
            )
        )
        bus = await connect_nats(settings)
        runtime = SignalFusionRuntime(
            engine=assembly.build_signal_fusion(),
            publisher=bus,
            symbols=selected_symbols,
            holding_quantities=quantities,
        )
        source_subscriptions = [
            await bus.subscribe(
                subject,
                runtime.handle_source,
                options=SubscriptionOptions(
                    durable_name=f"marketbot-signal-fusion-v0-{index}",
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
            for index, subject in enumerate(FUSION_SOURCE_SUBJECTS, start=1)
        ]
        subscriptions.extend(source_subscriptions)
        await _hydrate_latest(bus, runtime, selected_symbols)
        published = await runtime.complete_hydration()
        summary: dict[str, object] = {
            **universe_health_details("signal-fusion"),
            "service": "signal-fusion-v0",
            "engine_version": assembly.spec(EngineSlot.SIGNAL_FUSION).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.SIGNAL_FUSION).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "ACTIVE",
            "universe": "positive-holdings-only",
            "universe_source": holdings.source,
            "symbols": list(selected_symbols),
            "assessments_published": published,
            "persistence": "nats-jetstream-15d",
            "patreon_is_independent_vote": False,
            "execution_enabled": False,
        }
        if once:
            return summary
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


def _payload[ModelT: BaseModel](
    envelope: EventEnvelope, model: type[ModelT]
) -> ModelT:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)


class _Occurred(Protocol):
    occurred_at: datetime


def _keep_latest[OccurredT: _Occurred](
    store: dict[str, OccurredT], key: str, item: OccurredT
) -> None:
    current = store.get(key)
    if current is None or item.occurred_at >= current.occurred_at:
        store[key] = item


def _same_observation(previous: FusionAssessment, current: FusionAssessment) -> bool:
    return (
        previous.context_hash == current.context_hash
        and previous.engine_version == current.engine_version
        and previous.state is current.state
        and previous.score == current.score
    )


async def _hydrate_latest(
    bus: NatsJetStreamEventBus,
    runtime: SignalFusionRuntime,
    symbols: tuple[str, ...],
) -> None:
    for symbol in symbols:
        fusion = await bus.get_last(fusion_assessment_subject(symbol))
        if fusion is not None:
            await runtime.restore_fusion(fusion)
        subjects = (
            support_assessment_subject(symbol),
            elliott_wave_assessment_subject(symbol),
            patreon_caps_assessment_subject(symbol),
            *(
                analysis_result_subject(horizon, symbol)
                for horizon in FUSION_ANALYSIS_HORIZONS
            ),
        )
        envelopes = await asyncio.gather(*(bus.get_last(subject) for subject in subjects))
        for envelope in envelopes:
            if envelope is not None:
                await runtime.handle_source(envelope)
