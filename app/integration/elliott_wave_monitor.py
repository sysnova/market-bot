"""Dedicated terminal panel for held-symbol Elliott Wave assessments."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TextIO

from app.common.settings import AppSettings
from app.contracts import (
    ELLIOTT_WAVE_ASSESSMENT_EVENT,
    EventEnvelope,
    SubscriptionOptions,
    WaveAssessment,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready


async def run_elliott_wave_monitor(
    *, ready_path: Path | None = None, stream: TextIO | None = None
) -> None:
    import sys

    output = stream or sys.stdout
    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != ELLIOTT_WAVE_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, WaveAssessment)
            else WaveAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_assessment(assessment), file=output, flush=True)

    subscription = await bus.subscribe(
        "marketbot.v1.elliott-wave.assessment.>",
        handle,
        options=SubscriptionOptions(
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "elliott-wave-analysis-monitor",
                    "universe": "positive-holdings-only",
                },
            )
        print(
            "ELLIOTT WAVE — TENENCIAS — esperando análisis NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()


def _display(value: object | None) -> str:
    return "-" if value is None else str(value)


def _format_assessment(item: WaveAssessment) -> str:
    assessed_at = getattr(item, "assessed_at", None) or item.occurred_at
    data_as_of = getattr(item, "data_as_of", None) or item.occurred_at
    return (
        f"{assessed_at:%H:%M} {item.symbol:<6} {item.phase.value:<18} "
        f"SCORE {item.score} CONF {item.confidence} PX {item.current_price} "
        f"Z {_display(item.entry_zone_low)}-{_display(item.entry_zone_high)} "
        f"TRG {_display(item.trigger_price)} INV {_display(item.invalidation)} "
        f"TGT {_display(item.target_low)}-{_display(item.target_high)} "
        f"DATA {data_as_of:%m-%d %H:%M}"
    )
