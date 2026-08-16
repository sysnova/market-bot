"""Composition and client loader for centralized MarketData historical bars."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.market_session import (
    is_completed_daily_bar,
    is_regular_analytical_bar,
    requires_regular_session,
)
from app.common.settings import AppSettings, Environment
from app.contracts import (
    BarTimeframe,
    MarketBar,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryResponse,
)
from app.market_history_engine import MarketHistoryService
from app.persistence import create_database_engine

from .market_bar_repository import PostgresMarketBarRepository
from .market_history_rpc import NatsMarketHistoryClient, NatsMarketHistoryServer


class HistoryClient(Protocol):
    async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse: ...


class HistoryRepository(Protocol):
    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
        regular_session_only: bool = False,
    ) -> tuple[MarketBar, ...]: ...


@dataclass(frozen=True)
class HistoryRequirementProfile:
    timeframe: BarTimeframe
    repository_rows: int
    selected_rows: int
    repository_read_ms: float
    selection_ms: float


@dataclass(frozen=True)
class MarketHistoryLoadProfile:
    bars: tuple[MarketBar, ...]
    ensure_ms: float
    requirements: tuple[HistoryRequirementProfile, ...]
    total_ms: float

    @property
    def repository_read_ms(self) -> float:
        return sum(item.repository_read_ms for item in self.requirements)

    @property
    def selection_ms(self) -> float:
        return sum(item.selection_ms for item in self.requirements)


class MarketHistoryLoader:
    def __init__(self, *, client: HistoryClient, repository: HistoryRepository) -> None:
        self._client = client
        self._repository = repository

    async def ensure_and_load(
        self,
        *,
        engine_id: str,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
        as_of: datetime,
        force_refresh: bool = False,
    ) -> tuple[MarketBar, ...]:
        profile = await self.ensure_and_load_profiled(
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
        )
        return profile.bars

    async def ensure_and_load_profiled(
        self,
        *,
        engine_id: str,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
        as_of: datetime,
        force_refresh: bool = False,
    ) -> MarketHistoryLoadProfile:
        total_started = perf_counter()
        request = MarketHistoryRequest(
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            requested_at=as_of,
            force_refresh=force_refresh,
        )
        ensure_started = perf_counter()
        await self._client.ensure(request)
        ensure_ms = _elapsed_ms(ensure_started)
        output: list[MarketBar] = []
        requirement_profiles: list[HistoryRequirementProfile] = []
        for requirement in requirements:
            repository_started = perf_counter()
            loaded = await self._repository.load_latest(
                request.symbols,
                requirement.timeframe,
                limit_per_symbol=requirement.max_bars_per_symbol,
                regular_session_only=requires_regular_session(requirement.timeframe),
            )
            repository_read_ms = _elapsed_ms(repository_started)
            selection_started = perf_counter()
            output_started_at = len(output)
            eligible = tuple(
                bar
                for bar in loaded
                if is_regular_analytical_bar(bar)
                and (
                    bar.timeframe is not BarTimeframe.DAY_1
                    or is_completed_daily_bar(bar, as_of=as_of)
                )
            )
            output.extend(eligible)
            requirement_profiles.append(
                HistoryRequirementProfile(
                    timeframe=requirement.timeframe,
                    repository_rows=len(loaded),
                    selected_rows=len(output) - output_started_at,
                    repository_read_ms=repository_read_ms,
                    selection_ms=_elapsed_ms(selection_started),
                )
            )
        return MarketHistoryLoadProfile(
            bars=tuple(output),
            ensure_ms=ensure_ms,
            requirements=tuple(requirement_profiles),
            total_ms=_elapsed_ms(total_started),
        )


async def load_market_history(
    settings: AppSettings,
    database: AsyncEngine,
    *,
    engine_id: str,
    symbols: tuple[str, ...],
    requirements: tuple[MarketHistoryRequirement, ...],
    as_of: datetime,
    force_refresh: bool = False,
) -> tuple[MarketBar, ...]:
    return (
        await load_market_history_profiled(
            settings,
            database,
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
        )
    ).bars


async def load_market_history_profiled(
    settings: AppSettings,
    database: AsyncEngine,
    *,
    engine_id: str,
    symbols: tuple[str, ...],
    requirements: tuple[MarketHistoryRequirement, ...],
    as_of: datetime,
    force_refresh: bool = False,
) -> MarketHistoryLoadProfile:
    client = await NatsMarketHistoryClient.connect(
        [settings.nats_url.get_secret_value()],
        timeout_seconds=settings.market_history_request_timeout_seconds,
    )
    try:
        return await MarketHistoryLoader(
            client=client,
            repository=PostgresMarketBarRepository(database),
        ).ensure_and_load_profiled(
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
        )
    finally:
        await client.close()


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


async def run_market_history_process(*, ready_path: Path | None = None) -> None:
    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("market-history-v1")
    clock = SystemClock()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    repository = PostgresMarketBarRepository(database)
    if not await repository.is_ready():
        await database.dispose()
        raise RuntimeError(
            "Market bar cache is unavailable; apply 20260802170000_market_bar_cache.sql"
        )
    rest = _build_rest(settings)
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed=settings.alpaca_data_feed,
        batch_size=settings.alpaca_rest_batch_size,
        freshness=timedelta(seconds=settings.market_history_refresh_seconds),
    )
    server: NatsMarketHistoryServer | None = None
    try:
        server = await NatsMarketHistoryServer.connect(
            [settings.nats_url.get_secret_value()], service, now=clock.now
        )
        await server.start()
        if ready_path is not None:
            _write_ready(
                ready_path,
                {
                    "service": "market-history-v1",
                    "transport": "nats-core-request-reply",
                    "persistence": "postgresql-local",
                    "refresh_seconds": settings.market_history_refresh_seconds,
                    "websocket_persistence": False,
                },
            )
        while True:
            await asyncio.sleep(settings.market_history_refresh_seconds)
            try:
                responses = await service.refresh_registered(as_of=clock.now())
                removed = 0
                for timeframe, keep in service.retention_limits().items():
                    removed += await repository.prune(timeframe, keep_per_symbol=keep)
                await logger.ainfo(
                    "market_history_hourly_refreshed",
                    request_groups=len(responses),
                    persisted_bars=sum(item.persisted_bars for item in responses),
                    pruned_bars=removed,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await logger.aexception(
                    "market_history_hourly_refresh_failed",
                    error_type=type(error).__name__,
                )
    finally:
        if server is not None:
            await server.close()
        await rest.close()
        await database.dispose()


def _build_rest(settings: AppSettings) -> AlpacaRestClient:
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    return AlpacaRestClient(
        api_key_id=settings.alpaca_api_key_id.get_secret_value(),
        api_secret_key=settings.alpaca_api_secret_key.get_secret_value(),
        base_url=str(settings.alpaca_data_base_url),
        feed=settings.alpaca_data_feed,
        adjustment=settings.alpaca_adjustment,
        transport=HttpxTransport(),
    )


def _write_ready(path: Path, details: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(details, sort_keys=True), encoding="utf-8")
