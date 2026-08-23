"""Lifecycle orchestration for historical warmup and continuous market streaming."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.contracts import UniverseChanged

from .postgres_universe import UniverseSnapshot

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
        max_bars_per_symbol: int | None = None,
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

    async def update_stream_subscriptions(
        self,
        symbols: tuple[str, ...],
        *,
        trade_symbols: tuple[str, ...] | None = None,
        quote_symbols: tuple[str, ...] | None = None,
    ) -> None: ...


class JoinableBus(Protocol):
    async def join(self) -> None: ...


class AnalysisRuntimePort(Protocol):
    async def evaluate_all(self, symbols: tuple[str, ...]) -> None: ...

    async def evaluate_long_term_all(self, symbols: tuple[str, ...]) -> None: ...

    def enable_live(self) -> None: ...

    def disable_live(self) -> None: ...


class UniverseProvider(Protocol):
    async def get_universe(self) -> UniverseSnapshot: ...


class UniverseChangePublisher(Protocol):
    async def publish_universe_changed(self, change: UniverseChanged) -> None: ...


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
        universe_publisher: UniverseChangePublisher | None = None,
        universe_source: str = "configured",
    ) -> None:
        normalized = tuple(dict.fromkeys(item.strip().upper() for item in symbols))
        if not normalized or any(not item for item in normalized):
            raise ValueError("at least one market symbol is required")
        self._symbols = normalized
        self._market_data = market_data
        self._local_bus = local_bus
        self._runtime = runtime
        self._universe_publisher = universe_publisher
        self._universe_source = universe_source

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    async def initialize(self, as_of: datetime) -> InitializationSummary:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("initialization time must be timezone-aware")
        total = await self._warm_market_data(self._symbols, as_of)
        await self._runtime.evaluate_all(self._symbols)
        await self._publish_universe_changed((), self._symbols, as_of)
        self._runtime.enable_live()
        return InitializationSummary(self._symbols, total)

    async def refresh_universe(
        self,
        symbols: tuple[str, ...],
        as_of: datetime,
        *,
        source: str | None = None,
    ) -> bool:
        """Backfill newly added symbols quietly and replace the live subscription set."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("universe refresh time must be timezone-aware")
        normalized = _normalize_symbols(symbols)
        if normalized == self._symbols:
            return False
        previous = self._symbols
        added = tuple(symbol for symbol in normalized if symbol not in previous)
        self._runtime.disable_live()
        try:
            if added:
                await self._warm_market_data(added, as_of)
            self._symbols = normalized
            await self._runtime.evaluate_all(self._symbols)
            await self._publish_universe_changed(
                previous,
                self._symbols,
                as_of,
                source=source,
            )
        finally:
            self._runtime.enable_live()
        return True

    async def refresh_weekly_context(self, as_of: datetime) -> int:
        """Refresh recent completed weeks without reacting to historical publications."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("weekly refresh time must be timezone-aware")
        self._runtime.disable_live()
        try:
            count = await self._market_data.publish_bars(
                self._symbols,
                timeframe="1Week",
                start=as_of - timedelta(days=21),
                end=as_of,
                limit=10_000,
            )
            await self._local_bus.join()
        finally:
            self._runtime.enable_live()
        await self._runtime.evaluate_long_term_all(self._symbols)
        return count

    async def _warm_market_data(self, symbols: tuple[str, ...], as_of: datetime) -> int:
        total = 0
        windows = (
            ("1Week", timedelta(days=365 * 5), 221),
            ("1Day", timedelta(days=400), 260),
            ("15Min", timedelta(days=14), 160),
            ("1Min", timedelta(days=5), 500),
        )
        for timeframe, lookback, max_bars_per_symbol in windows:
            total += await self._market_data.publish_bars(
                symbols,
                timeframe=timeframe,
                start=as_of - lookback,
                end=as_of,
                limit=10_000,
                max_bars_per_symbol=max_bars_per_symbol,
            )
            await self._local_bus.join()
        total += await self._market_data.publish_snapshots(symbols)
        await self._local_bus.join()
        return total

    async def _publish_universe_changed(
        self,
        previous: tuple[str, ...],
        current: tuple[str, ...],
        occurred_at: datetime,
        *,
        source: str | None = None,
    ) -> None:
        if self._universe_publisher is None:
            return
        await self._universe_publisher.publish_universe_changed(
            UniverseChanged(
                occurred_at=occurred_at,
                source=source or self._universe_source,
                previous_symbols=previous,
                symbols=current,
                added_symbols=tuple(value for value in current if value not in previous),
                removed_symbols=tuple(value for value in previous if value not in current),
            )
        )

    async def stream_forever(
        self,
        *,
        initial_backoff_seconds: float = 1.0,
        maximum_backoff_seconds: float = 30.0,
        universe_provider: UniverseProvider | None = None,
        universe_refresh_seconds: float = 120.0,
    ) -> None:
        if initial_backoff_seconds <= 0 or maximum_backoff_seconds < initial_backoff_seconds:
            raise ValueError("invalid reconnect backoff")
        backoff = initial_backoff_seconds
        while True:
            stream_task = asyncio.create_task(self._market_data.stream_once(self._symbols))
            try:
                count = await self._run_stream_session(
                    stream_task,
                    universe_provider=universe_provider,
                    universe_refresh_seconds=universe_refresh_seconds,
                )
                if count > 0:
                    backoff = initial_backoff_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "Alpaca market-data stream disconnected; reconnecting in %.1fs",
                    backoff,
                )
            finally:
                if not stream_task.done():
                    stream_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stream_task
            await asyncio.sleep(backoff)
            backoff = min(maximum_backoff_seconds, backoff * 2)

    async def _run_stream_session(
        self,
        stream_task: asyncio.Task[int],
        *,
        universe_provider: UniverseProvider | None,
        universe_refresh_seconds: float,
    ) -> int:
        if universe_provider is None:
            return await stream_task
        if universe_refresh_seconds <= 0:
            raise ValueError("universe refresh interval must be positive")
        while True:
            done, _pending = await asyncio.wait(
                (stream_task,), timeout=universe_refresh_seconds
            )
            if done:
                return await stream_task
            try:
                universe = await universe_provider.get_universe()
            except Exception:
                _LOGGER.exception(
                    "Local PostgreSQL universe refresh failed; keeping current symbols"
                )
                continue
            if _normalize_symbols(universe.symbols) == self._symbols:
                continue
            await self.refresh_universe(
                universe.symbols,
                datetime.now(UTC),
                source=universe.source,
            )
            await self._market_data.update_stream_subscriptions(self._symbols)
            _LOGGER.info(
                "Market universe updated in-place to %d symbols from %s",
                len(self._symbols),
                universe.source,
            )


def _normalize_symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip().upper() for item in symbols))
    if not normalized or any(not item for item in normalized):
        raise ValueError("at least one market symbol is required")
    return normalized
