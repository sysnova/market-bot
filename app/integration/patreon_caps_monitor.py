"""Dedicated terminal views for PatreonCaps assessments and transitions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, TextIO

from app.common.settings import AppSettings, Environment
from app.contracts import (
    PATREON_CAPS_ASSESSMENT_EVENT,
    PATREON_CAPS_TRANSITION_EVENT,
    EventEnvelope,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .alert_sounds import play_patreon_confirmation_sound
from .distributed_composition import write_ready
from .patreon_caps_store import PostgresPatreonCapsStore

_RESET = "\033[0m"
_COLORS = {
    PatreonCapsState.WATCH_ZONE: "\033[90m",
    PatreonCapsState.SUPPORT_TEST: "\033[33m",
    PatreonCapsState.CONFIRMED_V: "\033[32m",
    PatreonCapsState.CONFIRMED_BASE: "\033[32m",
    PatreonCapsState.IMPULSE_RETEST: "\033[36m",
    PatreonCapsState.INVALIDATED: "\033[31m",
    PatreonCapsState.EXPIRED: "\033[90m",
}


async def run_patreon_caps_monitor(
    *,
    mode: Literal["analysis", "alerts"],
    ready_path: Path | None = None,
    history: int = 50,
    bell: bool = True,
    stream: TextIO | None = None,
) -> None:
    import sys

    output = stream or sys.stdout
    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresPatreonCapsStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError("PatreonCaps PostgreSQL schema is unavailable")
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )

    async def handle(envelope: EventEnvelope) -> None:
        if mode == "analysis":
            if envelope.event_type != PATREON_CAPS_ASSESSMENT_EVENT:
                return
            assessment = (
                envelope.payload
                if isinstance(envelope.payload, PatreonCapsAssessment)
                else PatreonCapsAssessment.model_validate(envelope.payload, strict=False)
            )
            print(_format_assessment(assessment), file=output, flush=True)
            return
        if envelope.event_type != PATREON_CAPS_TRANSITION_EVENT:
            return
        transition = (
            envelope.payload
            if isinstance(envelope.payload, PatreonCapsTransition)
            else PatreonCapsTransition.model_validate(envelope.payload, strict=False)
        )
        print(_format_transition(transition, color=True), file=output, flush=True)
        if bell and transition.state in {
            PatreonCapsState.CONFIRMED_V,
            PatreonCapsState.CONFIRMED_BASE,
            PatreonCapsState.IMPULSE_RETEST,
        }:
            play_patreon_confirmation_sound(fallback=output)

    if mode == "alerts":
        for transition in await store.recent(limit=history):
            print(_format_transition(transition, color=True), file=output, flush=True)
        subject = "marketbot.v1.patreon-caps.transition.>"
    else:
        subject = "marketbot.v1.patreon-caps.assessment.>"
    subscription = await bus.subscribe(
        subject,
        handle,
        options=SubscriptionOptions(
            durable_name=f"marketbot-patreon-caps-{mode}-monitor-v1",
            replay_all=False,
            ack_wait_seconds=60,
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": f"patreon-caps-{mode}-monitor",
                    "mode": mode,
                    "history": history if mode == "alerts" else 0,
                },
            )
        heading = "ANÁLISIS" if mode == "analysis" else "ALERTAS"
        print(f"PATREON CAPS — {heading} — esperando NATS...", file=output, flush=True)
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()


def _format_assessment(item: PatreonCapsAssessment) -> str:
    sources = ",".join(item.support_sources) or "-"
    threshold = str(item.macro_threshold) if item.macro_threshold is not None else "BLOCK"
    return (
        f"{item.occurred_at:%H:%M} {item.symbol:<6} {item.state.value:<16} "
        f"PX {item.current_price} Z {item.zone_low}-{item.zone_high} "
        f"INV {item.invalidation} C/Q/A/L/P {item.confluence_score}/"
        f"{item.confirmation_score}/{item.alignment_score}/{item.lesson_score}/"
        f"{item.patreon_score} LESSON {'OK' if item.lesson_gate_passed else 'BLOCK'} "
        f"MACRO {item.macro_regime.value}:{threshold} SRC [{sources}]"
    )


def _format_transition(item: PatreonCapsTransition, *, color: bool) -> str:
    sizing = ""
    if item.tranche_stage is not None:
        sizing = f" T{item.tranche_stage}"
    if item.suggested_tranche_usd is not None:
        sizing += (
            f" USD {item.suggested_tranche_usd} SH {item.suggested_whole_shares}"
        )
    text = (
        f"{item.occurred_at:%Y-%m-%d %H:%M} {item.symbol:<6} {item.state.value:<16} "
        f"SCORE {item.patreon_score} PX {item.current_price} "
        f"Z {item.zone_low}-{item.zone_high} INV {item.invalidation} "
        f"LESSON {item.lesson_score}:{'OK' if item.lesson_gate_passed else 'BLOCK'} "
        f"MACRO {item.macro_regime.value}{sizing}"
    )
    return f"{_COLORS[item.state]}{text}{_RESET}" if color else text
