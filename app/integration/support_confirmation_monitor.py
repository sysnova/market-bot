"""Dedicated panel for Support Confirmation assessments of held symbols."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from app.common.settings import AppSettings
from app.contracts import (
    SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_TRANSITION_EVENT,
    EventEnvelope,
    StructuralSupportReference,
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
                    "mode": "ACTIVE",
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
    assessed_at = getattr(item, "assessed_at", None) or item.occurred_at
    data_as_of = getattr(item, "data_as_of", None) or item.occurred_at
    structural = _format_structural_supports(getattr(item, "structural_supports", ()))
    sources = _format_sources(getattr(item, "support_sources", ()))
    confirmation = _confirmation_label(item)
    position = getattr(item, "zone_position", None)
    position_label = position.value if position is not None else "-"
    distance_percent = getattr(item, "zone_distance_percent", None)
    distance_atr = getattr(item, "zone_distance_atr", None)
    touch_age = getattr(item, "touch_age_sessions", None)
    touch_count = getattr(item, "touch_count", 0)
    four_hour = _format_four_hour(item)
    actionability = getattr(item, "actionability_score", Decimal())
    impulse = _format_impulse(item)
    return (
        f"{assessed_at:%H:%M} {item.symbol:<6} {item.state.value:<22} "
        f"{confirmation:<17} SUP {item.support_score} "
        f"REACT {item.reaction_score} REV {item.reversal_score} "
        f"PX {item.current_price} Z {_display(item.zone_low)}-"
        f"{_display(item.zone_high)} INV {_display(item.invalidation)} "
        f"POS {position_label} DIST {_display(distance_percent)}%/"
        f"{_display(distance_atr)}ATR TOUCH {touch_count}@{_display(touch_age)} "
        f"ACT {actionability} B-RISK {risk} 4H {four_hour} "
        f"SRC {sources} STRUCT {structural} IMP {impulse} "
        f"DATA {data_as_of:%m-%d %H:%M}"
    )


def _confirmation_label(item: SupportAssessment) -> str:
    if item.state is SupportState.BASE_BUILDING:
        return "BREAKOUT_PENDING"
    if item.state is SupportState.LIQUIDITY_SWEEP:
        return "RECLAIM_PENDING"
    if item.state is SupportState.SINGLE_SUPPORT_NEARBY:
        return "UNCONFIRMED"
    return item.confirmation_type.value


def _format_sources(items: tuple[str, ...]) -> str:
    if not items:
        return "-"
    return ",".join(_support_label(item) for item in items)


def _format_four_hour(item: SupportAssessment) -> str:
    reclaim = "R" if getattr(item, "four_hour_reclaim", False) else "-"
    higher_high = "HH" if getattr(item, "four_hour_higher_high", False) else "-"
    higher_low = "HL" if getattr(item, "four_hour_higher_low", False) else "-"
    return f"{reclaim}/{higher_high}/{higher_low}"


def _format_structural_supports(
    items: tuple[StructuralSupportReference, ...],
) -> str:
    if not items:
        return "-"
    return ",".join(
        f"{_support_label(str(item.source))}:{item.price}(-{item.distance_percent}%)"
        for item in items
    )


def _support_label(source: str) -> str:
    labels = {
        "daily_sma50": "D-SMA50",
        "daily_sma200": "D-SMA200",
        "weekly_sma10": "W-SMA10",
        "weekly_sma30": "W-SMA30",
        "weekly_sma50": "W-SMA50",
        "weekly_sma200": "W-SMA200",
    }
    if source in labels:
        return labels[source]
    if source.startswith("pivot_daily_"):
        return "D-PIVOT"
    if source.startswith("pivot_weekly_"):
        return "W-PIVOT"
    return source.upper()


def _format_impulse(item: SupportAssessment) -> str:
    origin = getattr(item, "impulse_origin", None)
    origin_at = getattr(item, "impulse_origin_at", None)
    advance = getattr(item, "impulse_advance_percent", None)
    if origin is None or origin_at is None or advance is None:
        return "-"
    return f"{origin}@{origin_at:%m-%d} +{advance}%"


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
