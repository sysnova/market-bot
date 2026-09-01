"""Bounded terminal dashboard for durable Order Flow state."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.common.clock import SystemClock
from app.common.settings import AppSettings
from app.contracts import (
    ORDER_FLOW_STATE_EVENT,
    ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
    EventEnvelope,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
    OrderFlowWindow,
    Subscription,
    SubscriptionOptions,
    order_flow_state_subject,
    order_flow_support_subject,
)
from app.event_bus import NatsJetStreamEventBus

from .distributed_composition import write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly

_CLEAR_SCREEN = "\033[2J\033[H"
_FOUR_PLACES = Decimal("0.0001")
_ONE_PLACE = Decimal("0.1")
_NEW_YORK = ZoneInfo("America/New_York")
_DISPLAY_WINDOWS = (5, 15, 60, 300)
_BULLISH_STATES = {
    OrderFlowStateKind.BUY_PRESSURE,
    OrderFlowStateKind.SELLER_EXHAUSTION,
    OrderFlowStateKind.BUY_ABSORPTION,
    OrderFlowStateKind.BULLISH_DIVERGENCE,
}
_BEARISH_STATES = {
    OrderFlowStateKind.SELL_PRESSURE,
    OrderFlowStateKind.BUYER_EXHAUSTION,
    OrderFlowStateKind.SELL_ABSORPTION,
    OrderFlowStateKind.BEARISH_DIVERGENCE,
}
_STATE_LABELS = {
    OrderFlowStateKind.NEUTRAL: "NEUTRAL",
    OrderFlowStateKind.BUY_PRESSURE: "PRESION COMPRADORA",
    OrderFlowStateKind.SELL_PRESSURE: "PRESION VENDEDORA",
    OrderFlowStateKind.SELLER_EXHAUSTION: "AGOTAMIENTO VENDEDOR (ALCISTA)",
    OrderFlowStateKind.BUYER_EXHAUSTION: "AGOTAMIENTO COMPRADOR (BAJISTA)",
    OrderFlowStateKind.BUY_ABSORPTION: "COMPRADORES ABSORBEN VENTAS",
    OrderFlowStateKind.SELL_ABSORPTION: "VENDEDORES ABSORBEN COMPRAS",
    OrderFlowStateKind.BULLISH_DIVERGENCE: "DIVERGENCIA CVD ALCISTA",
    OrderFlowStateKind.BEARISH_DIVERGENCE: "DIVERGENCIA CVD BAJISTA",
}


def order_flow_monitor_subjects(symbols: tuple[str, ...]) -> tuple[str, ...]:
    """Return exact durable state subjects for the configured bounded scope."""

    return tuple(order_flow_state_subject(symbol) for symbol in symbols)


def order_flow_monitor_support_subjects(symbols: tuple[str, ...]) -> tuple[str, ...]:
    """Return exact contextual support subjects for the same bounded scope."""

    return tuple(order_flow_support_subject(symbol) for symbol in symbols)


class OrderFlowDashboard:
    """Keep the newest compact state for every configured symbol."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        expected_engine_version: str,
    ) -> None:
        normalized = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )
        if not normalized:
            raise ValueError("symbols must not be empty")
        if not expected_engine_version.strip():
            raise ValueError("expected_engine_version must not be empty")
        self.symbols = normalized
        self.expected_engine_version = expected_engine_version.strip()
        self.ignored_versions: set[str] = set()
        self._states: dict[str, OrderFlowState] = {}
        self._supports: dict[str, OrderFlowSupportAssessment] = {}

    def merge(self, state: OrderFlowState) -> bool:
        if state.symbol not in self.symbols:
            raise ValueError(f"unexpected Order Flow symbol: {state.symbol}")
        if state.engine_version != self.expected_engine_version:
            previous_count = len(self.ignored_versions)
            self.ignored_versions.add(state.engine_version)
            return len(self.ignored_versions) != previous_count
        current = self._states.get(state.symbol)
        if current is not None and state.occurred_at < current.occurred_at:
            return False
        changed = current != state
        self._states[state.symbol] = state
        return changed

    def items(self) -> tuple[tuple[str, OrderFlowState | None], ...]:
        return tuple((symbol, self._states.get(symbol)) for symbol in self.symbols)

    def merge_support(self, support: OrderFlowSupportAssessment) -> bool:
        if support.symbol not in self.symbols:
            raise ValueError(f"unexpected Order Flow Support symbol: {support.symbol}")
        current = self._supports.get(support.symbol)
        if current is not None and support.occurred_at < current.occurred_at:
            return False
        changed = current != support
        self._supports[support.symbol] = support
        return changed

    def support(self, symbol: str) -> OrderFlowSupportAssessment | None:
        return self._supports.get(symbol)


