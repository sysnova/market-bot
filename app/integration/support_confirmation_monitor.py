"""Dedicated panel for Support Confirmation assessments of held symbols."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TextIO

from app.common.settings import AppSettings
from app.contracts import (
    SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_TRANSITION_EVENT,
    EventEnvelope,
    SubscriptionOptions,
    SupportAssessment,
    SupportState,
    SupportTransition,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready


async def run_support_confirmation_monitor(
    *,
    ready_path: Path | None = None,
    stream: TextIO | None = None,
    bell: bool = True,
) -> None:
    import sys

    output = stream or sys.stdout
    settings = AppSettings()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )

    async def handle_assessment(envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, SupportAssessment)
            else SupportAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_assessment(assessment), file=output, flush=True)

    async def handle_transition(envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_TRANSITION_EVENT:
            return
        transition = (
            envelope.payload
            if isinstance(envelope.payload, SupportTransition)
            else SupportTransition.model_validate(envelope.payload, strict=False)
        )
        if not _is_reentry_transition(transition):
            return
        if bell:
            print("\a", end="", file=output, flush=True)
        print(_format_reentry(transition), file=output, flush=True)

    assessment_subscription = await bus.subscribe(
        "marketbot.v1.support-confirmation.assessment.>",
        handle_assessment,
        options=SubscriptionOptions(
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    transition_subscription = await bus.subscribe(
        "marketbot.v1.support-confirmation.transition.>",
        handle_transition,
        options=SubscriptionOptions(
            replay_all=False,
            ack_wait_seconds=60,
        ),
    )
    try:
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "support-confirmation-monitor",
                    "universe": "positive-holdings-only",
                    "mode": "SHADOW",
                },
            )
        print(
            "SUPPORT CONFIRMATION — TENENCIAS — esperando análisis NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await assessment_subscription.unsubscribe()
        await transition_subscription.unsubscribe()
        await bus.close()


def _display(value: object | None) -> str:
    return "-" if value is None else str(value)


def _format_assessment(item: SupportAssessment) -> str:
    risk = "YES" if item.b_wave_risk else "NO"
    return (
        f"{item.occurred_at:%H:%M} {item.symbol:<6} {item.state.value:<20} "
        f"{item.confirmation_type.value:<15} SUP {item.support_score} "
        f"REACT {item.reaction_score} REV {item.reversal_score} "
        f"PX {item.current_price} Z {_display(item.zone_low)}-"
        f"{_display(item.zone_high)} INV {_display(item.invalidation)} "
        f"B-RISK {risk}"
    )


def _is_reentry_transition(item: SupportTransition) -> bool:
    return item.state in {
        SupportState.STRUCTURE_CONFIRMED,
        SupportState.RETEST_CONFIRMED,
    }


def _format_reentry(item: SupportTransition) -> str:
    return (
        f"*** REENTRY ARMED {item.symbol} {item.state.value} "
        f"{item.confirmation_type.value} SUP {item.support_score} "
        f"REACT {item.reaction_score} REV {item.reversal_score} "
        f"Z {_display(item.zone_low)}-{_display(item.zone_high)} "
        f"INV {_display(item.invalidation)} ***"
    )
