"""NATS consumer rendering only confirmed buy alerts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.alert_engine.confirmed import is_confirmed_buy
from app.alert_engine.sinks import ConsoleAlertSink
from app.common.settings import AppSettings
from app.contracts import LOCAL_ALERT_EVENT, EventEnvelope, LocalAlert, SubscriptionOptions
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import _write_ready


async def run_confirmed_buy_monitor(*, ready_path: Path | None = None, bell: bool = True) -> None:
    """Consume new local-alert events and render confirmed buys only."""

    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )
    sink = ConsoleAlertSink(stream=sys.stdout, bell=bell, color=True)

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != LOCAL_ALERT_EVENT:
            return
        alert = (
            envelope.payload
            if isinstance(envelope.payload, LocalAlert)
            else LocalAlert.model_validate(envelope.payload, strict=False)
        )
        if is_confirmed_buy(alert):
            sink.emit(alert)

    subscription = await bus.subscribe(
        "marketbot.v1.alert.local.>",
        handle,
        options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
    )
    try:
        if ready_path is not None:
            _write_ready(
                ready_path,
                {
                    "service": "confirmed-buy-monitor",
                    "subject": "marketbot.v1.alert.local.>",
                    "replay": False,
                },
            )
        print("COMPRAS CONFIRMADAS — esperando nuevas señales NATS...", flush=True)
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
