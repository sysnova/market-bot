"""Terminal dashboards for operational scalp analysis and intraday paper trades."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TextIO
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    INTRADAY_OPPORTUNITY_TRANSITION_EVENT,
    ORDER_FLOW_STATE_EVENT,
    SCALP_ASSESSMENT_EVENT,
    EventEnvelope,
    IntradayOpportunity,
    IntradayOpportunityEvent,
    IntradayOpportunityStatus,
    OrderFlowState,
    OrderFlowWindow,
    ScalpAssessment,
    ScalpState,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import write_ready
from .engine_assembly import MarketBotAssembly
from .intraday_opportunity_report import summarize_intraday_opportunities
from .intraday_opportunity_store import PostgresIntradayOpportunityStore

_CLEAR_SCREEN = "\033[2J\033[H"
_FOUR_PLACES = Decimal("0.0001")
_NEW_YORK = ZoneInfo("America/New_York")
_SCALP_STATE_PRIORITY = {
    ScalpState.ENTRY_CONFIRMED: 6,
    ScalpState.MANAGING: 5,
    ScalpState.ARMED: 4,
    ScalpState.EXIT_CONFIRMED: 3,
    ScalpState.INVALIDATED: 2,
    ScalpState.WATCHING: 1,
}


class ScalpingDashboard:
    """Keep the newest Order Flow and Scalp snapshots per symbol."""

    def __init__(self, *, history: int) -> None:
        if history <= 0:
            raise ValueError("history must be positive")
        self.history = history
        self._flows: dict[str, OrderFlowState] = {}
        self._scalps: dict[str, ScalpAssessment] = {}

    def merge_flow(self, flow: OrderFlowState) -> bool:
        current = self._flows.get(flow.symbol)
        if current is not None and flow.occurred_at < current.occurred_at:
            return False
        changed = current != flow
        self._flows[flow.symbol] = flow
        return changed

    def merge_scalp(self, scalp: ScalpAssessment) -> bool:
        current = self._scalps.get(scalp.symbol)
        if current is not None and scalp.occurred_at < current.occurred_at:
            return False
        changed = current != scalp
        self._scalps[scalp.symbol] = scalp
        return changed

    def items(
        self,
    ) -> tuple[tuple[str, OrderFlowState | None, ScalpAssessment | None], ...]:
        symbols = set(self._flows) | set(self._scalps)

        def order(symbol: str) -> tuple[int, float, str]:
            flow = self._flows.get(symbol)
            scalp = self._scalps.get(symbol)
            priority = _SCALP_STATE_PRIORITY.get(scalp.state, 0) if scalp else 0
            updated_at = max(
                (
                    item.occurred_at
                    for item in (flow, scalp)
                    if item is not None
                ),
            )
            return (-priority, -updated_at.timestamp(), symbol)

        return tuple(
            (symbol, self._flows.get(symbol), self._scalps.get(symbol))
            for symbol in sorted(symbols, key=order)[: self.history]
        )


class IntradayOpportunityDashboard:
    """Keep the newest revision of recent intraday paper round trips."""

    def __init__(self, *, history: int) -> None:
        if history <= 0:
            raise ValueError("history must be positive")
        self.history = history
        self._items: dict[UUID, IntradayOpportunity] = {}

    def merge(self, opportunity: IntradayOpportunity) -> bool:
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
        self._trim()
        return changed

    def replace(self, opportunities: tuple[IntradayOpportunity, ...]) -> None:
        self._items = {item.opportunity_id: item for item in opportunities}
        self._trim()

    def items(self) -> tuple[IntradayOpportunity, ...]:
        return tuple(self._ordered())

    def _trim(self) -> None:
        retained = self._ordered()[: self.history]
        self._items = {item.opportunity_id: item for item in retained}

    def _ordered(self) -> list[IntradayOpportunity]:
        return sorted(
            self._items.values(),
            key=lambda item: (
                item.status is IntradayOpportunityStatus.CLOSED,
                -item.updated_at.timestamp(),
                item.symbol,
            ),
        )


def format_scalping_dashboard(
    dashboard: ScalpingDashboard, *, refreshed_at: datetime
) -> str:
    """Render live Order Flow plus actionable Scalp levels."""

    lines = [
        f"SCALPING | PAPER | REFRESH {refreshed_at.astimezone(_NEW_YORK):%Y-%m-%d %H:%M:%S %Z}",
        "ORDER FLOW 1/5/15/60/300s | sin ordenes de broker",
    ]
    items = dashboard.items()
    if not items:
        lines.append("Sin estados de microestructura recibidos.")
        return "\n".join(lines) + "\n"
    for symbol, flow, scalp in items:
        if flow is None:
            lines.append(f"\n{symbol} | ORDER FLOW pendiente")
        else:
            freshness = "FRESH" if flow.quote_fresh else "STALE"
            lines.append(
                f"\n{symbol} | OF {flow.state.value} CONF {_percent(flow.confidence)} "
                f"Q {_percent(flow.data_quality)} {freshness} PX {_number(flow.current_price)} "
                f"CVD {_signed(flow.cumulative_delta)}"
            )
            windows = {item.window_seconds: item for item in flow.windows}
            lines.append(
                "  "
                + " | ".join(
                    _format_flow_window(windows[seconds]) for seconds in (5, 15, 60, 300)
                )
            )
        if scalp is None:
            lines.append("  SCALP pendiente")
            continue
        lines.append(
            f"  SCALP {scalp.state.value} | {scalp.setup.value} {scalp.direction.value} "
            f"| VWAP {_number(scalp.session_vwap)} SPREAD {_number(scalp.spread_bps)}bps"
        )
        if (
            scalp.entry_price is not None
            and scalp.invalidation is not None
            and scalp.target is not None
        ):
            lines.append(
                f"  ENTRY {_number(scalp.entry_price)} | STOP {_number(scalp.invalidation)} "
                f"| TARGET {_number(scalp.target)} | MAX {scalp.max_hold_seconds}s"
            )
        lines.append(f"  RAZONES {','.join(scalp.reasons[-4:])}")
    return "\n".join(lines) + "\n"


def format_intraday_opportunity_dashboard(
    dashboard: IntradayOpportunityDashboard,
    *,
    refreshed_at: datetime,
    days: int,
) -> str:
    """Render open paper P/L and closed-trade effectiveness."""

    end_date = refreshed_at.astimezone(_NEW_YORK).date()
    start_date = end_date - timedelta(days=days - 1)
    items = dashboard.items()
    report = summarize_intraday_opportunities(
        items,
        start_date=start_date,
        end_date=end_date,
    )
    hit_rate = report["effectiveness_rate_percent"] or "-"
    expectancy = report["expectancy_net_percent"] or "-"
    lines = [
        f"INTRADAY OPS | PAPER | {start_date} -> {end_date} | "
        f"REFRESH {refreshed_at.astimezone(_NEW_YORK):%H:%M:%S %Z}",
        f"OPEN {report['open']} | CLOSED {report['closed']} | "
        f"W/L {report['wins']}/{report['losses']} | HIT {hit_rate}% | "
        f"EXPECT {expectancy}% | NET {report['total_net_pnl']}",
    ]
    if not items:
        lines.append("Sin operaciones intraday registradas en el periodo.")
        return "\n".join(lines) + "\n"
    for item in items:
        close = f" | EXIT {item.close_reason.value}" if item.close_reason is not None else ""
        lines.append(
            f"\n{item.symbol} {item.side.value} {item.status.value} | "
            f"ENTRY {_number(item.entry_price)} | MARK {_number(item.current_price)} "
            f"| P/L {_signed(item.net_pnl)} ({_signed(item.net_pnl_percent)}%)"
            f"{close}"
        )
        lines.append(
            f"  STOP {_number(item.stop_price)} | TARGET {_number(item.target_price)} "
            f"| MFE {_signed(item.mfe_percent)}% MAE {_signed(item.mae_percent)}% "
            f"| REV {item.revision} | OPENED {item.opened_at.astimezone(_NEW_YORK):%H:%M:%S}"
        )
    return "\n".join(lines) + "\n"


async def run_scalping_monitor(
    *,
    ready_path: Path | None = None,
    history: int = 40,
    refresh_interval: timedelta = timedelta(seconds=1),
    stream: TextIO | None = None,
) -> None:
    """Show compact live Order Flow and Scalp assessment state."""

    import sys

    if refresh_interval <= timedelta():
        raise ValueError("refresh_interval must be positive")
    output = stream or sys.stdout
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    dashboard = ScalpingDashboard(history=history)
    changed = asyncio.Event()
    lock = asyncio.Lock()
    clock = SystemClock()

    def render() -> None:
        print(
            _CLEAR_SCREEN + format_scalping_dashboard(dashboard, refreshed_at=clock.now()),
            file=output,
            end="",
            flush=True,
        )

    async def handle_flow(envelope: EventEnvelope) -> None:
        if envelope.event_type != ORDER_FLOW_STATE_EVENT:
            return
        flow = _payload(envelope, OrderFlowState)
        async with lock:
            if dashboard.merge_flow(flow):
                changed.set()

    async def handle_scalp(envelope: EventEnvelope) -> None:
        if envelope.event_type != SCALP_ASSESSMENT_EVENT:
            return
        scalp = _payload(envelope, ScalpAssessment)
        async with lock:
            if dashboard.merge_scalp(scalp):
                changed.set()

    subscriptions = (
        await bus.subscribe(
            "marketbot.v1.order-flow.state.>",
            handle_flow,
            options=SubscriptionOptions(replay_latest_per_subject=True),
        ),
        await bus.subscribe(
            "marketbot.v1.scalp.assessment.>",
            handle_scalp,
            options=SubscriptionOptions(replay_latest_per_subject=True),
        ),
    )
    render_task: asyncio.Task[None] | None = None
    try:
        render()
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "scalping-monitor",
                    "subjects": [
                        "marketbot.v1.order-flow.state.>",
                        "marketbot.v1.scalp.assessment.>",
                    ],
                    "mode": "PAPER",
                    "marketbot_definition_version": assembly.definition.version,
                    "event_driven": True,
                },
            )
        render_task = asyncio.create_task(
            _render_when_changed(
                changed=changed,
                lock=lock,
                render=render,
                refresh_interval=refresh_interval,
            )
        )
        await asyncio.Event().wait()
    finally:
        if render_task is not None:
            render_task.cancel()
            await asyncio.gather(render_task, return_exceptions=True)
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await bus.close()


async def run_intraday_opportunity_monitor(
    *,
    ready_path: Path | None = None,
    history: int = 50,
    days: int = 7,
    refresh_interval: timedelta = timedelta(seconds=10),
    stream: TextIO | None = None,
) -> None:
    """Show persisted and event-driven intraday paper P/L."""

    import sys

    if not 1 <= days <= 30:
        raise ValueError("days must be between 1 and 30")
    if refresh_interval <= timedelta():
        raise ValueError("refresh_interval must be positive")
    output = stream or sys.stdout
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresIntradayOpportunityStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError("intraday opportunity migration is not applied")
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    dashboard = IntradayOpportunityDashboard(history=history)
    clock = SystemClock()
    lock = asyncio.Lock()

    def render() -> None:
        print(
            _CLEAR_SCREEN
            + format_intraday_opportunity_dashboard(
                dashboard,
                refreshed_at=clock.now(),
                days=days,
            ),
            file=output,
            end="",
            flush=True,
        )

    async def load() -> tuple[IntradayOpportunity, ...]:
        today = clock.now().astimezone(_NEW_YORK).date()
        return await _load_intraday_period(store=store, end_date=today, days=days)

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != INTRADAY_OPPORTUNITY_TRANSITION_EVENT:
            return
        event = _payload(envelope, IntradayOpportunityEvent)
        async with lock:
            if dashboard.merge(event.opportunity):
                render()

    subscription = await bus.subscribe(
        "marketbot.v1.intraday-opportunity.transition.>",
        handle,
        options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
    )
    refresh_task: asyncio.Task[None] | None = None
    try:
        dashboard.replace(await load())
        render()
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "intraday-opportunity-monitor",
                    "subject": "marketbot.v1.intraday-opportunity.transition.>",
                    "mode": "PAPER",
                    "persistence": "postgresql",
                    "days": days,
                    "marketbot_definition_version": assembly.definition.version,
                    "event_driven": True,
                },
            )
        refresh_task = asyncio.create_task(
            _refresh_intraday_dashboard(
                dashboard=dashboard,
                load=load,
                render=render,
                lock=lock,
                interval=refresh_interval,
            )
        )
        await asyncio.Event().wait()
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()


async def _render_when_changed(
    *,
    changed: asyncio.Event,
    lock: asyncio.Lock,
    render: Callable[[], None],
    refresh_interval: timedelta,
) -> None:
    while True:
        with suppress(TimeoutError):
            await asyncio.wait_for(changed.wait(), timeout=refresh_interval.total_seconds())
        changed.clear()
        async with lock:
            render()


async def _refresh_intraday_dashboard(
    *,
    dashboard: IntradayOpportunityDashboard,
    load: Callable[[], Awaitable[tuple[IntradayOpportunity, ...]]],
    render: Callable[[], None],
    lock: asyncio.Lock,
    interval: timedelta,
) -> None:
    while True:
        await asyncio.sleep(interval.total_seconds())
        opportunities = await load()
        async with lock:
            dashboard.replace(opportunities)
            render()


async def _load_intraday_period(
    *,
    store: PostgresIntradayOpportunityStore,
    end_date: date,
    days: int,
) -> tuple[IntradayOpportunity, ...]:
    items: list[IntradayOpportunity] = []
    for offset in range(days):
        items.extend(await store.list_session(end_date - timedelta(days=offset)))
    return tuple(items)


def _payload[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)


def _format_flow_window(window: OrderFlowWindow) -> str:
    return (
        f"D{window.window_seconds} {_signed(window.delta)} "
        f"V{window.trade_count} P{_signed(window.price_change_bps)}bps"
    )


def _number(value: Decimal) -> str:
    return str(value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP))


def _signed(value: Decimal) -> str:
    return f"{value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP):+}"


def _percent(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
