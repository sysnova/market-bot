"""NATS composition for the versioned LONG portfolio entry monitor."""

from __future__ import annotations

import asyncio
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
from app.long_portfolio_engine import LongPortfolioEngine, load_long_portfolio_policy
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import _write_ready  # pyright: ignore[reportPrivateUsage]
from .long_portfolio_store import PostgresLongPortfolioAlertStore
from .postgres_universe import PostgresUniverseClient


async def run_long_portfolio_process(
    *,
    config_path: Path = Path("configs/rules/long_portfolio/1.0.0.yaml"),
    runtime_root: Path = Path(".runtime"),
    ready_path: Path | None = None,
) -> None:
    """Replay recent Long results, then monitor new ones and publish durable alerts."""

    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    portfolio_data = PostgresUniverseClient(database)
    allocations = await portfolio_data.get_portfolio_allocations()
    policy = load_long_portfolio_policy(config_path, allocations=allocations)
    engine = LongPortfolioEngine(policy)
    ledger = NdjsonAlertSink(runtime_root / "alerts" / "long-portfolio-alerts.ndjson")
    clock = SystemClock()
    store = PostgresLongPortfolioAlertStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError(
            "LONG portfolio alert schema is unavailable; apply "
            "20260731210000_long_portfolio_alerts.sql"
        )
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )

    async def handle(envelope: EventEnvelope) -> None:
        if envelope.event_type != ANALYSIS_RESULT_EVENT:
            return
        result = (
            envelope.payload
            if isinstance(envelope.payload, AnalysisResult)
            else AnalysisResult.model_validate(envelope.payload, strict=False)
        )
        held_quantity = await portfolio_data.get_holding_quantity(result.symbol)
        alert = engine.ingest(result, now=clock.now(), held_quantity=held_quantity)
        if alert is None or not await store.save(alert):
            return
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
        "marketbot.v1.analysis.result.LONG_TERM.>",
        handle,
        options=SubscriptionOptions(replay_all=True, ack_wait_seconds=60),
    )
    try:
        details = {
            "service": "long-portfolio-v1",
            "rule_version": policy.rule_version,
            "horizon_end": policy.horizon_end,
            "portfolio_capital_usd": str(policy.portfolio_capital_usd),
            "monitored_symbols": len(policy.allocations),
            "universe_source": "postgresql-local:watchlist:PORT_YTD",
            "input_subject": "marketbot.v1.analysis.result.LONG_TERM.>",
            "replay_recent": True,
            "persistence": "postgresql+ndjson",
        }
        if ready_path is not None:
            _write_ready(ready_path, details)
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()
