"""Central PostgreSQL hydration and bounded, lazy Redis history readers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from typing import Protocol, cast

from app.common.market_session import (
    analytical_storage_limit,
    is_completed_daily_bar,
    is_regular_analytical_bar,
    market_session,
    requires_regular_session,
)
from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    MarketHistoryRequirement,
    MarketSession,
)
from app.market_history_engine import BarCoverage

from .event_fanout import EventPublisher
from .redis_ticker_cache import RedisTickerCache


class Repository(Protocol):
    async def coverage(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe
    ) -> dict[str, BarCoverage]: ...

    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
        regular_session_only: bool = False,
    ) -> tuple[MarketBar, ...]: ...


def history_view(symbol: str, timeframe: BarTimeframe, regular: bool) -> str:
    return f"history:{symbol}:{timeframe.value}:{'regular' if regular else 'all'}"


def bar_row(bar: MarketBar) -> list[object]:
    return [
        bar.symbol,
        bar.timeframe.value,
        bar.timestamp.timestamp(),
        bar.is_final,
        bar.model_dump_json(),
    ]


class RedisHistoryWarmer:
    """Only the central history service reads full PostgreSQL windows, one ticker at a time."""

    def __init__(self, cache: RedisTickerCache, repository: Repository) -> None:
        self.cache = cache
        self.repository = repository

    async def warm(
        self,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
        *,
        include_premarket_intraday: bool = False,
    ) -> None:
        for requirement in requirements:
            tf = requirement.timeframe
            coverage = await self.repository.coverage(symbols, tf)
            for symbol in symbols:
                item = coverage[symbol]
                fingerprint = f"{item.count}:{item.latest}:{item.downloaded_at}"
                regular_only = requires_regular_session(tf) and not include_premarket_intraday
                variants = (regular_only,)
                for regular in variants:
                    limit = (
                        requirement.max_bars_per_symbol
                        if regular
                        else analytical_storage_limit(tf, requirement.max_bars_per_symbol)
                    )
                    view = history_view(symbol, tf, regular)
                    meta = self.cache.namespace + view + ":coverage"
                    previous = cast(dict[str, str], self.cache.redis.hgetall(meta))
                    if (
                        previous.get("fingerprint") == fingerprint
                        and int(previous.get("limit", 0)) >= limit
                        and self.cache.redis.exists(self.cache.namespace + "view:" + view)
                    ):
                        continue
                    limit = max(limit, int(previous.get("limit", 0)))
                    bars = await self.repository.load_latest(
                        (symbol,),
                        tf,
                        limit_per_symbol=limit,
                        regular_session_only=regular,
                    )
                    self.cache.open_persistent(view, limit)
                    for offset in range(0, len(bars), 256):
                        self.cache.call(
                            "add", view, [bar_row(b) for b in bars[offset : offset + 256]]
                        )
                    # Publish coverage only after all rows are installed successfully.
                    self.cache.redis.hset(
                        meta, mapping={"fingerprint": fingerprint, "limit": limit}
                    )

    def add_live(self, bar: MarketBar) -> None:
        for regular in (False, True):
            if regular and not is_regular_analytical_bar(bar):
                continue
            view = history_view(bar.symbol, bar.timeframe, regular)
            if self.cache.redis.exists(self.cache.namespace + "view:" + view):
                self.cache.call("add", view, [bar_row(bar)])


class RedisHistoryBars:
    """A reusable iterable, never a tuple containing the entire watchlist history."""

    def __init__(
        self,
        cache: RedisTickerCache,
        symbols: tuple[str, ...],
        requirements: tuple[MarketHistoryRequirement, ...],
        as_of: datetime,
        include_premarket: bool,
    ) -> None:
        self.cache, self.symbols, self.requirements = cache, symbols, requirements
        self.as_of, self.include_premarket = as_of, include_premarket
        self._count: int | None = None

    def _window(self, symbol: str, requirement: MarketHistoryRequirement) -> Iterator[MarketBar]:
        tf = requirement.timeframe
        regular = requires_regular_session(tf) and not self.include_premarket
        limit = (
            analytical_storage_limit(tf, requirement.max_bars_per_symbol)
            if self.include_premarket and requires_regular_session(tf)
            else requirement.max_bars_per_symbol
        )
        values = self.cache.call(
            "history", history_view(symbol, tf, regular), symbol, tf.value, limit, False
        )
        duration = {
            BarTimeframe.MINUTE_1: 1,
            BarTimeframe.MINUTE_5: 5,
            BarTimeframe.MINUTE_15: 15,
            BarTimeframe.HOUR_1: 60,
        }.get(tf)
        for value in values:
            bar = MarketBar.model_validate_json(value)
            if not (
                is_regular_analytical_bar(bar)
                or (
                    self.include_premarket
                    and market_session(bar.timestamp) is MarketSession.PRE_MARKET
                )
            ):
                continue
            if tf is BarTimeframe.DAY_1 and not is_completed_daily_bar(bar, as_of=self.as_of):
                continue
            if duration is not None and bar.timestamp + timedelta(minutes=duration) > self.as_of:
                continue
            if bar.timestamp > self.as_of:
                continue
            yield bar

    def __iter__(self) -> Iterator[MarketBar]:
        for requirement in self.requirements:
            for symbol in self.symbols:
                yield from self._window(symbol, requirement)

    def __len__(self) -> int:
        if self._count is None:
            self._count = sum(1 for _ in self)
        return self._count

    def chronological(self) -> Iterator[MarketBar]:
        # Aggregators are keyed by ticker. Only within-ticker ordering is relevant;
        # sorting the whole universe would recreate the original memory spike.
        for symbol in self.symbols:
            yield from sorted(
                (bar for req in self.requirements for bar in self._window(symbol, req)),
                key=lambda bar: bar.timestamp,
            )


def chronological_bars(bars: Iterable[MarketBar]) -> Iterable[MarketBar]:
    if isinstance(bars, RedisHistoryBars):
        return bars.chronological()
    return sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol))


class RedisIngressPublisher:
    """One live writer updates canonical Redis windows before notifying engines."""

    def __init__(self, target: EventPublisher, warmer: RedisHistoryWarmer) -> None:
        self.target, self.warmer = target, warmer

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        if envelope.event_type in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            bar = envelope.payload
            if not isinstance(bar, MarketBar):
                bar = MarketBar.model_validate(bar, strict=False)
            self.warmer.add_live(bar)
        await self.target.publish(subject, envelope)
