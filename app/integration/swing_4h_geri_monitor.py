"""Human terminal view for the independent horizontal-level 4HGERI model."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TextIO

from app.common.settings import AppSettings
from app.contracts import (
    GERI_ASSESSMENT_EVENT,
    EventEnvelope,
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready

_RESET = "\033[0m"
_COLORS = {
    GeriMaturity.BUILDING: "\033[90m",
    GeriMaturity.ARMED: "\033[90m",
    GeriMaturity.IN_ZONE_4H: "\033[33m",
    GeriMaturity.L2_4H: "\033[36m",
    GeriMaturity.L3: "\033[32m",
    GeriMaturity.L4: "\033[1;32m",
    GeriMaturity.EXTENDED: "\033[35m",
    GeriMaturity.RECLAIM_REQUIRED: "\033[33m",
    GeriMaturity.INVALIDATED: "\033[31m",
}


async def run_swing_4h_geri_monitor(
    *, ready_path: Path | None = None, stream: TextIO | None = None
) -> None:
    output = stream or sys.stdout
    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != GERI_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, GeriAssessment)
            else GeriAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_assessment(assessment, color=True), file=output, flush=True)

    subscription = await bus.subscribe(
        "marketbot.v1.4hgeri.assessment.>",
        handle,
        options=SubscriptionOptions(
            durable_name="marketbot-4hgeri-monitor-v1",
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "4hgeri-monitor",
                    "subject": "marketbot.v1.4hgeri.assessment.>",
                    "replay": "latest-per-symbol",
                },
            )
        print(
            "4HGERI - MONITOR MANUAL - NO COMPRA / NO OPPORTUNITY - esperando NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()


def _format_assessment(item: GeriAssessment, *, color: bool) -> str:
    levels = " -> ".join(
        f"N{level.sequence} {level.kind.value} {level.price}" for level in item.levels[-5:]
    )
    active = (
        f"nivel activo para pullback {item.trade_side.value}"
        if item.standalone_swing
        else (
            "esperando ruptura alcista"
            if item.active_level_kind is GeriLevelKind.RESISTANCE
            else "soporte activo para pullback"
        )
    )
    zone = (
        f"zona {item.zone_low}-{item.zone_high} | invalida {item.invalidation}"
        if item.zone_low is not None
        else "sin zona operable mientras se construye la estructura"
    )
    stage = {
        GeriMaturity.BUILDING: "G0 BUILDING",
        GeriMaturity.ARMED: "G0 ARMED",
        GeriMaturity.IN_ZONE_4H: "G1 IN_ZONE",
        GeriMaturity.L2_4H: "G2 FAST",
        GeriMaturity.L3: "G3 4H CONFIRMED",
        GeriMaturity.L4: "G4 CONTINUATION",
        GeriMaturity.EXTENDED: "EXTENDED",
        GeriMaturity.RECLAIM_REQUIRED: "RECLAIM REQUIRED",
        GeriMaturity.INVALIDATED: "INVALIDATED",
    }[item.maturity]
    heading = (
        f"4HGERI v{item.engine_version} | {stage} | {item.trade_side.value}"
        if item.standalone_swing
        else f"4HGERI {item.maturity.value}"
    )
    body = (
        f"{item.symbol} | {heading} | N{item.active_level_sequence} "
        f"{item.active_level_kind.value} {item.active_level_price} | {active}\n"
        f"  Precio {item.current_price} | {zone} | ruptura {item.breakout_buffer} ATR-px\n"
        f"  Estructura: {levels}\n"
        f"  Confirmaciones: 15m {'SI' if item.fast_confirmation else 'NO'} | "
        f"4H {'SI' if item.four_hour_confirmation else 'NO'} | "
        f"continuacion {'SI' if item.continuation_confirmation else 'NO'}\n"
        "  SALIDA: MONITOR MANUAL | NO COMPRA | NO OPPORTUNITY"
    )
    return f"{_COLORS[item.maturity]}{body}{_RESET}" if color else body
