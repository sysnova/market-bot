"""Ordered recovery primitives for the Alpaca WebSocket ingress."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from app.alpaca_market_data.ports import EventPublisher
from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    MarketHistoryRequirement,
    market_bar_subject,
)

_MINIMUM_RECOVERY_LOOKBACK = timedelta(minutes=5)
_MAXIMUM_RECOVERY_BARS_PER_SYMBOL = 10_000


class ReconnectBackoff:
    """Exponential reconnect delay which resets only after a stable session."""

    def __init__(
        self,
        *,
        initial_seconds: float,
        maximum_seconds: float,
        stable_seconds: float,
    ) -> None:
        if initial_seconds <= 0:
            raise ValueError("initial reconnect delay must be positive")
        if maximum_seconds < initial_seconds:
            raise ValueError("maximum reconnect delay cannot be below initial delay")
        if stable_seconds <= 0:
            raise ValueError("stable session duration must be positive")
        self._initial = initial_seconds
        self._maximum = maximum_seconds
        self._stable = stable_seconds
        self._next = initial_seconds

    def failure_delay(self, *, session_uptime_seconds: float) -> float:
        if session_uptime_seconds < 0:
            raise ValueError("session uptime cannot be negative")
        if session_uptime_seconds >= self._stable:
            self._next = self._initial
        delay = self._next
        self._next = min(self._maximum, self._next * 2)
        return delay


class BufferedMarketDataPublisher:
    """Gate bar events during recovery while keeping quotes and trades live."""

    def __init__(
        self,
        target: EventPublisher,
        *,
        max_buffered_bars: int = 100_000,
    ) -> None:
        if isinstance(max_buffered_bars, bool) or max_buffered_bars < 1:
            raise ValueError("max_buffered_bars must be positive")
        self._target = target
        self._maximum = max_buffered_bars
        self._lock = asyncio.Lock()
        self._recovering = False
        self._buffer: list[tuple[str, EventEnvelope]] = []
        self._final_bar_cursors: dict[str, datetime] = {}

    @property
    def final_bar_cursors(self) -> dict[str, datetime]:
        return dict(self._final_bar_cursors)

    def begin_recovery(self) -> None:
        """Start a fresh gate before the next WebSocket connection is opened."""

        self._buffer.clear()
        self._recovering = True

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        async with self._lock:
            if self._recovering and _is_bar_event(envelope):
                if len(self._buffer) >= self._maximum:
                    raise RuntimeError("market stream recovery buffer capacity exceeded")
                self._buffer.append((subject, envelope))
                return
            await self._publish_target(subject, envelope)

    async def finish_recovery(self, recovered: Sequence[MarketBar]) -> int:
        """Publish missing final bars, then release buffered live bars without duplicates."""

        async with self._lock:
            recovered_keys: set[tuple[str, BarTimeframe, datetime]] = set()
            published = 0
            for bar in sorted(recovered, key=lambda item: (item.timestamp, item.symbol)):
                key = _bar_key(bar)
                if key in recovered_keys:
                    continue
                recovered_keys.add(key)
                await self._publish_target(
                    market_bar_subject(bar.timeframe, bar.symbol),
                    EventEnvelope(
                        event_type=MARKET_BAR_EVENT,
                        occurred_at=bar.timestamp,
                        source="alpaca-stream-recovery",
                        subject=bar.symbol,
                        payload=bar,
                    ),
                )
                published += 1
            for subject, envelope in self._buffer:
                payload = envelope.payload
                if isinstance(payload, MarketBar) and _bar_key(payload) in recovered_keys:
                    continue
                await self._publish_target(subject, envelope)
                published += 1
            self._buffer.clear()
            self._recovering = False
            return published

    async def _publish_target(self, subject: str, envelope: EventEnvelope) -> None:
        await self._target.publish(subject, envelope)
        payload = envelope.payload
        if (
            envelope.event_type == MARKET_BAR_EVENT
            and isinstance(payload, MarketBar)
            and payload.timeframe is BarTimeframe.MINUTE_1
            and payload.is_final
        ):
            current = self._final_bar_cursors.get(payload.symbol)
            if current is None or payload.timestamp > current:
                self._final_bar_cursors[payload.symbol] = payload.timestamp


def pending_recovery_bars(
    bars: Sequence[MarketBar],
    *,
    cursors: Mapping[str, datetime],
    recovery_started_at: datetime,
    connected_at: datetime,
) -> tuple[MarketBar, ...]:
    """Select completed one-minute bars not previously published by the live stream."""

    _require_aware(recovery_started_at, "recovery start")
    _require_aware(connected_at, "connection time")
    if connected_at < recovery_started_at:
        raise ValueError("connection time cannot precede recovery start")
    open_minute = connected_at.replace(second=0, microsecond=0)
    pending: list[MarketBar] = []
    for bar in bars:
        cursor = cursors.get(bar.symbol)
        after_lower_bound = (
            bar.timestamp > cursor
            if cursor is not None
            else bar.timestamp >= recovery_started_at
        )
        if (
            bar.timeframe is BarTimeframe.MINUTE_1
            and bar.is_final
            and after_lower_bound
            and bar.timestamp < open_minute
        ):
            pending.append(bar)
    return tuple(sorted(pending, key=lambda item: (item.timestamp, item.symbol)))


def recovery_requirement(
    recovery_started_at: datetime,
    connected_at: datetime,
) -> MarketHistoryRequirement:
    _require_aware(recovery_started_at, "recovery start")
    _require_aware(connected_at, "connection time")
    if connected_at < recovery_started_at:
        raise ValueError("connection time cannot precede recovery start")
    lookback = max(_MINIMUM_RECOVERY_LOOKBACK, connected_at - recovery_started_at)
    minutes = math.ceil(lookback.total_seconds() / 60)
    return MarketHistoryRequirement(
        timeframe=BarTimeframe.MINUTE_1,
        lookback=lookback,
        max_bars_per_symbol=min(_MAXIMUM_RECOVERY_BARS_PER_SYMBOL, minutes + 10),
    )


def _is_bar_event(envelope: EventEnvelope) -> bool:
    return envelope.event_type in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}


def _bar_key(bar: MarketBar) -> tuple[str, BarTimeframe, datetime]:
    return (bar.symbol, bar.timeframe, bar.timestamp)


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