def format_order_flow_dashboard(
    dashboard: OrderFlowDashboard, *, refreshed_at: datetime
) -> str:
    """Render compact L1 pressure, quote and canonical rolling-window telemetry."""

    items = dashboard.items()
    has_assessment = any(state is not None for _, state in items)
    lines = [
        f"ORDER FLOW | SIP L1 | {len(dashboard.symbols)} SYMBOLS | "
        f"ENGINE {dashboard.expected_engine_version} | "
        f"REFRESH {refreshed_at.astimezone(_NEW_YORK):%Y-%m-%d %H:%M:%S %Z}",
        (
            "ESTADO | RECIBIENDO ASSESSMENTS"
            if has_assessment
            else "ESTADO | ESPERANDO EVENTOS DE MERCADO"
        ),
        "Estados durables; sin ordenes de broker",
    ]
    if dashboard.ignored_versions:
        lines.append(
            "IGNORADOS | assessment incompatible "
            + ",".join(sorted(dashboard.ignored_versions))
        )
    if not has_assessment:
        lines.append(
            "Todavia no se recibio ningun assessment de Order Flow; "
            "el panel se actualizara con el primer trade/quote util."
        )
    for symbol, state in items:
        if state is None:
            lines.append(f"\n{symbol} | PENDIENTE")
            continue
        if state.pulse_state is not None:
            lines.extend(
                _format_actionable_state(
                    symbol,
                    state,
                    dashboard.support(symbol),
                    refreshed_at=refreshed_at,
                )
            )
            continue
        freshness = "FRESH" if state.quote_fresh else "STALE"
        lines.append(
            f"\n{symbol} | {state.state.value} | CONF {_percent(state.confidence)} "
            f"| Q {_percent(state.data_quality)} | PX {_number(state.current_price)} "
            f"| {freshness} {_number(state.quote_age_ms)}ms"
        )
        lines.append(
            f"  BID {_number(state.bid_price)} | ASK {_number(state.ask_price)} "
            f"| SPREAD {_number(state.spread_bps)}bps | CVD {_signed(state.cumulative_delta)}"
        )
        windows = {window.window_seconds: window for window in state.windows}
        lines.append(
            "  "
            + " | ".join(
                _format_window(windows[seconds])
                for seconds in _DISPLAY_WINDOWS
                if seconds in windows
            )
        )
        lines.append(f"  RAZONES {','.join(state.reasons[-4:])}")
    return "\n".join(lines) + "\n"


def _format_actionable_state(
    symbol: str,
    state: OrderFlowState,
    support: OrderFlowSupportAssessment | None,
    *,
    refreshed_at: datetime,
) -> list[str]:
    regime, regime_score = _regime(state)
    action = _action(state, support, refreshed_at=refreshed_at)
    stable_since = state.state_stable_since or state.occurred_at
    stable_seconds = max(0, int((refreshed_at - stable_since).total_seconds()))
    pulse = state.pulse_state or state.state
    lines = [
        f"\n{symbol} | REGIMEN {regime} {_signed_score(regime_score)} "
        f"| ACCION {action}",
        f"  ESTABLE {_STATE_LABELS[state.state]} {stable_seconds}s "
        f"| PULSO {_STATE_LABELS[pulse]}",
    ]
    if state.candidate_state is not None:
        lines.append(
            f"  CANDIDATO {_STATE_LABELS[state.candidate_state]} "
            f"{state.candidate_samples} | esperando persistencia"
        )
    freshness = "FRESH" if state.quote_fresh else "STALE"
    lines.append(
        f"  CONF {_percent(state.confidence)} | Q {_percent(state.data_quality)} "
        f"| PX {_number(state.current_price)} | {freshness} "
        f"| CVD {_signed(state.cumulative_delta)}"
    )
    if support is None:
        lines.append("  CONTEXTO SIN CONFLUENCIA ESTRUCTURAL | ACCION NO HABILITADA")
    else:
        disposition = _support_label(support.disposition)
        freshness_label = "FRESH" if support.fresh_until >= refreshed_at else "STALE"
        lines.append(
            f"  SOPORTE {_number(support.zone_low)}-{_number(support.zone_high)} "
            f"| {disposition} | {freshness_label}"
        )
        lines.append(
            f"  TRIGGER > {_number(support.zone_high)} "
            f"| RIESGO < {_number(support.zone_low)}"
        )
    windows = {window.window_seconds: window for window in state.windows}
    lines.append(
        "  "
        + " | ".join(
            _format_window(windows[seconds])
            for seconds in _DISPLAY_WINDOWS
            if seconds in windows
        )
    )
    lines.append(f"  RAZONES {','.join(state.reasons[-4:])}")
    return lines


