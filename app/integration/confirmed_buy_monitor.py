"""NATS consumer rendering buy maturities and portfolio-protection alerts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TextIO
from uuid import UUID

from app.alert_engine.sinks import ConsoleAlertSink
from app.common.settings import AppSettings
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    LOCAL_ALERT_EVENT,
    AlertKind,
    EntrySignal,
    EntrySignalFamily,
    EventEnvelope,
    LocalAlert,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus

from .alert_sounds import (
    play_aggressive_flow_sound,
    play_buy_maturity_sound,
    play_solid_buy_sound,
)
from .confirmed_signal_projection import project_confirmed_signal
from .distributed_composition import write_ready


async def run_confirmed_buy_monitor(
    *,
    ready_path: Path | None = None,
    bell: bool = True,
    stream: TextIO | None = None,
) -> None:
    """Render final buy decisions plus manual Portfolio Flow alarms."""

    settings = AppSettings()
    output = stream or sys.stdout
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )
    sink = ConsoleAlertSink(stream=output, bell=False, color=True)

    displayed: set[UUID | str] = set()

    async def handle_signal(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_SIGNAL_EVENT:
            return
        signal = (
            envelope.payload
            if isinstance(envelope.payload, EntrySignal)
            else EntrySignal.model_validate(envelope.payload, strict=False)
        )
        display_key: UUID | str = signal.signal_id
        if (
            signal.family is EntrySignalFamily.SWING_TRADE
            and signal.swing_trade_maturity is not None
        ):
            display_key = (
                f"{signal.family.value}:{signal.setup_id}:"
                f"{signal.swing_trade_maturity.value}"
            )
        if display_key in displayed:
            return
        projection = project_confirmed_signal(signal, color=True)
        if projection is None:
            return
        displayed.add(display_key)
        print(projection.text, file=output, flush=True)
        if not bell:
            return
        if projection.sound_maturity is not None:
            play_buy_maturity_sound(projection.sound_maturity, fallback=output)
        else:
            play_solid_buy_sound(fallback=output)

    async def handle_manual_flow(envelope: EventEnvelope) -> None:
        if envelope.event_type != LOCAL_ALERT_EVENT:
            return
        alert = (
            envelope.payload
            if isinstance(envelope.payload, LocalAlert)
            else LocalAlert.model_validate(envelope.payload, strict=False)
        )
        if alert.kind not in {AlertKind.PORTFOLIO_PROTECT, AlertKind.PORTFOLIO_FLOW_BUY}:
            return
        sink.emit(alert)
        if bell and alert.kind is AlertKind.PORTFOLIO_FLOW_BUY:
            play_aggressive_flow_sound(fallback=output)

    subscriptions = (
        await bus.subscribe(
            "marketbot.v1.entry-signal.>",
            handle_signal,
            options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
        ),
        await bus.subscribe(
            "marketbot.v1.alert.local.>",
            handle_manual_flow,
            options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "confirmed-buy-monitor",
                    "subjects": (
                        "marketbot.v1.entry-signal.>",
                        "marketbot.v1.alert.local.>",
                    ),
                    "replay": False,
                },
            )
        print(
            "COMPRAS CONFIRMADAS + GESTION MANUAL PORTFOLIO FLOW - esperando NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()
