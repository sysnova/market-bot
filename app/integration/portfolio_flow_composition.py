"""Portfolio order-flow process consuming ephemeral Core NATS ticks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from nats.aio.client import Client as NatsClient

from app.common.clock import SystemClock
from app.common.settings import AppSettings
from app.contracts import LOCAL_ALERT_EVENT, EventEnvelope, local_alert_subject
from app.event_bus import NatsJetStreamEventBus
from app.event_bus.codec import decode_envelope

from .distributed_composition import _write_ready  # pyright: ignore[reportPrivateUsage]
from .engine_assembly import EngineSlot, MarketBotAssembly
from .entry_signal_adapter import entry_signal_from_alert, publish_entry_signal
from .universe_policy import universe_health_details


class _CoreMessage(Protocol):
    data: bytes


async def run_portfolio_flow_process(*, ready_path: Path | None = None) -> None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    url = settings.nats_url.get_secret_value()
    core = NatsClient()
    await core.connect(url)
    durable = await NatsJetStreamEventBus.connect(
        servers=[url], prefix="marketbot", stream="MARKETBOT"
    )
    engine = assembly.build_portfolio_flow()
    clock = SystemClock()

    async def handle(message: _CoreMessage) -> None:
        envelope = decode_envelope(message.data)
        alert = engine.ingest(envelope, now=clock.now())
        if alert is None:
            return
        await durable.publish(
            local_alert_subject(alert.severity, alert.symbol),
            EventEnvelope(
                event_type=LOCAL_ALERT_EVENT,
                occurred_at=alert.created_at,
                source="portfolio-flow-engine",
                subject=alert.symbol,
                payload=alert,
            ),
        )
        signal = entry_signal_from_alert(alert)
        if signal is not None:
            await publish_entry_signal(durable, signal, source="portfolio-flow")

    subscriptions = [
        await core.subscribe("marketbot.market.data.quote.>", cb=handle),
        await core.subscribe("marketbot.market.data.trade.>", cb=handle),
    ]
    try:
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.PORTFOLIO_FLOW)
            _write_ready(
                ready_path,
                {
                    **universe_health_details("portfolio-flow"),
                    "service": "portfolio-flow-engine",
                    "ephemeral": True,
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await core.drain()
        await durable.close()
