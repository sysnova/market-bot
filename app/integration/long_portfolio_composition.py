"""NATS composition for the versioned LONG portfolio entry monitor."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.alert_engine.sinks import NdjsonAlertSink
from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    LOCAL_ALERT_EVENT,
    AnalysisResult,
    EventEnvelope,
    SubscriptionOptions,
    local_alert_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.long_portfolio_engine import load_long_portfolio_policy
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import _write_ready  # pyright: ignore[reportPrivateUsage]
from .engine_assembly import EngineSlot, MarketBotAssembly
from .long_portfolio_store import PostgresLongPortfolioAlertStore
from .postgres_universe import PostgresUniverseClient


async def run_long_portfolio_process(
    *,
    config_path: Path | None = None,
    runtime_root: Path = Path(".runtime"),
    ready_path: Path | None = None,
    once: bool = False,
    symbol: str | None = None,
) -> dict[str, object] | None:
    """Replay recent Long results, then monitor new ones and publish durable alerts."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    portfolio_data = PostgresUniverseClient(database)
    allocations = await portfolio_data.get_portfolio_allocations()
    policy = load_long_portfolio_policy(
        config_path or assembly.strategy_artifact(EngineSlot.LONG_PORTFOLIO),
        allocations=allocations,
    )
    requested = symbol.strip().upper() if symbol is not None else None
    if requested is not None and policy.allocation_for(requested) is None:
        await database.dispose()
        return {
            "service": "long-portfolio-v1",
            "requested_symbol": requested,
            "eligible": False,
            "reason": "PORT_YTD_allocation_required",
            "execution_enabled": False,
        }
    ledger = NdjsonAlertSink(runtime_root / "alerts" / "long-portfolio-alerts.ndjson")
    clock = SystemClock()
    store = PostgresLongPortfolioAlertStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError(
            "LONG portfolio alert schema is unavailable; apply "
            "20260802223000_long_portfolio_states.sql"
        )
    engine = assembly.build_long_portfolio(
        policy,
        restored_states=await store.load_states(rule_version=policy.rule_version),
    )
    holding_quantities = await portfolio_data.get_holding_quantities()
    holdings_loaded_at = clock.now()
    holdings_lock = asyncio.Lock()
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )

    async def handle(envelope: EventEnvelope) -> None:
        nonlocal holding_quantities, holdings_loaded_at
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        if policy.allocation_for(result.symbol) is None:
            return
        now = clock.now()
        if now - holdings_loaded_at >= timedelta(minutes=5):
            async with holdings_lock:
                if now - holdings_loaded_at >= timedelta(minutes=5):
                    holding_quantities = await portfolio_data.get_holding_quantities()
                    holdings_loaded_at = now
        held_quantity = holding_quantities.get(result.symbol, Decimal())
        alert = engine.ingest(result, now=now, held_quantity=held_quantity)
        state = engine.state_for(result.symbol, updated_at=now)
        if state is None or not await store.save_evaluation(state, alert):
            return
        assert alert is not None
        ledger.emit(alert)
        await bus.publish(
            local_alert_subject(alert.severity, alert.symbol),
            EventEnvelope(
                event_type=LOCAL_ALERT_EVENT,
                occurred_at=alert.created_at,
                source="long-portfolio-engine",
                subject=alert.symbol,
                payload=alert,
            ),
        )

    subscription = await bus.subscribe(
        (
            f"marketbot.v1.analysis.result.LONG_TERM.{requested}"
            if requested is not None
            else "marketbot.v1.analysis.result.LONG_TERM.>"
        ),
        handle,
        options=SubscriptionOptions(
            durable_name=None if once else "marketbot-long-portfolio-v1",
            replay_latest_per_subject=once,
            ack_wait_seconds=60,
        ),
    )
    try:
        details: dict[str, object] = {
            "service": "long-portfolio-v1",
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": assembly.spec(EngineSlot.LONG_PORTFOLIO).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.LONG_PORTFOLIO).strategy.version,
            "rule_version": policy.rule_version,
            "horizon_end": policy.horizon_end,
            "portfolio_capital_usd": str(policy.portfolio_capital_usd),
            "monitored_symbols": len(policy.allocations),
            "universe_source": "postgresql-local:watchlist:PORT_YTD",
            "input_subject": "marketbot.v1.analysis.result.LONG_TERM.>",
            "replay_recent": once,
            "state_restore": "postgresql-local:long_portfolio_states",
            "persistence": "postgresql+ndjson",
        }
        if ready_path is not None:
            _write_ready(ready_path, details)
        if once:
            await bus.wait_until_caught_up(subscription, timeout_seconds=10)
            return details
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()
    return None
