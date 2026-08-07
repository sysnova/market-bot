"""Composition root for the periodic independent market-rotation process."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    MARKET_ROTATION_EVENT,
    MARKET_ROTATION_SUBJECT,
    BarTimeframe,
    EventEnvelope,
    MarketRotationReport,
    RotationSector,
)
from app.event_bus import NatsJetStreamEventBus
from app.market_rotation_engine import Bar
from app.persistence import create_database_engine

from .distributed_composition import HistoryRequest, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_repository import PostgresMarketBarRepository
from .market_history_composition import load_market_history
from .market_rotation_store import PostgresMarketRotationStore

ROTATION_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.DAY_1,
        lookback=timedelta(days=140),
        max_bars_per_symbol=100,
    ),
)


async def run_market_rotation_process(
    *, once: bool = False, interval_minutes: int = 5, ready_path: Path | None = None
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    bus = await NatsJetStreamEventBus.connect(
        servers=[settings.nats_url.get_secret_value()], prefix="marketbot", stream="MARKETBOT"
    )
    store = PostgresMarketRotationStore(database)
    bars_repository = PostgresMarketBarRepository(database)
    calculator = assembly.build_market_rotation()
    try:
        profiles = await store.load_profiles()
        symbols = tuple(
            dict.fromkeys(s for p in profiles for s in (*p.symbols, p.proxy, p.benchmark))
        )
        await load_market_history(
            settings,
            database,
            engine_id="market-rotation-v1",
            symbols=symbols,
            requirements=ROTATION_HISTORY_REQUESTS,
            as_of=clock.now(),
        )
        if ready_path is not None:
            write_ready(
                ready_path,
                {
                    "service": "market-rotation-v1",
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": assembly.spec(
                        EngineSlot.MARKET_ROTATION
                    ).implementation,
                    "engine_strategy_version": assembly.spec(
                        EngineSlot.MARKET_ROTATION
                    ).strategy.version,
                    "interval_minutes": interval_minutes,
                    "profiles": len(profiles),
                },
            )
        while True:
            now = clock.now()
            bars = await bars_repository.load_latest(
                symbols,
                BarTimeframe.DAY_1,
                limit_per_symbol=ROTATION_HISTORY_REQUESTS[0].max_bars_per_symbol,
            )
            history = {
                symbol: tuple(
                    Bar(item.close, item.volume) for item in bars if item.symbol == symbol
                )
                for symbol in symbols
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
            summary: dict[str, object] = {
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
        await bus.close()
        await database.dispose()
