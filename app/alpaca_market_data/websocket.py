"""Authenticated Alpaca Stock Market Data WebSocket session."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import cast
from urllib.parse import urlsplit

from .ports import WebSocketConnection, WebSocketConnector
from .rest import AlpacaMarketDataError

_CONTROL_TYPES = {"success", "subscription"}


class AlpacaMarketDataStream:
    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str,
        feed: str,
        connector: WebSocketConnector,
    ) -> None:
        if not api_key_id.strip() or not api_secret_key.strip():
            raise ValueError("Alpaca credentials cannot be blank")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme != "wss" or parsed_url.hostname != "stream.data.alpaca.markets":
            raise ValueError("Alpaca stream must use the Stock Market Data endpoint")
        self._api_key_id = api_key_id
        self._api_secret_key = api_secret_key
        self._url = f"{base_url.rstrip('/')}/{feed}"
        self._connector = connector

    async def messages(
        self,
        symbols: tuple[str, ...],
        *,
        trades: bool = True,
        quotes: bool = True,
        bars: bool = True,
        updated_bars: bool = True,
        daily_bars: bool = True,
    ) -> AsyncIterator[Mapping[str, object]]:
        normalized_symbols = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols)
        )
        if not normalized_symbols or any(not symbol for symbol in normalized_symbols):
            raise ValueError("at least one non-blank symbol is required")
        if not any((trades, quotes, bars, updated_bars, daily_bars)):
            raise ValueError("at least one stream channel must be enabled")

        socket = await self._connector.connect(self._url)
        try:
            connected = await _receive_batch(socket)
            if not any(
                message.get("T") == "success" and message.get("msg") == "connected"
                for message in connected
            ):
                raise AlpacaMarketDataError("Alpaca WebSocket connection handshake failed")
            await _send_json(
                socket,
                {
                    "action": "auth",
                    "key": self._api_key_id,
                    "secret": self._api_secret_key,
                },
            )
            auth = await _receive_batch(socket)
            if not any(
                message.get("T") == "success" and message.get("msg") == "authenticated"
                for message in auth
            ):
                raise AlpacaMarketDataError("Alpaca WebSocket authentication failed")

            request: dict[str, object] = {"action": "subscribe"}
            channels = {
                "trades": trades,
                "quotes": quotes,
                "bars": bars,
                "updatedBars": updated_bars,
                "dailyBars": daily_bars,
            }
            for channel, enabled in channels.items():
                if enabled:
                    request[channel] = list(normalized_symbols)
            await _send_json(socket, request)

            while True:
                for message in await _receive_batch(socket):
                    message_type = message.get("T")
                    if message_type == "error":
                        raise AlpacaMarketDataError(
                            f"Alpaca WebSocket error: {message.get('msg', 'unknown')}"
                        )
                    if message_type not in _CONTROL_TYPES:
                        yield message
        finally:
            await socket.close()


async def _send_json(socket: WebSocketConnection, payload: Mapping[str, object]) -> None:
    await socket.send(json.dumps(payload, separators=(",", ":")))


async def _receive_batch(socket: WebSocketConnection) -> tuple[Mapping[str, object], ...]:
    raw = await socket.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlpacaMarketDataError("Alpaca WebSocket sent invalid JSON") from error
    if not isinstance(decoded, list):
        raise AlpacaMarketDataError("Alpaca WebSocket frame must be a JSON array")
    messages: list[Mapping[str, object]] = []
    for item in cast("list[object]", decoded):
        if not isinstance(item, Mapping):
            raise AlpacaMarketDataError("Alpaca WebSocket message must be an object")
        messages.append(cast("Mapping[str, object]", item))
    return tuple(messages)
