"""NATS consumer rendering buy maturities and portfolio-protection alerts."""

from __future__ import annotations

import asyncio
import sys
from collections import OrderedDict
from pathlib import Path
from typing import TextIO, TypeVar
from uuid import UUID

from app.alert_engine.sinks import ConsoleAlertSink
from app.common.settings import AppSettings
from app.contracts import (
    ENTRY_OPPORTUNITY_EVENT,
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
from app.leveraged_thesis_engine import LeveragedPair

from .alert_sounds import (
    play_aggressive_flow_sound,
    play_buy_maturity_sound,
    play_entry_close_sound,
    play_solid_buy_sound,
)
from .confirmed_signal_projection import project_confirmed_short, project_confirmed_signal
from .distributed_composition import write_ready
from .engine_assembly import MarketBotAssembly

_CONFIRMATION_CACHE_LIMIT = 4096
_GateValue = TypeVar("_GateValue")


class _PersistedConfirmationGate:
    """Release operator confirmations only after their opportunity commit is published."""

    def __init__(self, *, limit: int = _CONFIRMATION_CACHE_LIMIT) -> None:
        self._limit = limit
        self._pending: OrderedDict[UUID, EntrySignal | LocalAlert] = OrderedDict()
        self._persisted: OrderedDict[UUID, None] = OrderedDict()
        self._delivered: OrderedDict[UUID, None] = OrderedDict()

    def observe(
        self,
        event_id: UUID,
        candidate: EntrySignal | LocalAlert,
    ) -> EntrySignal | LocalAlert | None:
        if event_id in self._delivered:
            return None
        if event_id not in self._persisted:
            self._remember(self._pending, event_id, candidate)
            return None
        self._persisted.pop(event_id)
        self._mark_delivered(event_id)
        return candidate

    def confirm(self, event_id: UUID) -> EntrySignal | LocalAlert | None:
        if event_id in self._delivered:
            return None
        candidate = self._pending.pop(event_id, None)
        if candidate is None:
            self._remember(self._persisted, event_id, None)
            return None
        self._mark_delivered(event_id)
        return candidate

    def _mark_delivered(self, event_id: UUID) -> None:
        self._persisted.pop(event_id, None)
        self._remember(self._delivered, event_id, None)

    def _remember(
        self,
        items: OrderedDict[UUID, _GateValue],
        event_id: UUID,
        value: _GateValue,
    ) -> None:
        items[event_id] = value
        items.move_to_end(event_id)
        while len(items) > self._limit:
            items.popitem(last=False)


async def run_confirmed_buy_monitor(
    *,
    ready_path: Path | None = None,
    bell: bool = True,
    stream: TextIO | None = None,
    leveraged_pairs: tuple[LeveragedPair, ...] | None = None,
) -> None:
    """Render confirmed directions with associated instruments and manual Flow alarms."""

    settings = AppSettings()
    if leveraged_pairs is None:
        leveraged_pairs = MarketBotAssembly.from_settings(settings).build_leveraged_thesis().pairs
    pairs = {pair.underlying_symbol: pair for pair in leveraged_pairs}
    output = stream or sys.stdout
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )
    sink = ConsoleAlertSink(stream=output, bell=False, color=True)

    displayed: set[UUID | str] = set()
    analytical_stages: dict[str, str] = {}
    confirmation_gate = _PersistedConfirmationGate()

    def emit_signal(signal: EntrySignal) -> None:
        projection = project_confirmed_signal(
            signal,
            color=True,
            instrument_symbol=(
                pairs[signal.symbol].bullish_instrument if signal.symbol in pairs else None
            ),
        )
        if projection is None:
            return
        print(projection.text, file=output, flush=True)
        if not bell:
            return
        if projection.sound_maturity is not None:
            play_buy_maturity_sound(projection.sound_maturity, fallback=output)
        else:
            play_solid_buy_sound(fallback=output)

    def emit_short(alert: LocalAlert) -> None:
        pair = pairs.get(alert.symbol)
        projection = project_confirmed_short(
            alert,
            color=True,
            instrument_symbol=pair.bearish_instrument if pair is not None else None,
        )
        if projection is None:
            return
        print(projection.text, file=output, flush=True)
        if bell:
            play_solid_buy_sound(fallback=output)

    def emit_persisted(candidate: EntrySignal | LocalAlert) -> None:
        if isinstance(candidate, EntrySignal):
            emit_signal(candidate)
        else:
            emit_short(candidate)

    async def handle_opportunity(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_OPPORTUNITY_EVENT or envelope.causation_id is None:
            return
        candidate = confirmation_gate.confirm(envelope.causation_id)
        if candidate is not None:
            emit_persisted(candidate)

    async def handle_signal(envelope: EventEnvelope) -> None:
        if envelope.event_type != ENTRY_SIGNAL_EVENT:
            return
        signal = (
            envelope.payload
            if isinstance(envelope.payload, EntrySignal)
            else EntrySignal.model_validate(envelope.payload, strict=False)
        )
        stage_changed = _analytical_stage_changed(signal, analytical_stages)
        if stage_changed is False:
            return
        display_key: UUID | str = signal.signal_id
        if display_key in displayed:
            return
        projection = project_confirmed_signal(
            signal,
            color=False,
            instrument_symbol=None,
        )
        if projection is None:
            return
        displayed.add(display_key)
        candidate = confirmation_gate.observe(signal.signal_id, signal)
        if candidate is not None:
            emit_persisted(candidate)

    async def handle_manual_flow(envelope: EventEnvelope) -> None:
        if envelope.event_type != LOCAL_ALERT_EVENT:
            return
        alert = (
            envelope.payload
            if isinstance(envelope.payload, LocalAlert)
            else LocalAlert.model_validate(envelope.payload, strict=False)
        )
        short = project_confirmed_short(
            alert,
            color=False,
            instrument_symbol=None,
        )
        if short is not None:
            display_key = f"short:{alert.deduplication_key}"
            if display_key in displayed:
                return
            displayed.add(display_key)
            candidate = confirmation_gate.observe(alert.alert_id, alert)
            if candidate is not None:
                emit_persisted(candidate)
            return
        if alert.kind not in {
            AlertKind.PORTFOLIO_PROTECT,
            AlertKind.PORTFOLIO_FLOW_BUY,
            AlertKind.LEVERAGED_THESIS_BUY,
            AlertKind.LEVERAGED_THESIS_CANCELLED,
        }:
            return
        sink.emit(alert)
        if not bell:
            return
        if alert.kind is AlertKind.PORTFOLIO_FLOW_BUY:
            play_aggressive_flow_sound(fallback=output)
        elif alert.kind is AlertKind.LEVERAGED_THESIS_BUY:
            play_solid_buy_sound(fallback=output)
        elif alert.kind is AlertKind.LEVERAGED_THESIS_CANCELLED:
            play_entry_close_sound(fallback=output)

    subscriptions = (
        await bus.subscribe(
            "marketbot.v1.entry-opportunity.transition.>",
            handle_opportunity,
            options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
        ),
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
                        "marketbot.v1.entry-opportunity.transition.>",
                        "marketbot.v1.entry-signal.>",
                        "marketbot.v1.alert.local.>",
                    ),
                    "replay": False,
                },
            )
        print(
            "COMPRAS Y SHORT CONFIRMADOS + TESIS APALANCADAS + PORTFOLIO FLOW - esperando NATS...",
            file=output,
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()


def _analytical_stage_changed(signal: EntrySignal, stages: dict[str, str]) -> bool | None:
    if signal.family is EntrySignalFamily.SWING_TRADE:
        stage = (
            signal.swing_trade_maturity.value if signal.swing_trade_maturity is not None else "NONE"
        )
    elif signal.family is EntrySignalFamily.GERI_COUNTERTREND:
        stage = (
            signal.countertrend_maturity.value
            if signal.countertrend_maturity is not None
            else "NONE"
        )
    else:
        return None
    key = f"{signal.family.value}:{signal.setup_id}"
    previous = stages.get(key)
    stages[key] = stage
    return previous != stage
