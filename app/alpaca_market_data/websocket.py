"""Authenticated Alpaca Stock Market Data WebSocket session."""

from __future__ import annotations

import asyncio
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
        handshake_timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key_id.strip() or not api_secret_key.strip():
            raise ValueError("Alpaca credentials cannot be blank")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme != "wss" or parsed_url.hostname != "stream.data.alpaca.markets":
            raise ValueError("Alpaca stream must use the Stock Market Data endpoint")
        if handshake_timeout_seconds <= 0:
            raise ValueError("handshake timeout must be positive")
        self._api_key_id = api_key_id
        self._api_secret_key = api_secret_key
        self._url = f"{base_url.rstrip('/')}/{feed}"
        self._connector = connector
        self._handshake_timeout_seconds = handshake_timeout_seconds
        self._subscription_lock = asyncio.Lock()
        self._active_socket: WebSocketConnection | None = None
        self._subscriptions: dict[str, tuple[str, ...]] = {}

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
        connected_event: asyncio.Event | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        normalized_symbols = _normalize_symbols(symbols)
        if not any((trades, quotes, bars, updated_bars, daily_bars)):
            raise ValueError("at least one stream channel must be enabled")

        socket = await self._connector.connect(self._url)
        try:
            connected = await self._receive_handshake_batch(socket, phase="connection")
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
            auth = await self._receive_handshake_batch(socket, phase="authentication")
            if not any(
                message.get("T") == "success" and message.get("msg") == "authenticated"
                for message in auth
            ):
                raise AlpacaMarketDataError("Alpaca WebSocket authentication failed")

            subscriptions = _subscriptions(
                normalized_symbols,
                trades=trades,
                quotes=quotes,
                bars=bars,
                updated_bars=updated_bars,
                daily_bars=daily_bars,
                trade_symbols=trade_symbols,
                quote_symbols=quote_symbols,
            )
            async with self._subscription_lock:
                if self._active_socket is not None:
                    raise AlpacaMarketDataError(
                        "Alpaca market-data stream already has an active session"
                    )
                await _send_subscription_request(socket, "subscribe", subscriptions)
                confirmation = await self._receive_handshake_batch(
                    socket, phase="subscription"
                )
                _raise_provider_error(confirmation)
                if not any(message.get("T") == "subscription" for message in confirmation):
                    raise AlpacaMarketDataError(
                        "Alpaca WebSocket subscription acknowledgement failed"
                    )
                self._active_socket = socket
                self._subscriptions = subscriptions
                if connected_event is not None:
                    connected_event.set()

            for message in confirmation:
                if message.get("T") not in _CONTROL_TYPES:
                    yield message

            while True:
                for message in await _receive_batch(socket):
                    message_type = message.get("T")
                    _raise_provider_error((message,))
                    if message_type not in _CONTROL_TYPES:
                        yield message
        finally:
            async with self._subscription_lock:
                if self._active_socket is socket:
                    self._active_socket = None
                    self._subscriptions = {}
            await socket.close()

    async def _receive_handshake_batch(
        self,
        socket: WebSocketConnection,
        *,
        phase: str,
    ) -> tuple[Mapping[str, object], ...]:
        try:
            return await asyncio.wait_for(
                _receive_batch(socket), timeout=self._handshake_timeout_seconds
            )
        except TimeoutError as error:
            raise AlpacaMarketDataError(
                f"Alpaca WebSocket handshake timed out during {phase}"
            ) from error

    async def update_subscriptions(
        self,
        symbols: tuple[str, ...],
        *,
        trade_symbols: tuple[str, ...] | None = None,
        quote_symbols: tuple[str, ...] | None = None,
    ) -> None:
        """Replace channel targets by sending only deltas on the active connection."""

        normalized_symbols = _normalize_symbols(symbols)
        async with self._subscription_lock:
            socket = self._active_socket
            if socket is None:
                raise AlpacaMarketDataError(
                    "Alpaca market-data stream has no active session to update"
                )
            current = self._subscriptions
            target = _subscriptions(
                normalized_symbols,
                trades="trades" in current,
                quotes="quotes" in current,
                bars="bars" in current,
                updated_bars="updatedBars" in current,
                daily_bars="dailyBars" in current,
                trade_symbols=trade_symbols,
                quote_symbols=quote_symbols,
            )
            removed = _subscription_delta(current, target)
            added = _subscription_delta(target, current)
            if removed:
                await _send_subscription_request(socket, "unsubscribe", removed)
            if added:
                await _send_subscription_request(socket, "subscribe", added)
            self._subscriptions = target


async def _send_json(socket: WebSocketConnection, payload: Mapping[str, object]) -> None:
    await socket.send(json.dumps(payload, separators=(",", ":")))


async def _send_subscription_request(
    socket: WebSocketConnection,
    action: str,
    subscriptions: Mapping[str, tuple[str, ...]],
) -> None:
    request: dict[str, object] = {"action": action}
    request.update({channel: list(symbols) for channel, symbols in subscriptions.items()})
    await _send_json(socket, request)


def _subscriptions(
    symbols: tuple[str, ...],
    *,
    trades: bool,
    quotes: bool,
    bars: bool,
    updated_bars: bool,
    daily_bars: bool,
    trade_symbols: tuple[str, ...] | None,
    quote_symbols: tuple[str, ...] | None,
) -> dict[str, tuple[str, ...]]:
    channels: dict[str, tuple[str, ...]] = {}
    selections = {
        "trades": (trades, trade_symbols),
        "quotes": (quotes, quote_symbols),
        "bars": (bars, None),
        "updatedBars": (updated_bars, None),
        "dailyBars": (daily_bars, None),
    }
    for channel, (enabled, selected) in selections.items():
        if enabled:
            channels[channel] = (
                symbols if selected is None else _normalize_symbols(selected, allow_empty=True)
            )
    return channels


def _subscription_delta(
    desired: Mapping[str, tuple[str, ...]],
    existing: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    return {
        channel: tuple(symbol for symbol in symbols if symbol not in existing.get(channel, ()))
        for channel, symbols in desired.items()
        if any(symbol not in existing.get(channel, ()) for symbol in symbols)
    }


def _normalize_symbols(
    symbols: tuple[str, ...], *, allow_empty: bool = False
) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if any(not symbol for symbol in normalized) or (not normalized and not allow_empty):
        raise ValueError("at least one non-blank symbol is required")
    return normalized


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


def _raise_provider_error(messages: tuple[Mapping[str, object], ...]) -> None:
    error = next((message for message in messages if message.get("T") == "error"), None)
    if error is not None:
        raise AlpacaMarketDataError(
            f"Alpaca WebSocket error: {error.get('msg', 'unknown')}"
        )
