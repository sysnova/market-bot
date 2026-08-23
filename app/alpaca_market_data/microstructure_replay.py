"""Causal historical trade/quote replay for Order Flow walk-forward tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import cast

from .ports import MarketDataRest

Sleep = Callable[[float], Awaitable[None]]


class HistoricalOrderFlowReplay:
    """Merge historical quotes and prints in exchange-time order without lookahead."""

    def __init__(
        self,
        *,
        rest: MarketDataRest,
        start: datetime,
        end: datetime,
        speed: float | None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start.utcoffset() != UTC.utcoffset(start)
            or end.utcoffset() != UTC.utcoffset(end)
        ):
            raise ValueError("microstructure replay boundaries must be UTC aware")
        if end <= start:
            raise ValueError("microstructure replay end must follow start")
        if speed is not None and (isinstance(speed, bool) or speed <= 0):
            raise ValueError("speed must be positive or None")
        self._rest = rest
        self._start = start
        self._end = end
        self._speed = speed
        self._sleep = sleep

    async def events(
        self,
        symbols: tuple[str, ...],
        *,
        include_trades: bool = True,
        include_quotes: bool = True,
    ) -> AsyncIterator[Mapping[str, object]]:
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        if not normalized or any(not symbol for symbol in normalized):
            raise ValueError("at least one non-blank symbol is required")
        if not include_trades and not include_quotes:
            raise ValueError("at least one microstructure channel is required")
        trades, quotes = await asyncio.gather(
            self._fetch_trades(normalized, include=include_trades),
            self._fetch_quotes(normalized, include=include_quotes),
        )
        ordered = _ordered_events(trades=trades, quotes=quotes)
        previous: datetime | None = None
        for timestamp, _, _, event in ordered:
            if self._speed is not None and previous is not None:
                delay = (timestamp - previous).total_seconds() / self._speed
                if delay > 0:
                    await self._sleep(delay)
            yield event
            previous = timestamp

    async def _fetch_trades(
        self, symbols: tuple[str, ...], *, include: bool
    ) -> dict[str, list[Mapping[str, object]]]:
        if not include:
            return {}
        return await self._rest.fetch_trades(
            symbols, start=self._start, end=self._end, limit=10_000
        )

    async def _fetch_quotes(
        self, symbols: tuple[str, ...], *, include: bool
    ) -> dict[str, list[Mapping[str, object]]]:
        if not include:
            return {}
        return await self._rest.fetch_quotes(
            symbols, start=self._start, end=self._end, limit=10_000
        )


def _ordered_events(
    *,
    trades: Mapping[str, list[Mapping[str, object]]],
    quotes: Mapping[str, list[Mapping[str, object]]],
) -> tuple[tuple[datetime, int, str, Mapping[str, object]], ...]:
    events: list[tuple[datetime, int, str, Mapping[str, object]]] = []
    for channel, records_by_symbol, priority in (
        ("q", quotes, 0),
        ("t", trades, 1),
    ):
        for raw_symbol, records in records_by_symbol.items():
            symbol = raw_symbol.strip().upper()
            if not symbol:
                raise ValueError("historical microstructure record has a blank symbol")
            for raw in records:
                timestamp = _timestamp(raw.get("t"))
                event = dict(raw)
                event.update({"T": channel, "S": symbol})
                events.append((timestamp, priority, symbol, cast("Mapping[str, object]", event)))
    events.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(events)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("historical microstructure timestamp is missing")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("historical microstructure timestamp is invalid") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("historical microstructure timestamp must be timezone-aware")
    return timestamp.astimezone(UTC)