def _action(
    state: OrderFlowState,
    support: OrderFlowSupportAssessment | None,
    *,
    refreshed_at: datetime,
) -> str:
    if (
        not state.quote_fresh
        or state.data_quality < Decimal("0.60")
        or state.confidence < Decimal("0.50")
    ):
        return "ESPERAR_DATOS"
    support_age = (
        state.occurred_at - support.order_flow_occurred_at
        if support is not None
        else timedelta.max
    )
    usable_support = (
        support is not None
        and support.fresh_at_assessment
        and support.fresh_until >= refreshed_at
        and support.quote_fresh
        and support.order_flow_state is state.state
        and timedelta() <= support_age <= timedelta(seconds=15)
    )
    if usable_support and support is not None:
        if (
            support.disposition is OrderFlowSupportDisposition.CONFIRMS_SUPPORT
            and state.state in _BULLISH_STATES
        ):
            regime, _ = _regime(state)
            if regime == "VENDEDOR":
                return "ESPERAR_RECLAIM"
            if state.current_price < support.zone_low:
                return "SOPORTE_PERDIDO"
            if state.current_price <= support.zone_high:
                return "PREPARAR_LONG"
            maximum_chase = support.zone_high + (support.zone_high - support.zone_low)
            if state.current_price <= maximum_chase:
                return "LONG_TRIGGERED"
            return "NO_PERSEGUIR_EXTENDIDO"
        if (
            support.disposition is OrderFlowSupportDisposition.WARNS_BREAKDOWN
            and state.state in _BEARISH_STATES
        ):
            if state.current_price < support.zone_low:
                return "BREAKDOWN_CONFIRMADO"
            return "RIESGO_BREAKDOWN"
    if state.state in _BULLISH_STATES:
        return "ESPERAR_ESTRUCTURA"
    if state.state in _BEARISH_STATES:
        return "PROTEGER_RIESGO"
    return "ESPERAR"


def _regime(state: OrderFlowState) -> tuple[str, Decimal]:
    windows = {window.window_seconds: window for window in state.windows}
    weights = ((15, Decimal("0.25")), (60, Decimal("0.45")), (300, Decimal("0.30")))
    score = Decimal("0")
    used = Decimal("0")
    for seconds, weight in weights:
        window = windows.get(seconds)
        if window is None:
            continue
        classified = window.buy_volume + window.sell_volume
        if not classified:
            continue
        score += window.delta / classified * weight
        used += weight
    normalized = score / used if used else Decimal("0")
    if normalized >= Decimal("0.15"):
        return "COMPRADOR", normalized
    if normalized <= Decimal("-0.15"):
        return "VENDEDOR", normalized
    return "MIXTO", normalized


def _signed_score(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(_ONE_PLACE, rounding=ROUND_HALF_UP):+}"


def _support_label(disposition: OrderFlowSupportDisposition) -> str:
    return {
        OrderFlowSupportDisposition.CONFIRMS_SUPPORT: "CONFIRMA SOPORTE",
        OrderFlowSupportDisposition.WARNS_BREAKDOWN: "ADVIERTE RUPTURA",
        OrderFlowSupportDisposition.NEUTRAL: "SIN CONFIRMACION",
    }[disposition]


async def run_order_flow_monitor(  # pragma: no cover - long-running NATS process
    *,
    ready_path: Path | None = None,
    refresh_interval: timedelta = timedelta(seconds=1),
    stream: TextIO | None = None,
) -> None:
    """Show the latest durable Order Flow states for the exact configured symbols."""

    import sys

    if refresh_interval <= timedelta():
        raise ValueError("refresh_interval must be positive")
    output = stream or sys.stdout
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    engine = assembly.build_order_flow()
    symbols = engine.tracked_symbols
    if not symbols:
        raise RuntimeError("Order Flow monitor requires a bounded symbol strategy")
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    dashboard = OrderFlowDashboard(
        symbols=symbols,
        expected_engine_version=engine.engine_version,
    )
    clock = SystemClock()
    changed = asyncio.Event()
    lock = asyncio.Lock()
    subscriptions: list[Subscription] = []

    def render() -> None:
        print(
            _CLEAR_SCREEN + format_order_flow_dashboard(dashboard, refreshed_at=clock.now()),
            file=output,
            end="",
            flush=True,
        )

    async def handle(envelope: EventEnvelope) -> None:
        async with lock:
            if envelope.event_type == ORDER_FLOW_STATE_EVENT:
                changed_now = dashboard.merge(_payload(envelope, OrderFlowState))
            elif envelope.event_type == ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT:
                changed_now = dashboard.merge_support(
                    _payload(envelope, OrderFlowSupportAssessment)
                )
            else:
                return
            if changed_now:
                changed.set()

    subjects = (
        *order_flow_monitor_subjects(symbols),
        *order_flow_monitor_support_subjects(symbols),
    )
    try:
        for subject in subjects:
            subscriptions.append(
                await bus.subscribe(
                    subject,
                    handle,
                    options=SubscriptionOptions(
                        replay_latest_per_subject=True,
                        ack_wait_seconds=60,
                    ),
                )
            )
        render()
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.ORDER_FLOW)
            write_ready(
                ready_path,
                {
                    "service": "order-flow-monitor",
                    "symbols": symbols,
                    "subjects": subjects,
                    "mode": "ANALYTICAL_ONLY",
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        while True:
            await changed.wait()
            await asyncio.sleep(refresh_interval.total_seconds())
            changed.clear()
            async with lock:
                render()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()


def _payload[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)


def _format_window(window: OrderFlowWindow) -> str:
    return (
        f"{window.window_seconds}s D{_signed(window.delta)} "
        f"T{window.trade_count} P{_signed(window.price_change_bps)}bps"
    )


def _number(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return str(value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP))


def _signed(value: Decimal) -> str:
    return f"{value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP):+}"


def _percent(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(_ONE_PLACE, rounding=ROUND_HALF_UP)}%"
