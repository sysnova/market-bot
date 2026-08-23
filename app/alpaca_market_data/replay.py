"""Historical OHLCV replay through the live market-data stream port."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, time
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from .ports import MarketDataRest

_NEW_YORK = ZoneInfo("America/New_York")
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)

Sleep = Callable[[float], Awaitable[None]]


class HistoricalMarketDataStream:
    """Replay one regular session as completed one-minute WebSocket bars.

    The adapter intentionally implements ``MarketDataStream`` so normalization,
    NATS subjects, and all downstream engine behavior remain identical to the
    live WebSocket path. Historical market timestamps are rebased by New York
    wall-clock time onto ``simulated_date``.
    """

    def __init__(
        self,
        *,
        rest: MarketDataRest,
        source_date: date,
        simulated_date: date,
        speed: float | None,
        run_id: str | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if source_date >= simulated_date:
            raise ValueError("source_date must be earlier than simulated_date")
        if speed is not None and (isinstance(speed, bool) or speed <= 0):
            raise ValueError("speed must be positive or None for immediate replay")
        resolved_run_id = run_id.strip() if run_id is not None else uuid4().hex
        if not resolved_run_id:
            raise ValueError("run_id cannot be blank")
        self._rest = rest
        self._source_date = source_date
        self._simulated_date = simulated_date
        self._speed = speed
        self._run_id = resolved_run_id
        self._sleep = sleep

    @property
    def run_id(self) -> str:
        return self._run_id

    async def messages(
        self,
        symbols: tuple[str, ...],
        *,
        trades: bool = True,
        quotes: bool = True,
        bars: bool = True,
        updated_bars: bool = True,
        daily_bars: bool = True,
        trade_symbols: tuple[str, ...] | None = None,
        quote_symbols: tuple[str, ...] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        """Yield historical OHLCV; live-only channel flags are accepted structurally."""

        del trades, quotes, updated_bars, daily_bars, trade_symbols, quote_symbols
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        if not normalized or any(not symbol for symbol in normalized):
            raise ValueError("at least one non-blank symbol is required")
        if not bars:
            raise ValueError("historical replay requires completed minute bars")

        session_open = datetime.combine(
            self._source_date, _REGULAR_OPEN, _NEW_YORK
        ).astimezone(UTC)
        session_close = datetime.combine(
            self._source_date, _REGULAR_CLOSE, _NEW_YORK
        ).astimezone(UTC)
        fetched = await self._rest.fetch_bars(
            normalized,
            timeframe="1Min",
            start=session_open,
            end=session_close,
            limit=10_000,
        )
        records = _ordered_records(
            fetched,
            source_date=self._source_date,
            simulated_date=self._simulated_date,
            run_id=self._run_id,
        )

        previous_timestamp: datetime | None = None
        for timestamp, _, record in records:
            if self._speed is not None and previous_timestamp is not None:
                delay = (timestamp - previous_timestamp).total_seconds() / self._speed
                if delay > 0:
                    await self._sleep(delay)
            yield record
            previous_timestamp = timestamp

    async def update_subscriptions(
        self,
        symbols: tuple[str, ...],
        *,
        trade_symbols: tuple[str, ...] | None = None,
        quote_symbols: tuple[str, ...] | None = None,
    ) -> None:
        del symbols, trade_symbols, quote_symbols
        raise RuntimeError("historical replay subscriptions cannot be updated")


def _ordered_records(
    fetched: Mapping[str, list[Mapping[str, object]]],
    *,
    source_date: date,
    simulated_date: date,
    run_id: str,
) -> tuple[tuple[datetime, str, Mapping[str, object]], ...]:
    records: list[tuple[datetime, str, Mapping[str, object]]] = []
    for raw_symbol, raw_records in fetched.items():
        symbol = raw_symbol.strip().upper()
        if not symbol:
            raise ValueError("historical replay received a blank symbol")
        for raw in raw_records:
            source_timestamp = _timestamp(raw.get("t"))
            local = source_timestamp.astimezone(_NEW_YORK)
            if (
                local.date() != source_date
                or local.time() < _REGULAR_OPEN
                or local.time() >= _REGULAR_CLOSE
            ):
                continue
            rebased_local = datetime.combine(simulated_date, local.time(), _NEW_YORK)
            rebased = rebased_local.astimezone(UTC)
            record = dict(raw)
            record.update(
                {
                    "T": "b",
                    "S": symbol,
                    "t": rebased.isoformat().replace("+00:00", "Z"),
                    "marketbot_replay_run_id": run_id,
                }
            )
            records.append((rebased, symbol, cast("Mapping[str, object]", record)))
    records.sort(key=lambda item: (item[0], item[1]))
    return tuple(records)


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("historical bar timestamp is invalid") from error
    else:
        raise ValueError("historical bar timestamp is missing")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("historical bar timestamp must be timezone-aware")
    return timestamp.astimezone(UTC)
