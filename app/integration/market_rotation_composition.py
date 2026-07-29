"""Composition root for the periodic independent market-rotation process."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    MARKET_ROTATION_EVENT,
    MARKET_ROTATION_SUBJECT,
    EventEnvelope,
    MarketRotationReport,
    RotationSector,
)
from app.event_bus import NatsJetStreamEventBus
from app.market_rotation_engine import Bar, RotationEngine
from app.persistence import create_database_engine

from .distributed_composition import _build_rest, _write_ready
from .market_rotation_store import PostgresMarketRotationStore


async def run_market_rotation_process(
    *, once: bool = False, interval_minutes: int = 5, ready_path: Path | None = None
) -> dict[str, object] | None:
    settings = AppSettings()
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    rest = _build_rest(settings)
    store = PostgresMarketRotationStore(database)
    calculator = RotationEngine()
    try:
        profiles = await store.load_profiles()
        if ready_path is not None:
            _write_ready(
                ready_path,
                {
                    "service": "market-rotation-v1",
                    "interval_minutes": interval_minutes,
                    "profiles": len(profiles),
                },
            )
        while True:
            now = clock.now()
            symbols = tuple(
                dict.fromkeys(s for p in profiles for s in (*p.symbols, p.proxy, p.benchmark))
            )
            raw: dict[str, list[object]] = {}
            for index in range(0, len(symbols), settings.alpaca_rest_batch_size):
                raw.update(
                    await rest.fetch_bars(
                        symbols[index : index + settings.alpaca_rest_batch_size],
                        timeframe="1Day",
                        start=now - timedelta(days=140),
                        end=now,
                    )
                )
            history = {
                symbol: tuple(
                    Bar(Decimal(str(item["c"])), Decimal(str(item["v"]))) for item in records
                )
                for symbol, records in raw.items()
            }
            results = calculator.analyze(profiles, history)
            run_id, additions = await store.save(results, generated_at=now)
            report = MarketRotationReport(
                run_id=run_id,
                generated_at=now,
                risk_regime="neutral",
                sectors=tuple(
                    RotationSector(
                        code=str(s["code"]),
                        label=str(s["label"]),
                        proxy=str(s["proxy"]),
                        score=s["score"],
                        state=s["state"],
                        top_symbols=tuple(str(e["symbol"]) for e in s["evidence"][:5]),
                    )
                    for s in results
                ),
                watchlist_additions=additions,
            )
            await bus.publish(
                MARKET_ROTATION_SUBJECT,
                EventEnvelope(
                    event_type=MARKET_ROTATION_EVENT,
                    occurred_at=now,
                    source="market-rotation-v1",
                    subject="MARKET",
                    payload=report,
                ),
            )
            summary = {
                "service": "market-rotation-v1",
                "run_id": run_id,
                "sectors": len(results),
                "watchlist_additions": list(additions),
                "subject": MARKET_ROTATION_SUBJECT,
            }
            if once:
                return summary
            await asyncio.sleep(interval_minutes * 60)
    finally:
        await rest.close()
        await bus.close()
        await database.dispose()
