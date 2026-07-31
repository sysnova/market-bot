"""Structural ports owned by the Alpaca market-data engine."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Protocol

from app.contracts import EventEnvelope


class EventPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class HttpResponse(Protocol):
    status_code: int

    @property
    def text(self) -> str: ...

    def json(self) -> object: ...


class HttpTransport(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> HttpResponse: ...

    async def close(self) -> None: ...


class MarketDataRest(Protocol):
    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]: ...

    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, Mapping[str, object]]: ...

    async def close(self) -> None: ...


class WebSocketConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


class WebSocketConnector(Protocol):
    async def connect(self, url: str) -> WebSocketConnection: ...


class MarketDataStream(Protocol):
    def messages(
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
    ) -> AsyncIterator[Mapping[str, object]]: ...
