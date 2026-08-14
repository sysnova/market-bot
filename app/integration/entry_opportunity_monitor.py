"""Event-driven terminal dashboard for persisted Entry Opportunities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TextIO
from uuid import UUID

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ENTRY_OPPORTUNITY_EVENT,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EventEnvelope,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import write_ready
from .entry_opportunity_store import PostgresEntryOpportunityStore

_CLEAR_SCREEN = "\033[2J\033[H"
_FOUR_PLACES = Decimal("0.0001")
_RESET_STYLE = "\033[0m"
_TICKER_STYLE = "\033[1;96m"
_ENTRY_STYLE = "\033[1;93m"
_EXIT_STYLE = "\033[1;95m"
_POSITIVE_STYLE = "\033[1;92m"
_NEGATIVE_STYLE = "\033[1;91m"
_NEUTRAL_STYLE = "\033[1;93m"


class OpportunityDashboard:
    """Retain the newest materialized snapshot for each recent opportunity."""

    def __init__(self, *, history: int) -> None:
        if history <= 0:
            raise ValueError("history must be positive")
        self.history = history
        self._items: dict[UUID, EntryOpportunity] = {}
        self._reasons: dict[UUID, tuple[str, ...]] = {}
        self._focused_opportunity_id: UUID | None = None

    def merge(
        self,
        opportunity: EntryOpportunity,
        *,
        reasons: tuple[str, ...] = (),
        focus: bool = False,
    ) -> bool:
        current = self._items.get(opportunity.opportunity_id)
        if current is not None and (
            opportunity.revision < current.revision
            or (
                opportunity.revision == current.revision
                and opportunity.updated_at < current.updated_at
            )
        ):
            return False
        changed = current != opportunity
        self._items[opportunity.opportunity_id] = opportunity
        if reasons:
            self._reasons[opportunity.opportunity_id] = reasons
        if focus:
            self._focused_opportunity_id = opportunity.opportunity_id
        self._trim()
        return changed or bool(reasons)

    def items(self) -> tuple[EntryOpportunity, ...]:
        ordered = list(self._sorted_items())
        if self._focused_opportunity_id is not None:
            focused = next(
                (
                    item
                    for item in ordered
                    if item.opportunity_id == self._focused_opportunity_id
                ),
                None,
            )
            if focused is not None:
                ordered.remove(focused)
                ordered.append(focused)
        return tuple(ordered)

    def is_focused(self, opportunity_id: UUID) -> bool:
        return opportunity_id == self._focused_opportunity_id

    def reasons_for(self, opportunity_id: UUID) -> tuple[str, ...]:
        return self._reasons.get(opportunity_id, ())

    def _trim(self) -> None:
        retained = self._sorted_items()[: self.history]
        retained_ids = {item.opportunity_id for item in retained}
        self._items = {item.opportunity_id: item for item in retained}
        self._reasons = {
            key: value for key, value in self._reasons.items() if key in retained_ids
        }
        if self._focused_opportunity_id not in retained_ids:
            self._focused_opportunity_id = None

    def _sorted_items(self) -> tuple[EntryOpportunity, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (
                    item.status is EntryOpportunityStatus.CLOSED,
                    -item.updated_at.timestamp(),
                    item.symbol,
                ),
            )
        )


async def run_entry_opportunity_monitor(
    *,
    ready_path: Path | None = None,
    history: int = 100,
    refresh_interval: timedelta = timedelta(seconds=30),
    stream: TextIO | None = None,
) -> None:
    """Render PostgreSQL state and redraw immediately for every opportunity event."""

    import sys

    if refresh_interval <= timedelta():
        raise ValueError("refresh_interval must be positive")
    output = stream or sys.stdout
    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresEntryOpportunityStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError(
            "entry opportunity schema is unavailable; apply "
            "20260807010000_entry_opportunity_lifecycle.sql"
        )
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()],
        prefix="marketbot",
        stream="MARKETBOT",
    )
    dashboard = OpportunityDashboard(history=history)
    clock = SystemClock()
    lock = asyncio.Lock()
    color = _supports_color(output)

    def render() -> None:
        snapshot = format_opportunity_dashboard(
            dashboard,
            refreshed_at=clock.now(),
            color=color,
        )
        print(f"{_CLEAR_SCREEN}{snapshot}", file=output, end="", flush=True)

    async def handle(envelope: EventEnvelope) -> None:
        event = _opportunity_event(envelope)
        if event is None:
            return
        async with lock:
            dashboard.merge(event.opportunity, reasons=event.reasons, focus=True)
            render()

    subscription = await bus.subscribe(
        "marketbot.v1.entry-opportunity.transition.>",
        handle,
        options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
    )
    refresh_task: asyncio.Task[None] | None = None
    try:
        refreshed_at = clock.now()
        opportunities = await _load_tracked_opportunities(
            store=store,
            history=history,
            refreshed_at=refreshed_at,
        )
        latest_events = await store.latest_events(
            tuple(item.opportunity_id for item in opportunities)
        )
        reasons_by_id = {
            item.opportunity.opportunity_id: item.reasons for item in latest_events
        }
        async with lock:
            for opportunity in opportunities:
                dashboard.merge(
                    opportunity,
                    reasons=reasons_by_id.get(opportunity.opportunity_id, ()),
                )
            render()
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "entry-opportunity-monitor",
                    "subject": "marketbot.v1.entry-opportunity.transition.>",
                    "history": history,
                    "refresh_interval_seconds": int(refresh_interval.total_seconds()),
                    "persistence": "postgresql",
                    "event_driven": True,
                },
            )
        refresh_task = asyncio.create_task(
            _refresh_from_postgres(
                store=store,
                dashboard=dashboard,
                history=history,
                interval=refresh_interval,
                lock=lock,
                render=render,
                output=output,
                clock=clock,
            )
        )
        await asyncio.Event().wait()
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()


async def _refresh_from_postgres(
    *,
    store: PostgresEntryOpportunityStore,
    dashboard: OpportunityDashboard,
    history: int,
    interval: timedelta,
    lock: asyncio.Lock,
    render: Callable[[], None],
    output: TextIO,
    clock: SystemClock,
) -> None:
    while True:
        await asyncio.sleep(interval.total_seconds())
        try:
            refreshed_at = clock.now()
            opportunities = await _load_tracked_opportunities(
                store=store,
                history=history,
                refreshed_at=refreshed_at,
            )
            latest_events = await store.latest_events(
                tuple(item.opportunity_id for item in opportunities)
            )
            reasons_by_id = {
                item.opportunity.opportunity_id: item.reasons for item in latest_events
            }
            async with lock:
                for opportunity in opportunities:
                    dashboard.merge(
                        opportunity,
                        reasons=reasons_by_id.get(opportunity.opportunity_id, ()),
                    )
                render()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(
                f"\nPOSTGRES REFRESH ERROR {type(error).__name__}: {error}",
                file=output,
                flush=True,
            )


async def _load_tracked_opportunities(
    *,
    store: PostgresEntryOpportunityStore,
    history: int,
    refreshed_at: datetime,
) -> tuple[EntryOpportunity, ...]:
    recent, active = await asyncio.gather(
        store.list_recent(limit=history),
        store.list_active(),
    )
    today = refreshed_at.date()
    by_id = {
        item.opportunity_id: item
        for item in recent
        if item.status is not EntryOpportunityStatus.CLOSED
        or (item.closed_at is not None and item.closed_at.date() == today)
    }
    for item in active:
        current = by_id.get(item.opportunity_id)
        if current is None or item.revision >= current.revision:
            by_id[item.opportunity_id] = item
    return tuple(by_id.values())


def _opportunity_event(envelope: EventEnvelope) -> EntryOpportunityEvent | None:
    if envelope.event_type != ENTRY_OPPORTUNITY_EVENT:
        return None
    return (
        envelope.payload
        if isinstance(envelope.payload, EntryOpportunityEvent)
        else EntryOpportunityEvent.model_validate(envelope.payload, strict=False)
    )


def format_opportunity_dashboard(
    dashboard: OpportunityDashboard,
    *,
    refreshed_at: datetime,
    color: bool = False,
) -> str:
    """Build a stable, terminal-friendly complete opportunity snapshot."""

    opportunities = dashboard.items()
    active = sum(item.status is not EntryOpportunityStatus.CLOSED for item in opportunities)
    lines = [
        (
            f"ENTRY OPPORTUNITIES | {refreshed_at:%Y-%m-%d %H:%M:%S %Z} | "
            f"TOTAL {len(opportunities)} | ACTIVAS {active} | "
            f"CERRADAS {len(opportunities) - active}"
        ),
        "Evento NATS: inmediato | Respaldo PostgreSQL: periodico | "
        "P/L LIVE: no realizado | G/L: cierre auditado",
        "=" * 118,
    ]
    if not opportunities:
        lines.append("No hay oportunidades registradas en PostgreSQL.")
        return "\n".join(lines) + "\n"
    for opportunity in opportunities:
        if dashboard.is_focused(opportunity.opportunity_id):
            reasons = dashboard.reasons_for(opportunity.opportunity_id)
            lines.extend(
                (
                    "",
                    (
                        f">>> ACTUALIZACION RECIENTE NATS | {opportunity.symbol} | "
                        f"UPDATED {opportunity.updated_at:%Y-%m-%d %H:%M:%S} | "
                        f"REV {opportunity.revision} | "
                        f"{','.join(reasons) if reasons else '-'}"
                    ),
                    ">>> Snapshot actualizado movido al final del monitor",
                )
            )
        lines.extend(
            _format_opportunity(
                opportunity,
                dashboard.reasons_for(opportunity.opportunity_id),
                color=color,
            )
        )
    return "\n".join(lines) + "\n"


def _format_opportunity(
    opportunity: EntryOpportunity,
    reasons: tuple[str, ...],
    *,
    color: bool,
) -> list[str]:
    filled = min(10, max(0, int(opportunity.progress_percent / Decimal("10"))))
    progress = f"[{'#' * filled}{'-' * (10 - filled)}]"
    core_family = opportunity.primary_signal_family.value.startswith("CORE_")
    decision = (
        f"MAT {opportunity.current_maturity.value:<7} "
        f"PEAK {opportunity.peak_maturity.value:<7}"
        if core_family
        else f"FAMILY {opportunity.primary_signal_family.value:<16}"
    )
    closed = ""
    if opportunity.status is EntryOpportunityStatus.CLOSED:
        assert opportunity.closed_at is not None
        assert opportunity.close_reason is not None
        closed = (
            f" | CLOSE {opportunity.closed_at:%Y-%m-%d %H:%M} "
            f"{opportunity.close_reason.value}"
        )
    lines = [
        "",
        (
            f"{_styled(opportunity.symbol, _TICKER_STYLE, color):<7} "
            f"{opportunity.status.value:<10} "
            f"{decision} {progress} "
            f"{opportunity.progress_percent}% | REV {opportunity.revision}{closed}"
        ),
        *_trade_summary_lines(opportunity, color=color),
        (
            f"  ORIG {opportunity.original_price} | PX {opportunity.current_price} | "
            f"ZONE {opportunity.zone_low}-{opportunity.zone_high} | "
            f"INV {opportunity.invalidation} | EXPIRES {opportunity.expires_at:%Y-%m-%d %H:%M}"
        ),
        (
            f"  ARMED {opportunity.armed_at:%Y-%m-%d %H:%M} | "
            f"UPDATED {opportunity.updated_at:%Y-%m-%d %H:%M:%S} | "
            f"WATCH {str(opportunity.original_watch_id) if opportunity.original_watch_id else '-'}"
        ),
        (
            f"  SOURCE ANALYSES {len(opportunity.source_analysis_ids)} | "
            f"SIGNALS {len(opportunity.signal_references)} | "
            f"ULTIMO EVENTO {','.join(reasons) if reasons else '-'}"
        ),
        "  CHECKPOINTS DE MADURACION",
    ]
    for item in opportunity.checkpoints:
        setup = f"{item.level.value}/{item.signal_family.value}"
        tracking = item.level in {
            EntryMaturityLevel.ARMED,
            EntryMaturityLevel.IN_ZONE,
        }
        if tracking:
            price_label = "REFERENCE"
            performance = (
                f"MOVE FINAL {_percent_text(item.gain_loss_percent)}"
                if item.status is EntryCheckpointStatus.CLOSED
                else f"MOVE LIVE {_live_percent(item.entry_price, item.current_price)}"
            )
        else:
            price_label = "ENTRY"
            performance = (
                f"G/L {_percent_text(item.gain_loss_percent)}"
                if item.status is EntryCheckpointStatus.CLOSED
                else f"P/L LIVE {_live_percent(item.entry_price, item.current_price)}"
            )
        lines.append(
            f"    {setup:<28} {item.status.value:<7} "
            f"{price_label} {item.entry_price} PX {item.current_price} "
            f"EXIT {item.exit_price or '-'} {performance} | "
            f"INV {item.invalidation} TARGET {item.target or '-'} | "
            f"MFE {_percent_text(item.mfe_percent)} MAE {_percent_text(item.mae_percent)} | "
            f"15m {_percent_text(item.return_15m)} 30m {_percent_text(item.return_30m)} "
            f"60m {_percent_text(item.return_60m)} CLOSE {_percent_text(item.return_close)} | "
            f"REACHED {item.reached_at:%m-%d %H:%M} "
            f"CLOSED {item.closed_at.strftime('%m-%d %H:%M') if item.closed_at else '-'} "
            f"SETUP {item.setup_id or '-'} "
            f"OUTCOME {item.outcome.value if item.outcome else '-'}"
        )
    lines.append("  LEGS POR HORIZONTE")
    if not opportunity.legs:
        lines.append("    -")
    for leg in opportunity.legs:
        entry = leg.entry_price
        tracking = leg.status is EntryLegStatus.WATCHING and entry is None
        if tracking:
            price_label = "REFERENCE"
            performance = "MOVE -"
        else:
            price_label = "ENTRY"
            performance = (
                f"G/L {_percent_text(leg.gain_loss_percent)}"
                if leg.status not in {EntryLegStatus.WATCHING, EntryLegStatus.OPEN}
                else (
                    f"P/L LIVE {_live_percent(entry, leg.current_price)}"
                    if entry is not None
                    else "P/L LIVE -"
                )
            )
        lines.append(
            f"    {leg.horizon.value:<10} {leg.status.value:<15} "
            f"{price_label} {entry or '-'} PX {leg.current_price} "
            f"EXIT {leg.exit_price or '-'} "
            f"{performance} | INV {leg.invalidation} TARGET {leg.target or '-'} | "
            f"MFE {_percent_text(leg.mfe_percent)} MAE {_percent_text(leg.mae_percent)} | "
            f"OPENED {leg.opened_at.strftime('%m-%d %H:%M') if leg.opened_at else '-'} "
            f"CLOSED {leg.closed_at.strftime('%m-%d %H:%M') if leg.closed_at else '-'}"
        )
    lines.append("  ANALISIS VIGENTES")
    if not opportunity.latest_analyses:
        lines.append("    -")
    for analysis in opportunity.latest_analyses:
        lines.append(
            f"    {analysis.horizon.value:<10} {analysis.verdict.value:<8} "
            f"{analysis.direction.value:<8} SCORE {analysis.score} CONF {analysis.confidence} "
            f"ASOF {analysis.as_of:%Y-%m-%d %H:%M} ENGINE {analysis.engine_id}"
        )
    lines.append("-" * 118)
    return lines


def _trade_summary_lines(
    opportunity: EntryOpportunity,
    *,
    color: bool,
) -> list[str]:
    lines: list[str] = []
    for checkpoint in opportunity.checkpoints:
        tracking = checkpoint.level in {
            EntryMaturityLevel.ARMED,
            EntryMaturityLevel.IN_ZONE,
        }
        closed = checkpoint.status is EntryCheckpointStatus.CLOSED
        if closed:
            assert checkpoint.exit_price is not None
            assert checkpoint.gain_loss_percent is not None
            mark_label = "SALIDA"
            mark = checkpoint.exit_price
            performance = checkpoint.gain_loss_percent
        else:
            mark_label = "MARCA"
            mark = checkpoint.current_price
            performance = _live_percent_value(
                checkpoint.entry_price,
                checkpoint.current_price,
            )
        summary_label = "REFERENCIA" if tracking else "COMPRA"
        price_label = "PRECIO" if tracking else "ENTRADA"
        lines.append(
            f"  {summary_label} {_styled(opportunity.symbol, _TICKER_STYLE, color)} | "
            f"MADUREZ {checkpoint.level.value} | ESTADO {checkpoint.status.value} | "
            f"{price_label} {_styled(str(checkpoint.entry_price), _ENTRY_STYLE, color)} | "
            f"{mark_label} {_styled(str(mark), _EXIT_STYLE, color)} | "
            f"P/L {_styled_percent(performance, color=color)}"
        )
    return lines


def _live_percent(entry: Decimal, current: Decimal) -> str:
    return _percent_text(_live_percent_value(entry, current))


def _live_percent_value(entry: Decimal, current: Decimal) -> Decimal:
    return (current - entry) / entry * Decimal("100")


def _styled_percent(value: Decimal, *, color: bool) -> str:
    if value > 0:
        style = _POSITIVE_STYLE
    elif value < 0:
        style = _NEGATIVE_STYLE
    else:
        style = _NEUTRAL_STYLE
    return _styled(_percent_text(value), style, color)


def _styled(value: str, style: str, enabled: bool) -> str:
    return f"{style}{value}{_RESET_STYLE}" if enabled else value


def _supports_color(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty is not None and isatty())


def _percent_text(value: Decimal | None) -> str:
    if value is None:
        return "-"
    rounded = value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)
    return f"{rounded:+}%"
