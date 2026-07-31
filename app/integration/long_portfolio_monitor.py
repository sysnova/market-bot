"""Dedicated terminal monitor for persisted and live LONG portfolio alerts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

from app.alert_engine.sinks import ConsoleAlertSink
from app.common.settings import AppSettings, Environment
from app.contracts import (
    LOCAL_ALERT_EVENT,
    AlertKind,
    EventEnvelope,
    LocalAlert,
    SubscriptionOptions,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import _write_ready  # pyright: ignore[reportPrivateUsage]
from .long_portfolio_store import PostgresLongPortfolioAlertStore


async def run_long_portfolio_monitor(
    *, ready_path: Path | None = None, bell: bool = True, history: int = 25
) -> None:
    """Render PostgreSQL history first and then new LONG portfolio NATS alerts."""

    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresLongPortfolioAlertStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError("market_bot.long_portfolio_alerts is not available")
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
    try:
        if ready_path is not None:
            _write_ready(ready_path, {
                "service": "long-portfolio-monitor",
                "history": history,
                "persistence": "postgresql",
            })
        print("LONG PORTFOLIO — historial cargado; esperando nuevas alertas...", flush=True)
        await asyncio.Event().wait()
    finally:
        await subscription.unsubscribe()
        await bus.close()
        await database.dispose()
