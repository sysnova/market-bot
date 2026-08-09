"""Independent NATS composition for paper-entry recovery decisions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    AnalysisResult,
    EntryOpportunityEvent,
    EventEnvelope,
    MarketBar,
    Subscription,
    SubscriptionOptions,
)
from app.entry_recovery_engine import EntryRecoveryEngineV11
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import _publish_health
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_opportunity_store import PostgresEntryOpportunityStore
from .entry_setup_publisher import publish_entry_setup_assessment
from .entry_signal_adapter import publish_entry_signal
from .foundation import connect_nats, write_ready
from .universe_policy import universe_health_details


async def run_entry_recovery_process(*, ready_path: Path | None = None) -> None:
    """Reconfirm recovered paper entries without changing the original Watcher."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    service = "entry-recovery"
    try:
        opportunity_store = PostgresEntryOpportunityStore(create_session_factory(database))
        if not await opportunity_store.is_ready():
            raise RuntimeError("entry opportunity schema is unavailable")
        engine = assembly.build_entry_recovery()
        for opportunity in await opportunity_store.list_active():
            engine.ingest_opportunity(
                EntryOpportunityEvent(
                    occurred_at=opportunity.updated_at,
                    opportunity=opportunity,
                    reasons=("restored_active_opportunity",),
                )
            )

        bus = await connect_nats(settings)

        async def handle_opportunity(envelope: EventEnvelope) -> None:
            if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
                return
            event = (
                envelope.payload
                if isinstance(envelope.payload, EntryOpportunityEvent)
                else EntryOpportunityEvent.model_validate(envelope.payload, strict=False)
            )
            engine.ingest_opportunity(event)

        async def handle_analysis(envelope: EventEnvelope) -> None:
            if envelope.event_type != ANALYSIS_RESULT_EVENT:
                return
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
            engine.ingest_analysis(result)

        async def handle_bar(envelope: EventEnvelope) -> None:
            if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
                return
            bar = (
                envelope.payload
                if isinstance(envelope.payload, MarketBar)
                else MarketBar.model_validate(envelope.payload, strict=False)
            )
            if isinstance(engine, EntryRecoveryEngineV11):
                assessment = engine.ingest_assessment(bar)
                if assessment is not None:
                    await publish_entry_setup_assessment(
                        bus, assessment, source=service
                    )
            else:
                signal = engine.ingest_bar(bar)
                if signal is not None:
                    await publish_entry_signal(bus, signal, source=service)

        subscriptions.extend(
            (
                await bus.subscribe(
                    "marketbot.v1.entry-opportunity.transition.>",
                    handle_opportunity,
                    options=SubscriptionOptions(
                        durable_name="marketbot-entry-recovery-opportunity-v1",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                ),
                await bus.subscribe(
                    "marketbot.v1.analysis.result.>",
                    handle_analysis,
                    options=SubscriptionOptions(
                        durable_name="marketbot-entry-recovery-analysis-v1",
                        replay_latest_per_subject=True,
                        ack_wait_seconds=60,
                    ),
                ),
                await bus.subscribe(
                    "marketbot.v1.market.bar.1Min.>",
                    handle_bar,
                    options=SubscriptionOptions(
                        durable_name="marketbot-entry-recovery-bars-v1",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                ),
            )
        )
        spec = assembly.spec(EngineSlot.ENTRY_RECOVERY)
        details = {
            **universe_health_details("entry-recovery"),
            "service": service,
            "engine_implementation": spec.implementation,
            "engine_strategy_version": spec.strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "universe": "active-paper-opportunities",
            "output_subject": (
                "marketbot.v1.entry-setup.CORE_RECOVERY.>"
                if isinstance(engine, EntryRecoveryEngineV11)
                else "marketbot.v1.entry-signal.CORE_RECOVERY.>"
            ),
            "execution_enabled": False,
        }
        await _publish_health(bus, service, details, clock.now())
        if ready_path is not None:
            write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()
