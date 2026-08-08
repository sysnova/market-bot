"""Terminal views for Signal Fusion evidence and confirmed analytical buys."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, TextIO

from app.alert_engine.confirmed import BuyMaturity
from app.common.settings import AppSettings
from app.contracts import (
    FUSION_ASSESSMENT_EVENT,
    FUSION_BUY_CONFIRMED_EVENT,
    FUSION_RECOVERY_CONFIRMED_EVENT,
    EventEnvelope,
    FusionAssessment,
    FusionState,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus

from .alert_sounds import play_buy_maturity_sound
from .distributed_composition import write_ready

_SOLID_BUY_STYLE = "\x1b[1;97;45m"
_RESET_STYLE = "\x1b[0m"


async def run_signal_fusion_monitor(
    *,
    mode: Literal["analysis", "buys"],
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
    hydrated = False

    async def handle_assessment(envelope: EventEnvelope) -> None:
        if envelope.event_type != FUSION_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, FusionAssessment)
            else FusionAssessment.model_validate(envelope.payload, strict=False)
        )
        if mode == "buys" and item.state not in {
            FusionState.BUY_CONFIRMED,
            FusionState.RECOVERY_CONFIRMED,
        }:
            return
        if mode == "buys" and hydrated:
            return
        print(_format_assessment(item), file=output, flush=True)

    async def handle_buy(envelope: EventEnvelope) -> None:
        if envelope.event_type != FUSION_BUY_CONFIRMED_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, FusionAssessment)
            else FusionAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_solid_banner(item, color=True), file=output, flush=True)
        print(_format_assessment(item), file=output, flush=True)
        if bell:
            play_buy_maturity_sound(BuyMaturity.FULLY_MATURED, fallback=output)

    async def handle_recovery(envelope: EventEnvelope) -> None:
        if envelope.event_type != FUSION_RECOVERY_CONFIRMED_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, FusionAssessment)
            else FusionAssessment.model_validate(envelope.payload, strict=False)
        )
        print(_format_solid_banner(item, color=True), file=output, flush=True)
        print(_format_assessment(item), file=output, flush=True)
        if bell:
            play_buy_maturity_sound(BuyMaturity.FULLY_MATURED, fallback=output)

    assessment_subscription = await bus.subscribe(
        "marketbot.v1.signal-fusion.assessment.>",
        handle_assessment,
        options=SubscriptionOptions(
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    subscriptions = [assessment_subscription]
    if mode == "buys":
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.signal-fusion.buy-confirmed.>",
                handle_buy,
                options=SubscriptionOptions(
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
        subscriptions.append(
            await bus.subscribe(
                "marketbot.v1.signal-fusion.recovery-confirmed.>",
                handle_recovery,
                options=SubscriptionOptions(
                    replay_all=False,
                    ack_wait_seconds=60,
                ),
            )
        )
    try:
        await bus.wait_until_caught_up(assessment_subscription, timeout_seconds=60)
        hydrated = True
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": f"signal-fusion-{mode}-monitor",
                    "mode": "ACTIVE",
                    "universe": "positive-holdings-only",
                },
            )
        label = (
            "BUY / RECOVERY CONFIRMED"
            if mode == "buys"
            else "ARMED / EVIDENCIA"
        )
        print(f"SIGNAL FUSION — {label} — esperando NATS...", file=output, flush=True)
        print(
            "GATES Z=zona R=reaccion S=estructura L=long T=timing "
            "X=ejecucion D=SEC P=cartera RR=beneficio/riesgo RC=recuperacion",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _display(value: object | None) -> str:
    return "-" if value is None else str(value)


def _format_solid_banner(item: FusionAssessment, *, color: bool) -> str:
    """Render an unmistakable execution-ready Fusion confirmation."""

    price = item.entry_price or item.current_price
    label = item.state.value.replace("_", " ")
    banner = f"{item.symbol} | BUY L4 ${price} | FUSION {label}"
    return f"{_SOLID_BUY_STYLE} {banner} {_RESET_STYLE}" if color else banner


def _format_assessment(item: FusionAssessment) -> str:
    missing = ",".join(item.missing_sources) if item.missing_sources else "-"
    return (
        f"{item.occurred_at:%H:%M} {item.symbol:<6} {item.state.value:<14} "
        f"SCORE {item.score} "
        f"Z:{_yn(item.support_zone_gate)} R:{_yn(item.support_reaction_gate)} "
        f"S:{_yn(item.support_gate)} L:{_yn(item.trend_gate)} "
        f"T:{_yn(item.timing_gate)} X:{_yn(item.execution_gate)} "
        f"D:{_yn(item.dilution_gate)} P:{_yn(item.portfolio_gate)} "
        f"RR:{_yn(item.reward_risk_gate)} RC:{_yn(item.recovery_gate)} "
        f"PX {item.current_price} "
        f"TRG {_display(item.trigger_price)} INV {_display(item.invalidation)} "
        f"TGT {_display(item.target_price)} R/R {_display(item.reward_risk_ratio)} "
        f"PAT {_display(item.patreon_context)} MISS {missing}"
    )
