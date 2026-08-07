"""NATS consumer rendering buy maturities and portfolio-protection alerts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.alert_engine.confirmed import buy_maturity, is_portfolio_monitor_alert
from app.alert_engine.sinks import ConsoleAlertSink
from app.common.settings import AppSettings
from app.contracts import (
    LOCAL_ALERT_EVENT,
    AlertKind,
    EventEnvelope,
    LocalAlert,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus

from .alert_sounds import (
    play_aggressive_flow_sound,
    play_buy_maturity_sound,
    play_entry_close_sound,
)
from .distributed_composition import write_ready


async def run_confirmed_buy_monitor(*, ready_path: Path | None = None, bell: bool = True) -> None:
    """Consume new local alerts and render explicit buy maturities and protection."""

    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )
    sink = ConsoleAlertSink(stream=sys.stdout, bell=False, color=True)

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != LOCAL_ALERT_EVENT:
            return
        alert = (
            envelope.payload
            if isinstance(envelope.payload, LocalAlert)
            else LocalAlert.model_validate(envelope.payload, strict=False)
        )
        if is_portfolio_monitor_alert(alert):
            sink.emit(alert)
            maturity = buy_maturity(alert)
            if bell and maturity is not None:
                play_buy_maturity_sound(maturity, fallback=sys.stdout)
            elif bell and alert.kind is AlertKind.PORTFOLIO_FLOW_BUY:
                play_aggressive_flow_sound(fallback=sys.stdout)
            elif bell and alert.kind is AlertKind.ENTRY_OPPORTUNITY_CLOSED:
                play_entry_close_sound(fallback=sys.stdout)

    subscription = await bus.subscribe(
        "marketbot.v1.alert.local.>",
        handle,
        options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "confirmed-buy-monitor",
                    "subject": "marketbot.v1.alert.local.>",
                    "replay": False,
                },
            )
        print(
            "COMPRAS L1-L4 + PROGRESO ENTRY WATCHER + CIERRES + PROTECCION - esperando NATS...",
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
