"""Live dashboard for every watchlist SwingTrade Fibonacci assessment."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from app.common.clock import SystemClock
from app.common.settings import AppSettings
from app.contracts import (
    SWING_TRADE_ASSESSMENT_EVENT,
    EventEnvelope,
    SubscriptionOptions,
    SwingTradeAssessment,
    SwingTradeMaturity,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready

_CLEAR_SCREEN = "\033[2J\033[H"
_RESET = "\033[0m"
_COLORS = {
    None: "\033[90m",
    SwingTradeMaturity.ST1: "\033[37m",
    SwingTradeMaturity.ST2: "\033[36m",
    SwingTradeMaturity.ST3: "\033[33m",
    SwingTradeMaturity.ST4: "\033[1;32m",
}
_RANK = {
    None: 0,
    SwingTradeMaturity.ST1: 1,
    SwingTradeMaturity.ST2: 2,
    SwingTradeMaturity.ST3: 3,
    SwingTradeMaturity.ST4: 4,
}


class SwingTradeDashboard:
    """Retain the newest complete assessment for each Watchlist symbol."""

    def __init__(self) -> None:
        self._items: dict[str, SwingTradeAssessment] = {}

    def merge(self, assessment: SwingTradeAssessment) -> bool:
        current = self._items.get(assessment.symbol)
        incoming_at = assessment.assessed_at or assessment.occurred_at
        if current is not None:
            current_at = current.assessed_at or current.occurred_at
            if incoming_at <= current_at:
                return False
        self._items[assessment.symbol] = assessment
        return True

    def items(self) -> tuple[SwingTradeAssessment, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (_RANK[item.maturity], item.symbol),
            )
        )


async def run_swing_trade_monitor(
    *, ready_path: Path | None = None, stream: TextIO | None = None
) -> None:
    """Replay and continuously redraw the latest SwingTrade state per symbol."""

    output = stream or sys.stdout
    settings = AppSettings()
    dashboard = SwingTradeDashboard()
    clock = SystemClock()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )

    def render() -> None:
        snapshot = format_swing_trade_dashboard(
            dashboard,
            refreshed_at=clock.now(),
            color=True,
        )
        print(f"{_CLEAR_SCREEN}{snapshot}", file=output, end="", flush=True)

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != SWING_TRADE_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, SwingTradeAssessment)
            else SwingTradeAssessment.model_validate(envelope.payload, strict=False)
        )
        if dashboard.merge(assessment):
            render()

    subscription = await bus.subscribe(
        "marketbot.v1.swing-trade.assessment.>",
        handle,
        options=SubscriptionOptions(
            durable_name="marketbot-swing-trade-monitor-v1",
            replay_latest_per_subject=True,
            ack_wait_seconds=60,
        ),
    )
    try:
        await bus.wait_until_caught_up(subscription, timeout_seconds=60)
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "swing-trade-monitor",
                    "subject": "marketbot.v1.swing-trade.assessment.>",
                    "replay": "latest-per-symbol",
                    "symbols": len(dashboard.items()),
                    "places_orders": False,
                },
            )
        render()
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()


def format_swing_trade_dashboard(
    dashboard: SwingTradeDashboard,
    *,
    refreshed_at: datetime,
    color: bool,
) -> str:
    items = dashboard.items()
    eligible = sum(item.eligible for item in items)
    in_zone = sum(item.spot_in_fibonacci_zone for item in items)
    lines = [
        (
            "SWING TRADE — FIBONACCI WATCHLIST | "
            f"{refreshed_at:%Y-%m-%d %H:%M:%S %Z} | "
            f"TOTAL {len(items)} | ELEGIBLES {eligible} | EN ZONA {in_zone}"
        ),
        "Evaluacion: cierre 15Min RTH | ST1-ST4 separado de Core | NO EMITE ORDENES",
        "=" * 118,
    ]
    if not items:
        lines.append("Esperando assessments de swing-trade-v1 por NATS...")
    for item in items:
        lines.extend(_format_assessment(item, color=color))
    return "\n".join(lines) + "\n"


def _format_assessment(item: SwingTradeAssessment, *, color: bool) -> list[str]:
    maturity = item.maturity.value if item.maturity is not None else "SIN_ST"
    eligible = "SI" if item.eligible else "NO"
    zone = "SI" if item.spot_in_fibonacci_zone else "NO"
    support = "SI" if item.support_confluence else "NO"
    geri = "SI" if item.geri_confluence else "NO"
    metrics = {metric.name: metric.value for metric in item.metrics}
    assessed_at = item.assessed_at or item.occurred_at
    heading = (
        f"{item.symbol} | {maturity} | ELIGIBLE {eligible} | "
        f"SPOT {item.current_price} | R:R {item.reward_risk} | "
        f"ASOF {assessed_at:%Y-%m-%d %H:%M}"
    )
    color_code = _COLORS[item.maturity]
    if color:
        heading = f"{color_code}{heading}{_RESET}"
    lines = [
        "",
        heading,
        (
            f"  IMPULSO {item.impulse_low} ({item.impulse_low_at:%Y-%m-%d}) -> "
            f"{item.impulse_high} ({item.impulse_high_at:%Y-%m-%d})"
        ),
        (
            f"  FIB 61.8 {item.fibonacci_618} | FIB 50 {item.fibonacci_50} | "
            f"FIB 161.8 {item.fibonacci_1618}"
        ),
        f"  ZONA {item.zone_low}-{item.zone_high} | SPOT EN ZONA {zone} | ATR14 {item.atr14}",
        (
            f"  SOPORTE 20D {item.support_20d} | "
            f"BANDA {item.support_band_low}-{item.support_band_high} | "
            f"CONFLUENCIA {support}"
        ),
        (
            f"  INVALIDA {item.invalidation} | TARGET 20D {item.primary_target} | "
            f"TARGET EXT {item.extended_target} | R:R EXT {item.extended_reward_risk}"
        ),
        (
            f"  GERI ZONA {_range(item.geri_zone_low, item.geri_zone_high)} | "
            f"CONFLUENCIA {geri} | FUENTE {metrics.get('geri_zone_source', '-')} | "
            f"ASSESSMENT {item.geri_assessment_id or '-'}"
        ),
        f"  RAZONES {','.join(item.reasons)}",
    ]
    if metrics.get("support_contribution") is not None:
        lines.insert(
            -1,
            (
                f"  SUPPORT {metrics.get('support_state')} / "
                f"{metrics.get('support_contribution')} | "
                f"ZONA {metrics.get('support_zone_low')}-{metrics.get('support_zone_high')} | "
                f"REACT {metrics.get('support_reaction_score')} | "
                f"REV {metrics.get('support_reversal_score')}"
            ),
        )
    if item.metrics:
        lines.append(
            "  METRICAS " + " | ".join(f"{metric.name}={metric.value}" for metric in item.metrics)
        )
    lines.append("-" * 118)
    return lines


def _range(low: object | None, high: object | None) -> str:
    return f"{low}-{high}" if low is not None and high is not None else "-"
