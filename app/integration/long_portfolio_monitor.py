"""Dedicated terminal monitor for persisted and live LONG portfolio alerts."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from app.alert_engine.sinks import ConsoleAlertSink
from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    LOCAL_ALERT_EVENT,
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    EventEnvelope,
    LocalAlert,
    SubscriptionOptions,
    analysis_result_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.long_portfolio_engine import (
    LongPortfolioEngine,
    LongPortfolioPolicy,
    LongPortfolioState,
    LongPortfolioValidationGate,
)
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import _write_ready  # pyright: ignore[reportPrivateUsage]
from .engine_assembly import EngineSlot, MarketBotAssembly
from .long_portfolio_store import PostgresLongPortfolioAlertStore
from .postgres_universe import PostgresUniverseClient

_PROGRESS_GATE_COUNT = 14


@dataclass(frozen=True, slots=True)
class _ProgressItem:
    symbol: str
    validation_gates: tuple[LongPortfolioValidationGate, ...]
    age_ok: bool
    age_detail: str
    qualified_sessions: int
    minimum_sessions: int
    cooldown_ok: bool
    cooldown_detail: str
    allocation_ok: bool
    allocation_detail: str


async def run_long_portfolio_monitor(
    *,
    ready_path: Path | None = None,
    bell: bool = True,
    history: int = 25,
    progress_interval: timedelta = timedelta(hours=1),
    config_path: Path | None = None,
) -> None:
    """Render PostgreSQL history first and then new LONG portfolio NATS alerts."""

    if progress_interval <= timedelta():
        raise ValueError("progress_interval must be positive")
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresLongPortfolioAlertStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError("market_bot.long_portfolio_alerts is not available")
    portfolio_data = PostgresUniverseClient(database)
    allocations = await portfolio_data.get_portfolio_allocations()
    policy = cast(
        "LongPortfolioPolicy",
        assembly.resolve_strategy(
            EngineSlot.LONG_PORTFOLIO,
            artifact_override=config_path,
            allocations=allocations,
        ),
    )
    validator = assembly.build_long_portfolio(
        allocations=allocations,
        strategy_artifact_override=config_path,
    )
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    sink = ConsoleAlertSink(stream=sys.stdout, bell=bell, color=True)
    displayed: set[UUID] = set()
    for alert in await store.recent(limit=history):
        displayed.add(alert.alert_id)
        sink.emit(alert)

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != LOCAL_ALERT_EVENT:
            return
        alert = (
            envelope.payload
            if isinstance(envelope.payload, LocalAlert)
            else LocalAlert.model_validate(envelope.payload, strict=False)
        )
        if alert.kind is AlertKind.LONG_PORTFOLIO_BUY and alert.alert_id not in displayed:
            displayed.add(alert.alert_id)
            sink.emit(alert)

    subscription = await bus.subscribe(
        "marketbot.v1.alert.local.>",
        handle,
        options=SubscriptionOptions(replay_all=False, ack_wait_seconds=60),
    )
    progress_task: asyncio.Task[None] | None = None
    try:
        if ready_path is not None:
            _write_ready(
                ready_path,
                {
                    "service": "long-portfolio-monitor",
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": assembly.spec(
                        EngineSlot.LONG_PORTFOLIO
                    ).implementation,
                    "engine_strategy_version": assembly.spec(
                        EngineSlot.LONG_PORTFOLIO
                    ).strategy.version,
                    "history": history,
                    "persistence": "postgresql",
                    "progress_interval_minutes": int(progress_interval.total_seconds() // 60),
                    "progress_symbols": len(allocations),
                },
            )
        print(
            "LONG PORTFOLIO — historial cargado; progreso horario + nuevas alertas...",
            flush=True,
        )
        progress_task = asyncio.create_task(
            _progress_loop(
                bus=bus,
                store=store,
                portfolio_data=portfolio_data,
                validator=validator,
                policy=policy,
                interval=progress_interval,
                clock=clock,
            )
        )
        await asyncio.Event().wait()
    finally:
        if progress_task is not None:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()


async def _progress_loop(
    *,
    bus: NatsJetStreamEventBus,
    store: PostgresLongPortfolioAlertStore,
    portfolio_data: PostgresUniverseClient,
    validator: LongPortfolioEngine,
    policy: LongPortfolioPolicy,
    interval: timedelta,
    clock: SystemClock,
) -> None:
    while True:
        try:
            await _print_progress_snapshot(
                bus=bus,
                store=store,
                portfolio_data=portfolio_data,
                validator=validator,
                policy=policy,
                now=clock.now(),
            )
        except Exception as error:
            print(f"LONG PORTFOLIO PROGRESS ERROR — {type(error).__name__}: {error}", flush=True)
        await asyncio.sleep(interval.total_seconds())


async def _print_progress_snapshot(
    *,
    bus: NatsJetStreamEventBus,
    store: PostgresLongPortfolioAlertStore,
    portfolio_data: PostgresUniverseClient,
    validator: LongPortfolioEngine,
    policy: LongPortfolioPolicy,
    now: datetime,
) -> None:
    states = {
        item.symbol: item for item in await store.load_states(rule_version=policy.rule_version)
    }
    holdings = await portfolio_data.get_holding_quantities()
    envelopes = await asyncio.gather(
        *(
            bus.get_last(analysis_result_subject(AnalysisHorizon.LONG_TERM, item.symbol))
            for item in policy.allocations
        )
    )
    print(
        f"\nLONG PORTFOLIO PROGRESS — {now:%Y-%m-%d %H:%M} UTC — AGE+10 LONG GATES+SES+CD+ALLOC",
        flush=True,
    )
    print(
        "GATES V=veredicto D=direccion SC=score C=confianza Z=buy-zone "
        "SET=setup ENT=entrada TR=trend REG=regimen RF=riesgo",
        flush=True,
    )
    for allocation, envelope in zip(policy.allocations, envelopes, strict=True):
        state = states.get(allocation.symbol)
        result: AnalysisResult | None = None
        if envelope is not None:
            result = (
                envelope.payload
                if isinstance(envelope.payload, AnalysisResult)
                else AnalysisResult.model_validate(envelope.payload, strict=False)
            )
        item = _progress_item(
            symbol=allocation.symbol,
            result=result,
            state=state,
            held_quantity=holdings.get(allocation.symbol, Decimal()),
            validator=validator,
            policy=policy,
            now=now,
        )
        print(_format_progress_line(item), flush=True)


def _progress_item(
    *,
    symbol: str,
    result: AnalysisResult | None,
    state: LongPortfolioState | None,
    held_quantity: Decimal,
    validator: LongPortfolioEngine,
    policy: LongPortfolioPolicy,
    now: datetime,
) -> _ProgressItem:
    sessions = state.qualified_sessions if state is not None else ()
    last_emitted = state.last_emitted if state is not None else None
    if last_emitted is None:
        cooldown_ok = True
        cooldown_detail = "ready"
    else:
        cooldown_elapsed = now - last_emitted
        cooldown_ok = cooldown_elapsed >= policy.cooldown
        cooldown_detail = (
            "ready"
            if cooldown_ok
            else f"{policy.cooldown - cooldown_elapsed} remaining"
        )
    if result is None:
        return _ProgressItem(
            symbol=symbol,
            validation_gates=(),
            age_ok=False,
            age_detail="no_long_result",
            qualified_sessions=len(sessions),
            minimum_sessions=policy.minimum_qualified_sessions,
            cooldown_ok=cooldown_ok,
            cooldown_detail=cooldown_detail,
            allocation_ok=True,
            allocation_detail="unknown_without_price",
        )
    age = now - result.as_of
    age_ok = result.as_of <= now and age <= policy.maximum_signal_age
    age_detail = (
        f"{age.total_seconds() / 3600:.1f}h<="
        f"{policy.maximum_signal_age.total_seconds() / 3600:.0f}h"
        if age_ok
        else ("future_result" if result.as_of > now else f"stale_{age.total_seconds() / 3600:.1f}h")
    )
    metrics = {item.name: item.value for item in result.metrics}
    price = _progress_decimal(metrics.get("reference_price"))
    allocation = policy.allocation_for(symbol)
    if price is None or allocation is None:
        allocation_ok = False
        allocation_detail = "missing_price_or_allocation"
    else:
        target = policy.portfolio_capital_usd * allocation.weight_percent / Decimal("100")
        remaining = max(target - held_quantity * price, Decimal())
        allocation_ok = remaining > 0
        allocation_detail = f"USD {remaining.quantize(Decimal('0.01'))} remaining"
    return _ProgressItem(
        symbol=symbol,
        validation_gates=validator.validation_gates(result),
        age_ok=age_ok,
        age_detail=age_detail,
        qualified_sessions=len(sessions),
        minimum_sessions=policy.minimum_qualified_sessions,
        cooldown_ok=cooldown_ok,
        cooldown_detail=cooldown_detail,
        allocation_ok=allocation_ok,
        allocation_detail=allocation_detail,
    )


def _format_progress_line(item: _ProgressItem) -> str:
    if not item.validation_gates:
        return (
            f"{item.symbol:<6} [{'-' * _PROGRESS_GATE_COUNT}] 0/{_PROGRESS_GATE_COUNT} "
            f"SES {item.qualified_sessions}/{item.minimum_sessions} | "
            f"DATA={item.age_detail}"
        )
    sessions_ok = item.qualified_sessions >= item.minimum_sessions
    statuses = (
        item.age_ok,
        *(gate.passed for gate in item.validation_gates),
        sessions_ok,
        item.cooldown_ok,
        item.allocation_ok,
    )
    passed = sum(statuses)
    bar = "#" * passed + "-" * (_PROGRESS_GATE_COUNT - passed)
    failures: list[str] = []
    if not item.age_ok:
        failures.append(f"AGE={item.age_detail}")
    failures.extend(
        f"{gate.code}={gate.detail}" for gate in item.validation_gates if not gate.passed
    )
    if not sessions_ok:
        failures.append(f"SES={item.qualified_sessions}/{item.minimum_sessions}")
    if not item.cooldown_ok:
        failures.append(f"CD={item.cooldown_detail}")
    if not item.allocation_ok:
        failures.append(f"ALLOC={item.allocation_detail}")
    suffix = "READY" if not failures else "FAIL " + "; ".join(failures)
    return (
        f"{item.symbol:<6} [{bar}] {passed}/{_PROGRESS_GATE_COUNT} "
        f"SES {item.qualified_sessions}/{item.minimum_sessions} | {suffix}"
    )


def _progress_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None
