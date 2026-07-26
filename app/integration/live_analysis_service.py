"""Lifecycle orchestration for historical warmup and continuous market streaming."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

_LOGGER = logging.getLogger(__name__)


class MarketDataService(Protocol):
    async def publish_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> int: ...

    async def publish_snapshots(self, symbols: tuple[str, ...]) -> int: ...

    async def stream_once(
        self,
        symbols: tuple[str, ...],
        *,
        trades: bool = True,
        quotes: bool = True,
        bars: bool = True,
        updated_bars: bool = True,
        daily_bars: bool = True,
    ) -> int: ...


class JoinableBus(Protocol):
    async def join(self) -> None: ...


class AnalysisRuntimePort(Protocol):
    async def evaluate_all(self, symbols: tuple[str, ...]) -> None: ...

    def enable_live(self) -> None: ...


class SecRefresher(Protocol):
    async def refresh(self, symbols: tuple[str, ...], as_of: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class InitializationSummary:
    symbols: tuple[str, ...]
    market_events: int


class LiveAnalysisService:
    """Warm histories without alerts, evaluate once, then enable live reactions."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        market_data: MarketDataService,
        local_bus: JoinableBus,
        runtime: AnalysisRuntimePort,
        sec_refresher: SecRefresher | None = None,
    ) -> None:
        normalized = tuple(dict.fromkeys(item.strip().upper() for item in symbols))
        if not normalized or any(not item for item in normalized):
            raise ValueError("at least one market symbol is required")
        self._symbols = normalized
        self._market_data = market_data
        self._local_bus = local_bus
        self._runtime = runtime
        self._sec_refresher = sec_refresher

    async def initialize(self, as_of: datetime) -> InitializationSummary:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("initialization time must be timezone-aware")
        total = 0
        windows = (
            ("1Week", timedelta(days=730)),
            ("1Day", timedelta(days=400)),
            ("15Min", timedelta(days=45)),
            ("1Min", timedelta(days=10)),
        )
        for timeframe, lookback in windows:
            total += await self._market_data.publish_bars(
                self._symbols,
                timeframe=timeframe,
                start=as_of - lookback,
                end=as_of,
                limit=10_000,
            )
            await self._local_bus.join()
        total += await self._market_data.publish_snapshots(self._symbols)
        await self._local_bus.join()
        if self._sec_refresher is not None:
            await self._sec_refresher.refresh(self._symbols, as_of)
        await self._runtime.evaluate_all(self._symbols)
        self._runtime.enable_live()
        return InitializationSummary(self._symbols, total)

    async def stream_forever(
        self,
        *,
        initial_backoff_seconds: float = 1.0,
        maximum_backoff_seconds: float = 30.0,
    ) -> None:
        if initial_backoff_seconds <= 0 or maximum_backoff_seconds < initial_backoff_seconds:
            raise ValueError("invalid reconnect backoff")
        backoff = initial_backoff_seconds
        while True:
            try:
                count = await self._market_data.stream_once(self._symbols)
                if count > 0:
                    backoff = initial_backoff_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "Alpaca market-data stream disconnected; reconnecting in %.1fs",
                    backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(maximum_backoff_seconds, backoff * 2)
