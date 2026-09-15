"""Bounded, live-only fan-out with per-client symbol subscriptions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, cast

_LOG = logging.getLogger(__name__)


class ClientSocket(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass(eq=False)
class _Client:
    socket: ClientSocket
    queue: asyncio.Queue[str]
    symbols: frozenset[str] = field(default_factory=lambda: frozenset[str]())


class OrderFlowGateway:
    def __init__(self, symbols: tuple[str, ...], *, queue_size: int = 256) -> None:
        if not symbols or queue_size < 1:
            raise ValueError("a bounded symbol scope and positive queue size are required")
        self.symbols = frozenset(symbols)
        self.ready = False
        self._queue_size = queue_size
        self._clients: set[_Client] = set()
        self._closers: set[asyncio.Task[None]] = set()

    async def set_ready(self, ready: bool) -> None:
        self.ready = ready
        if not ready:
            await self._disconnect_all(1013, "upstream_disconnected")

    async def close(self) -> None:
        self.ready = False
        await self._disconnect_all(1001, "server_shutdown")

    def publish(self, symbol: str, message: str) -> None:
        if self.ready:
            for client in tuple(self._clients):
                if symbol in client.symbols:
                    self._enqueue(client, message)

    async def handle(self, socket: ClientSocket) -> None:
        if not self.ready:
            await socket.close(code=1013, reason="upstream_disconnected")
            return
        client = _Client(socket, asyncio.Queue(maxsize=self._queue_size))
        self._clients.add(client)
        _LOG.info("order_flow_ws_connected")
        tasks = [asyncio.create_task(self._read(client)), asyncio.create_task(self._write(client))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except Exception:
            # Transport errors may embed request headers; never log exception text.
            _LOG.info("order_flow_ws_connection_ended")
        finally:
            self._clients.discard(client)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            _LOG.info("order_flow_ws_disconnected")

    async def _read(self, client: _Client) -> None:
        async for message in client.socket:
            try:
                symbols = self._parse_subscription(message)
            except ValueError:
                self._enqueue(client, json.dumps({
                    "type": "error",
                    "code": "invalid_subscription",
                    "description": "Send action=subscribe and a nonempty list of enabled symbols.",
                }))
                continue
            client.symbols = frozenset(symbols)
            self._enqueue(client, json.dumps({
                "type": "subscribed", "symbols": symbols, "delivery": "live",
            }))
            _LOG.info("order_flow_ws_subscribed symbols=%s", ",".join(symbols))

    def _parse_subscription(self, message: str | bytes) -> list[str]:
        if not isinstance(message, str):
            raise ValueError("text required")
        request = json.loads(message)
        if not isinstance(request, dict):
            raise ValueError("invalid request")
        request = cast("dict[str, object]", request)
        if set(request) != {"action", "symbols"}:
            raise ValueError("invalid request")
        raw = request["symbols"]
        if request["action"] != "subscribe" or not isinstance(raw, list) or not raw:
            raise ValueError("invalid subscription")
        raw = cast("list[object]", raw)
        if not all(isinstance(symbol, str) for symbol in raw):
            raise ValueError("invalid symbols")
        symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in cast("list[str]", raw)))
        if not set(symbols).issubset(self.symbols):
            raise ValueError("unsupported symbols")
        return symbols

    async def _write(self, client: _Client) -> None:
        while True:
            await client.socket.send(await client.queue.get())

    def _enqueue(self, client: _Client, message: str) -> None:
        if client not in self._clients:
            return
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            self._clients.discard(client)
            self._schedule_close(client, 1013, "slow_consumer")

    def _schedule_close(self, client: _Client, code: int, reason: str) -> None:
        async def close() -> None:
            try:
                await client.socket.close(code=code, reason=reason)
            except Exception:
                _LOG.warning("order_flow_ws_close_failed")
        _LOG.info("order_flow_ws_closing reason=%s", reason)
        task = asyncio.create_task(close())
        self._closers.add(task)
        task.add_done_callback(self._closers.discard)

    async def _disconnect_all(self, code: int, reason: str) -> None:
        for client in tuple(self._clients):
            self._clients.discard(client)
            self._schedule_close(client, code, reason)
        if self._closers:
            await asyncio.gather(*tuple(self._closers))
