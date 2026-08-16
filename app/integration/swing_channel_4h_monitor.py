"""Human terminal view for the independent four-hour Swing channel."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TextIO

from app.common.settings import AppSettings
from app.contracts import (
    SWING_CHANNEL_ASSESSMENT_EVENT,
    EventEnvelope,
    SubscriptionOptions,
    SwingChannelAssessment,
    SwingChannelMaturity,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready

_RESET = "\033[0m"
_COLORS = {
    SwingChannelMaturity.ARMED: "\033[90m",
    SwingChannelMaturity.IN_ZONE_4H: "\033[33m",
    SwingChannelMaturity.L2_4H: "\033[36m",
    SwingChannelMaturity.L3: "\033[32m",
    SwingChannelMaturity.L4: "\033[1;32m",
    SwingChannelMaturity.INVALIDATED: "\033[31m",
}
_HUMAN_STATE = {
    SwingChannelMaturity.ARMED: "CANAL ARMADO; esperando apoyo en soporte",
    SwingChannelMaturity.IN_ZONE_4H: "EN ZONA 4H; soporte bajo prueba",
    SwingChannelMaturity.L2_4H: "REBOTE 4H confirmado",
    SwingChannelMaturity.L3: "REBOTE 4H + Swing diario alineado",
    SwingChannelMaturity.L4: "4H + Swing diario + Opportunity L3/L4 alineados",
    SwingChannelMaturity.INVALIDATED: "CANAL INVALIDADO",
}


async def run_swing_channel_4h_monitor(
    *,
    ready_path: Path | None = None,
    stream: TextIO | None = None,
) -> None:
    """Replay one current state per ticker, then display material live changes."""

    output = stream or sys.stdout
    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != SWING_CHANNEL_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, SwingChannelAssessment)
            else SwingChannelAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_assessment(assessment, color=True), file=output, flush=True)

    subscription = await bus.subscribe(
        "marketbot.v1.swing-channel-4h.assessment.>",
        handle,
        options=SubscriptionOptions(
            durable_name="marketbot-swing-channel-4h-monitor-v1",
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "swing-channel-4h-monitor",
                    "subject": "marketbot.v1.swing-channel-4h.assessment.>",
                    "replay": "latest-per-symbol",
                },
            )
        print(
            "SWING CHANNEL 4H - canal paralelo experimental - esperando NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()


def _format_assessment(item: SwingChannelAssessment, *, color: bool) -> str:
    alignment = (
        "Swing diario SI" if item.daily_swing_aligned else "Swing diario NO"
    )
    opportunity = (
        "Opportunity L3/L4 SI"
        if item.existing_maturity_aligned
        else "Opportunity L3/L4 NO"
    )
    body = (
        f"{item.symbol} | {item.maturity.value} | {_HUMAN_STATE[item.maturity]}\n"
        f"  Precio {item.current_price} | soporte {item.support} | "
        f"zona {item.zone_low}-{item.zone_high} | invalida {item.invalidation}\n"
        f"  Canal: pendiente +{item.slope_per_bar}/barra | "
        f"ancho {item.width_atr} ATR | contencion {item.containment_ratio * 100:.1f}% | "
        f"toques {item.support_touch_count}\n"
        f"  Confirmaciones: rebote {'SI' if item.bounce_confirmed else 'NO'} | "
        f"{alignment} | {opportunity}"
    )
    return f"{_COLORS[item.maturity]}{body}{_RESET}" if color else body
