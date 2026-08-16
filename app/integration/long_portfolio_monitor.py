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
    print(f"\nPORTFOLIO 2026 — progreso LONG — {now:%Y-%m-%d %H:%M} UTC", flush=True)
    print(
        "Cada ticker requiere 14 condiciones. CUMPLE muestra lo aprobado; "
        "FALTA explica qué bloquea la alerta.",
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
            f"{item.symbol:<6} SIN ANÁLISIS LONG "
            f"[{'-' * _PROGRESS_GATE_COUNT}] 0/{_PROGRESS_GATE_COUNT}\n"
            "       CUMPLE: ninguna condición verificable todavía\n"
            f"       FALTA: {_human_age_detail(item.age_detail, passed=False)}"
        )
    checks = _human_progress_checks(item)
    passed = sum(check_passed for check_passed, _ in checks)
    bar = "#" * passed + "-" * (_PROGRESS_GATE_COUNT - passed)
    fulfilled = [description for check_passed, description in checks if check_passed]
    missing = [description for check_passed, description in checks if not check_passed]
    if not missing:
        status = "LISTO PARA ALERTA"
    elif not item.allocation_ok and item.allocation_detail.startswith("USD 0"):
        status = "OBJETIVO CUBIERTO"
    elif not item.cooldown_ok:
        status = "EN COOLDOWN"
    else:
        status = "ESPERANDO CONDICIONES"
    return (
        f"{item.symbol:<6} {status} [{bar}] {passed}/{_PROGRESS_GATE_COUNT}\n"
        f"       CUMPLE: {'; '.join(fulfilled) if fulfilled else 'ninguna todavía'}\n"
        f"       FALTA: {'; '.join(missing) if missing else 'ninguna; todo está cumplido'}"
    )


def _human_progress_checks(item: _ProgressItem) -> tuple[tuple[bool, str], ...]:
    sessions_ok = item.qualified_sessions >= item.minimum_sessions
    return (
        (item.age_ok, _human_age_detail(item.age_detail, passed=item.age_ok)),
        *((gate.passed, _human_gate_detail(gate)) for gate in item.validation_gates),
        (
            sessions_ok,
            f"confirmaciones {item.qualified_sessions}/{item.minimum_sessions} sesiones",
        ),
        (item.cooldown_ok, _human_cooldown_detail(item)),
        (item.allocation_ok, _human_allocation_detail(item)),
    )


def _human_gate_detail(gate: LongPortfolioValidationGate) -> str:
    if gate.code == "V":
        actual = gate.detail.split("!=", maxsplit=1)[0]
        description = f"veredicto {_human_token(actual)}"
        return description if gate.passed else f"{description} (requiere favorable)"
    if gate.code == "D":
        actual = gate.detail.split("!=", maxsplit=1)[0]
        description = f"dirección {_human_token(actual)}"
        return description if gate.passed else f"{description} (requiere alcista)"
    if gate.code == "SC":
        return _human_minimum_detail("score general", gate.detail)
    if gate.code == "C":
        return _human_minimum_detail("confianza", gate.detail, percentage=True)
    if gate.code == "Z":
        actual = gate.detail.split("!=", maxsplit=1)[0]
        if gate.passed:
            return "precio dentro de la zona de compra"
        return f"precio {_human_token(actual)} (requiere zona de compra)"
    if gate.code == "SET":
        return _human_minimum_detail("calidad del setup", gate.detail)
    if gate.code == "ENT":
        return _human_minimum_detail("calidad de entrada", gate.detail)
    if gate.code == "TR":
        return _human_minimum_detail("tendencia estructural", gate.detail)
    if gate.code == "REG":
        if gate.passed:
            return "régimen de mercado permitido"
        actual = gate.detail.removesuffix(" not_allowed")
        return f"régimen {_human_token(actual)} no permitido"
    if gate.code == "RF":
        if gate.passed:
            return "sin riesgos semanales bloqueantes"
        risks = ", ".join(_human_token(value) for value in gate.detail.split(","))
        return f"riesgo semanal: {risks}"
    return f"{gate.code}: {_human_token(gate.detail)}"


def _human_minimum_detail(label: str, detail: str, *, percentage: bool = False) -> str:
    if "<" not in detail:
        return f"{label} {_human_number(detail, percentage=percentage)}"
    actual, minimum = detail.split("<", maxsplit=1)
    return (
        f"{label} {_human_number(actual, percentage=percentage)} "
        f"(mínimo {_human_number(minimum, percentage=percentage)})"
    )


def _human_number(value: str, *, percentage: bool) -> str:
    if not percentage:
        return value
    try:
        percent = Decimal(value) * Decimal("100")
    except ArithmeticError:
        return value
    rendered = format(percent, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered}%"


def _human_age_detail(detail: str, *, passed: bool) -> str:
    if detail == "no_long_result":
        return "todavía no existe un análisis LONG para este ticker"
    if detail == "future_result":
        return "el análisis tiene una fecha futura y no puede utilizarse"
    if detail.startswith("stale_"):
        return f"análisis vencido ({detail.removeprefix('stale_')})"
    if passed and "<=" in detail:
        age, maximum = detail.split("<=", maxsplit=1)
        return f"análisis vigente ({age}; máximo {maximum})"
    return f"vigencia del análisis: {_human_token(detail)}"


def _human_cooldown_detail(item: _ProgressItem) -> str:
    if item.cooldown_ok:
        return "sin espera por cooldown"
    remaining = item.cooldown_detail.removesuffix(" remaining")
    return f"cooldown activo ({remaining} restante)"


def _human_allocation_detail(item: _ProgressItem) -> str:
    detail = item.allocation_detail
    if detail.startswith("USD ") and detail.endswith(" remaining"):
        amount = detail.removesuffix(" remaining")
        if item.allocation_ok:
            return f"cupo disponible {amount}"
        return "sin cupo; el objetivo de cartera ya está cubierto"
    if detail == "missing_price_or_allocation":
        return "no se pudo calcular el cupo por falta de precio o asignación"
    if detail == "unknown_without_price":
        return "no se pudo calcular el cupo porque todavía no hay precio"
    return f"cupo de cartera: {_human_token(detail)}"


def _human_token(value: str) -> str:
    translations = {
        "FAVORABLE": "favorable",
        "WATCH": "en observación",
        "CAUTION": "con precaución",
        "BULLISH": "alcista",
        "BEARISH": "bajista",
        "NEUTRAL": "neutral",
        "buy_zone": "en zona de compra",
        "watch_pullback": "esperando el retroceso",
        "below_weekly_200w": "debajo de la media de 200 semanas",
        "weekly_structure_broken": "estructura semanal rota",
        "weekly_distribution": "distribución semanal",
        "weekly_rsi_hot": "RSI semanal sobrecomprado",
        "extended_from_30w": "extendido respecto de la media de 30 semanas",
        "extended_from_50w": "extendido respecto de la media de 50 semanas",
        "clean_uptrend": "tendencia alcista ordenada",
        "volatile_uptrend": "tendencia alcista volátil",
    }
    return translations.get(value, value.replace("_", " ").lower())


def _progress_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None
