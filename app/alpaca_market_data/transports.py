"""Production HTTP and WebSocket transports for Alpaca market data."""

from __future__ import annotations

import httpx
from websockets.asyncio.client import connect

from .ports import HttpResponse, WebSocketConnection


class HttpxTransport:
    """Small adapter that keeps HTTP details out of the application service."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> HttpResponse:
        return await self._client.get(url, headers=headers, params=params)

    async def close(self) -> None:
        await self._client.aclose()


class WebsocketsConnector:
    """Open one resilient client connection using protocol-level heartbeats."""

    def __init__(
        self,
        *,
        open_timeout_seconds: float = 20.0,
        ping_interval_seconds: float = 20.0,
        ping_timeout_seconds: float = 20.0,
        max_queue: int = 1024,
    ) -> None:
        if min(open_timeout_seconds, ping_interval_seconds, ping_timeout_seconds) <= 0:
            raise ValueError("WebSocket timeouts must be positive")
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        self._open_timeout_seconds = open_timeout_seconds
        self._ping_interval_seconds = ping_interval_seconds
        self._ping_timeout_seconds = ping_timeout_seconds
        self._max_queue = max_queue

    async def connect(self, url: str) -> WebSocketConnection:
        return await connect(
            url,
            open_timeout=self._open_timeout_seconds,
            ping_interval=self._ping_interval_seconds,
            ping_timeout=self._ping_timeout_seconds,
            max_queue=self._max_queue,
        )
