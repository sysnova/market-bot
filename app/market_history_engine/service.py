"""Central incremental synchronization service for normalized market bars."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.alpaca_market_data import AlpacaEventNormalizer
from app.alpaca_market_data.ports import MarketDataRest
from app.common.market_session import analytical_storage_limit
from app.contracts import (
    BarTimeframe,
    MarketBar,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryResponse,
    MarketHistoryStatus,
)

_NEW_YORK = ZoneInfo("America/New_York")
_MINIMUM_RETENTION = {
    BarTimeframe.MINUTE_1: 750,
    BarTimeframe.MINUTE_15: 250,
    BarTimeframe.HOUR_1: 650,
    BarTimeframe.DAY_1: 650,
    BarTimeframe.WEEK_1: 500,
}


@dataclass(frozen=True, slots=True)
class BarCoverage:
    count: int
    latest: datetime | None
    downloaded_at: datetime | None = None


class MarketBarRepository(Protocol):
    async def coverage(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe
    ) -> dict[str, BarCoverage]: ...

    async def upsert(self, bars: tuple[MarketBar, ...]) -> int: ...


class MarketHistoryService:
    """Own every Alpaca historical-bars request and populate the shared cache."""

    def __init__(
        self,
        *,
        rest: MarketDataRest,
        repository: MarketBarRepository,
        feed: str,
        batch_size: int,
        freshness: timedelta = timedelta(hours=1),
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if freshness <= timedelta(0):
            raise ValueError("freshness must be positive")
        self._rest = rest
        self._repository = repository
        self._normalizer = AlpacaEventNormalizer(feed=feed)
        self._batch_size = batch_size
        self._freshness = freshness
        self._registered: dict[str, MarketHistoryRequest] = {}
        self._sync_lock = asyncio.Lock()

    async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse:
        # Forced tail repairs are one-shot. Registering their dynamic lookback would
        # permanently widen hourly refresh and PostgreSQL retention after a long outage.
        if not request.force_refresh:
            previous = self._registered.get(request.engine_id)
            self._registered[request.engine_id] = (
                request if previous is None else _merge_engine_request(previous, request)
            )
        async with self._sync_lock:
            persisted = await self._sync(request)
        return MarketHistoryResponse(
            request_id=request.request_id,
            status=MarketHistoryStatus.READY,
            synced_through=request.requested_at,
            persisted_bars=persisted,
        )

    async def refresh_registered(self, *, as_of: datetime) -> tuple[MarketHistoryResponse, ...]:
        if not self._registered:
            return ()
        requests = _refresh_requests(tuple(self._registered.values()), as_of=as_of)
        responses: list[MarketHistoryResponse] = []
        async with self._sync_lock:
            for request in requests:
                persisted = await self._sync(request)
                responses.append(
                    MarketHistoryResponse(
                        request_id=request.request_id,
                        status=MarketHistoryStatus.READY,
                        synced_through=as_of,
                        persisted_bars=persisted,
                    )
                )
        return tuple(responses)

    def retention_limits(self) -> dict[BarTimeframe, int]:
        required: dict[BarTimeframe, int] = {}
        for request in self._registered.values():
            for item in request.requirements:
                required[item.timeframe] = max(
                    required.get(item.timeframe, 0),
                    analytical_storage_limit(item.timeframe, item.max_bars_per_symbol),
                )
        return {
            timeframe: max(
                _MINIMUM_RETENTION[timeframe],
                count + max(50, count // 4),
            )
            for timeframe, count in required.items()
        }

    async def _sync(self, request: MarketHistoryRequest) -> int:
        persisted = 0
        for requirement in request.requirements:
            coverage = await self._repository.coverage(request.symbols, requirement.timeframe)
            for batch, start in _pending_sync_batches(
                request.symbols,
                coverage,
                requirement,
                as_of=request.requested_at,
                freshness=self._freshness,
                batch_size=self._batch_size,
                force_refresh=request.force_refresh,
            ):
                raw = await self._rest.fetch_bars(
                    batch,
                    timeframe=requirement.timeframe.value,
                    start=start,
                    end=request.requested_at,
                    limit=10_000,
                )
                bars = self._normalize(
                    raw,
                    batch=batch,
                    requirement=requirement,
                    as_of=request.requested_at,
                )
                persisted += await self._repository.upsert(bars)
        return persisted

    def _normalize(
        self,
        raw: Mapping[str, list[Mapping[str, object]]],
        *,
        batch: tuple[str, ...],
        requirement: MarketHistoryRequirement,
        as_of: datetime,
    ) -> tuple[MarketBar, ...]:
        output: list[MarketBar] = []
        for symbol in batch:
            storage_limit = analytical_storage_limit(
                requirement.timeframe, requirement.max_bars_per_symbol
            )
            records = raw.get(symbol, [])[-storage_limit:]
            for record in records:
                payload = self._normalizer.rest_bar(
                    symbol, requirement.timeframe.value, record
                ).envelope.payload
                if not isinstance(payload, MarketBar):
                    raise TypeError("normalized REST bar did not produce MarketBar")
                if payload.timeframe is BarTimeframe.WEEK_1 and not _weekly_bar_is_complete(
                    payload.timestamp, as_of
                ):
                    continue
                output.append(payload)
        return tuple(output)


def _symbol_sync_start(
    coverage: BarCoverage,
    requirement: MarketHistoryRequirement,
    *,
    as_of: datetime,
) -> datetime:
    if coverage.count <= 0 or coverage.latest is None:
        return as_of - requirement.lookback
    return coverage.latest - _overlap(requirement.timeframe)


def _pending_sync_batches(
    symbols: tuple[str, ...],
    coverage: Mapping[str, BarCoverage],
    requirement: MarketHistoryRequirement,
    *,
    as_of: datetime,
    freshness: timedelta,
    batch_size: int,
    force_refresh: bool = False,
) -> tuple[tuple[tuple[str, ...], datetime], ...]:
    threshold = as_of - freshness
    grouped: dict[datetime, list[str]] = {}
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    for symbol in normalized:
        item = coverage.get(symbol, BarCoverage(count=0, latest=None))
        if (
            not force_refresh
            and item.count > 0
            and item.latest is not None
            and item.downloaded_at is not None
            and item.downloaded_at >= threshold
        ):
            continue
        start = _symbol_sync_start(item, requirement, as_of=as_of)
        grouped.setdefault(start, []).append(symbol)
    return tuple(
        (batch, start)
        for start, pending_symbols in grouped.items()
        for batch in _batches(tuple(pending_symbols), batch_size)
    )


def _overlap(timeframe: BarTimeframe) -> timedelta:
    return {
        BarTimeframe.MINUTE_1: timedelta(minutes=2),
        BarTimeframe.MINUTE_15: timedelta(minutes=30),
        BarTimeframe.HOUR_1: timedelta(hours=2),
        BarTimeframe.DAY_1: timedelta(days=2),
        BarTimeframe.WEEK_1: timedelta(days=14),
    }[timeframe]


def _batches(symbols: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    return tuple(normalized[index : index + size] for index in range(0, len(normalized), size))


def _refresh_requests(
    requests: tuple[MarketHistoryRequest, ...], *, as_of: datetime
) -> tuple[MarketHistoryRequest, ...]:
    per_symbol: dict[tuple[str, BarTimeframe], MarketHistoryRequirement] = {}
    for item in requests:
        for symbol in item.symbols:
            for requirement in item.requirements:
                key = (symbol, requirement.timeframe)
                current = per_symbol.get(key)
                if current is None:
                    per_symbol[key] = requirement
                    continue
                per_symbol[key] = MarketHistoryRequirement(
                    timeframe=requirement.timeframe,
                    lookback=max(current.lookback, requirement.lookback),
                    max_bars_per_symbol=max(
                        current.max_bars_per_symbol,
                        requirement.max_bars_per_symbol,
                    ),
                )
    grouped: dict[tuple[BarTimeframe, timedelta, int], list[str]] = {}
    for (symbol, timeframe), requirement in per_symbol.items():
        key = (
            timeframe,
            requirement.lookback,
            requirement.max_bars_per_symbol,
        )
        grouped.setdefault(key, []).append(symbol)
    return tuple(
        MarketHistoryRequest(
            engine_id=f"market-history-hourly-{index}",
            symbols=tuple(symbols),
            requirements=(
                MarketHistoryRequirement(
                    timeframe=timeframe,
                    lookback=lookback,
                    max_bars_per_symbol=max_bars,
                ),
            ),
            requested_at=as_of,
        )
        for index, ((timeframe, lookback, max_bars), symbols) in enumerate(grouped.items(), start=1)
    )


def _merge_engine_request(
    previous: MarketHistoryRequest, current: MarketHistoryRequest
) -> MarketHistoryRequest:
    requirements: dict[BarTimeframe, MarketHistoryRequirement] = {
        item.timeframe: item for item in previous.requirements
    }
    for item in current.requirements:
        existing = requirements.get(item.timeframe)
        requirements[item.timeframe] = (
            item
            if existing is None
            else MarketHistoryRequirement(
                timeframe=item.timeframe,
                lookback=max(existing.lookback, item.lookback),
                max_bars_per_symbol=max(existing.max_bars_per_symbol, item.max_bars_per_symbol),
            )
        )
    return MarketHistoryRequest(
        engine_id=current.engine_id,
        symbols=tuple(dict.fromkeys((*previous.symbols, *current.symbols))),
        requirements=tuple(requirements.values()),
        requested_at=current.requested_at,
    )


def _weekly_bar_is_complete(timestamp: datetime, as_of: datetime) -> bool:
    local_date = timestamp.astimezone(_NEW_YORK).date()
    week_start = local_date - timedelta(days=local_date.weekday())
    completion = datetime.combine(week_start + timedelta(days=5), time(), _NEW_YORK)
    return as_of.astimezone(_NEW_YORK) >= completion
