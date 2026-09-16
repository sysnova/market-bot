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
    analytical_storage_limit,
    is_completed_daily_bar,
    is_regular_analytical_bar,
    market_session,
    requires_regular_session,
)
from app.common.settings import AppSettings, Environment
from app.contracts import (
    BarTimeframe,
    MarketBar,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryResponse,
    MarketSession,
)
from app.market_history_engine import MarketHistoryService
from app.persistence import create_database_engine

from .market_bar_repository import PostgresMarketBarRepository
from .market_history_rpc import NatsMarketHistoryClient, NatsMarketHistoryServer
from .redis_history import RedisHistoryBars, RedisHistoryWarmer
from .redis_ticker_cache import RedisTickerCache
from .ticker_cache_transport import shared_cache_client

_INTRADAY_DURATION = {
    BarTimeframe.MINUTE_1: timedelta(minutes=1),
    BarTimeframe.MINUTE_5: timedelta(minutes=5),
    BarTimeframe.MINUTE_15: timedelta(minutes=15),
    BarTimeframe.HOUR_1: timedelta(hours=1),
}


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
    bars: tuple[MarketBar, ...] | RedisHistoryBars
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
        include_premarket_intraday: bool = False,
    ) -> tuple[MarketBar, ...]:
        profile = await self.ensure_and_load_profiled(
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
            include_premarket_intraday=include_premarket_intraday,
        )
        return tuple(profile.bars)

    async def ensure_and_load_profiled(
        self,
        *,
        engine_id: str,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
        as_of: datetime,
        force_refresh: bool = False,
        include_premarket_intraday: bool = False,
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
            include_premarket = include_premarket_intraday and requires_regular_session(
                requirement.timeframe
            )
            repository_limit = (
                analytical_storage_limit(
                    requirement.timeframe,
                    requirement.max_bars_per_symbol,
                )
                if include_premarket
                else requirement.max_bars_per_symbol
            )
            repository_started = perf_counter()
            loaded = await self._repository.load_latest(
                request.symbols,
                requirement.timeframe,
                limit_per_symbol=repository_limit,
                regular_session_only=(
                    requires_regular_session(requirement.timeframe) and not include_premarket
                ),
            )
            repository_read_ms = _elapsed_ms(repository_started)
            selection_started = perf_counter()
            output_started_at = len(output)
            eligible = tuple(
                bar
                for bar in loaded
                if (
                    is_regular_analytical_bar(bar)
                    or (
                        include_premarket
                        and market_session(bar.timestamp) is MarketSession.PRE_MARKET
                    )
                )
                and (
                    bar.timeframe is not BarTimeframe.DAY_1
                    or is_completed_daily_bar(bar, as_of=as_of)
                )
                # REST can return the forming interval as an ordinary bar.
                # Bootstrap must not treat it as closed confirmation evidence.
                and (
                    bar.timeframe not in _INTRADAY_DURATION
                    or bar.timestamp + _INTRADAY_DURATION[bar.timeframe] <= as_of
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
    include_premarket_intraday: bool = False,
) -> tuple[MarketBar, ...] | RedisHistoryBars:
    return (
        await load_market_history_profiled(
            settings,
            database,
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
            include_premarket_intraday=include_premarket_intraday,
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
    include_premarket_intraday: bool = False,
) -> MarketHistoryLoadProfile:
    client = await NatsMarketHistoryClient.connect(
        [settings.nats_url.get_secret_value()],
        timeout_seconds=settings.market_history_request_timeout_seconds,
    )
    try:
        cache = shared_cache_client()
        if isinstance(cache, RedisTickerCache):
            started = perf_counter()
            await client.ensure(
                MarketHistoryRequest(
                    engine_id=engine_id,
                    symbols=symbols,
                    requirements=requirements,
                    requested_at=as_of,
                    force_refresh=force_refresh,
                    include_premarket_intraday=include_premarket_intraday,
                )
            )
            elapsed = _elapsed_ms(started)
            return MarketHistoryLoadProfile(
                bars=RedisHistoryBars(
                    cache, symbols, requirements, as_of, include_premarket_intraday
                ),
                ensure_ms=elapsed,
                requirements=(),
                total_ms=elapsed,
            )
        return await MarketHistoryLoader(
            client=client,
            repository=PostgresMarketBarRepository(database),
        ).ensure_and_load_profiled(
            engine_id=engine_id,
            symbols=symbols,
            requirements=requirements,
            as_of=as_of,
            force_refresh=force_refresh,
            include_premarket_intraday=include_premarket_intraday,
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
    cache = shared_cache_client()
    central = (
        RedisHistoryService(service, RedisHistoryWarmer(cache, repository))
        if isinstance(cache, RedisTickerCache)
        else None
    )
    server: NatsMarketHistoryServer | None = None
    try:
        if central is not None:
            await central.prewarm(settings, clock.now())
        server = await NatsMarketHistoryServer.connect(
            [settings.nats_url.get_secret_value()], central or service, now=clock.now
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
                responses = (
                    await central.refresh_registered(as_of=clock.now())
                    if central is not None
                    else await service.refresh_registered(as_of=clock.now())
                )
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


class RedisHistoryService:
    """Ensure Redis coverage before acknowledging any engine's history request."""

    def __init__(self, service: MarketHistoryService, warmer: RedisHistoryWarmer) -> None:
        self.service, self.warmer = service, warmer
        self._lock = asyncio.Lock()
        self._requests: dict[str, MarketHistoryRequest] = {}

    async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse:
        async with self._lock:
            response = await self.service.ensure(request)
            await self.warmer.warm(
                request.symbols,
                request.requirements,
                include_premarket_intraday=request.include_premarket_intraday,
            )
            if not request.force_refresh:
                self._requests[request.engine_id] = request
            return response

    async def refresh_registered(self, *, as_of: datetime) -> tuple[MarketHistoryResponse, ...]:
        async with self._lock:
            responses = await self.service.refresh_registered(as_of=as_of)
            for request in self._requests.values():
                await self.warmer.warm(
                    request.symbols,
                    request.requirements,
                    include_premarket_intraday=request.include_premarket_intraday,
                )
            return responses

    async def prewarm(self, settings: AppSettings, as_of: datetime) -> None:
        from .distributed_composition import resolve_runtime_universe
        from .redis_history_manifest import history_manifest

        universe = await resolve_runtime_universe(settings, None)
        requirements = history_manifest(settings)
        if universe.symbols and requirements:
            await self.ensure(
                MarketHistoryRequest(
                    engine_id="redis-central-warmup",
                    symbols=universe.symbols,
                    requirements=requirements,
                    requested_at=as_of,
                )
            )

            minute = tuple(r for r in requirements if r.timeframe is BarTimeframe.MINUTE_1)
            if minute:
                await self.warmer.warm(
                    universe.symbols,
                    minute,
                    include_premarket_intraday=True,
                )
                self._requests["redis-central-premarket"] = MarketHistoryRequest(
                    engine_id="redis-central-premarket",
                    symbols=universe.symbols,
                    requirements=minute,
                    requested_at=as_of,
                    include_premarket_intraday=True,
                )
