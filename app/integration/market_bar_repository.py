"""Local PostgreSQL repository for the recoverable normalized-bar cache."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contracts import BarTimeframe, MarketBar
from app.market_history_engine import BarCoverage
from app.persistence.models import MarketBarRecord


class PostgresMarketBarRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def is_ready(self) -> bool:
        async with self._engine.connect() as connection:
            value = await connection.scalar(
                text("select to_regclass('market_bot.market_bars') is not null")
            )
        return bool(value)

    async def coverage(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe
    ) -> dict[str, BarCoverage]:
        normalized = _symbols(symbols)
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        select symbol, count(*)::integer as count, max(timestamp) as latest
                        from market_bot.market_bars
                        where symbol = any(:symbols) and timeframe = :timeframe
                        group by symbol
                        """
                    ),
                    {"symbols": list(normalized), "timeframe": timeframe.value},
                )
            ).all()
        output: dict[str, BarCoverage] = {}
        for raw_row in rows:
            row = cast(Any, raw_row)
            output[str(row.symbol)] = BarCoverage(count=int(row.count), latest=row.latest)
        return {
            symbol: output.get(symbol, BarCoverage(count=0, latest=None)) for symbol in normalized
        }

    async def upsert(self, bars: tuple[MarketBar, ...]) -> int:
        if not bars:
            return 0
        statement = insert(MarketBarRecord)
        statement = statement.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "timestamp"],
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "trade_count": statement.excluded.trade_count,
                "vwap": statement.excluded.vwap,
                "source": statement.excluded.source,
                "feed": statement.excluded.feed,
                "is_final": statement.excluded.is_final,
                "downloaded_at": text("now()"),
            },
        )
        values = [_bar_values(bar) for bar in bars]
        async with self._engine.begin() as connection:
            await connection.execute(statement, values)
        return len(values)

    async def prune(self, timeframe: BarTimeframe, *, keep_per_symbol: int) -> int:
        if keep_per_symbol < 1:
            raise ValueError("keep_per_symbol must be positive")
        async with self._engine.begin() as connection:
            removed = await connection.scalar(
                text("select market_bot.prune_market_bars(:timeframe, :keep_per_symbol)"),
                {
                    "timeframe": timeframe.value,
                    "keep_per_symbol": keep_per_symbol,
                },
            )
        return int(removed or 0)

    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
    ) -> tuple[MarketBar, ...]:
        if limit_per_symbol < 1:
            raise ValueError("limit_per_symbol must be positive")
        normalized = _symbols(symbols)
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        select symbol, timeframe, timestamp, open, high, low, close,
                               volume, trade_count, vwap, source, feed, is_final
                        from (
                          select market_bars.*,
                                 row_number() over (
                                   partition by symbol, timeframe order by timestamp desc
                                 ) as row_number
                          from market_bot.market_bars
                          where symbol = any(:symbols) and timeframe = :timeframe
                        ) latest
                        where row_number <= :limit_per_symbol
                        order by symbol, timestamp
                        """
                    ),
                    {
                        "symbols": list(normalized),
                        "timeframe": timeframe.value,
                        "limit_per_symbol": limit_per_symbol,
                    },
                )
            ).all()
        return tuple(_row_to_bar(row) for row in rows)


def _bar_values(bar: MarketBar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "timeframe": bar.timeframe.value,
        "timestamp": bar.timestamp,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "trade_count": bar.trade_count,
        "vwap": bar.vwap,
        "source": bar.source,
        "feed": bar.feed,
        "is_final": bar.is_final,
    }


def _row_to_bar(row: object) -> MarketBar:
    values = _row_mapping(row)
    return MarketBar.model_validate(dict(values), strict=False)


def _row_mapping(row: object) -> Mapping[str, Any]:
    mapping = getattr(row, "_mapping", None)
    if isinstance(mapping, Mapping):
        return cast("Mapping[str, Any]", mapping)
    return {
        key: getattr(row, key)
        for key in (
            "symbol",
            "timeframe",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
            "source",
            "feed",
            "is_final",
        )
    }


def _symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("at least one non-blank symbol is required")
    return normalized
