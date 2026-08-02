"""Core NATS request/reply transport for market-history synchronization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

from app.contracts import (
    MARKET_HISTORY_ENSURE_SUBJECT,
    MarketHistoryRequest,
    MarketHistoryResponse,
    MarketHistoryStatus,
)


class _Message(Protocol):
    data: bytes

    async def respond(self, data: bytes) -> None: ...


class _Subscription(Protocol):
    async def unsubscribe(self) -> None: ...


class _NatsClient(Protocol):
    is_closed: bool

    async def request(
        self,
        subject: str,
        data: bytes,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> _Message: ...

    async def subscribe(
        self,
        subject: str,
        *,
        cb: Callable[[_Message], Awaitable[None]],
    ) -> _Subscription: ...

    async def drain(self) -> None: ...


class _HistoryService(Protocol):
    async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse: ...


class NatsMarketHistoryClient:
    def __init__(self, client: _NatsClient, *, timeout_seconds: float = 600) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = client
        self._timeout_seconds = timeout_seconds

    @classmethod
    async def connect(
        cls,
        servers: Sequence[str],
        *,
        timeout_seconds: float = 600,
    ) -> NatsMarketHistoryClient:
        import nats

        client = await nats.connect(
            servers=list(servers),
            connect_timeout=2,
            max_reconnect_attempts=3,
            reconnect_time_wait=0.5,
        )
        return cls(cast(_NatsClient, client), timeout_seconds=timeout_seconds)

    async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse:
        message = await self._client.request(
            MARKET_HISTORY_ENSURE_SUBJECT,
            request.model_dump_json().encode(),
            timeout=self._timeout_seconds,
        )
        response = MarketHistoryResponse.model_validate_json(message.data)
        if response.request_id != request.request_id:
            raise RuntimeError("MarketData History returned a mismatched request id")
        if response.status is MarketHistoryStatus.ERROR:
            raise RuntimeError(response.error or "MarketData History synchronization failed")
        return response

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.drain()


class NatsMarketHistoryServer:
    def __init__(
        self,
        client: _NatsClient,
        service: _HistoryService,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._service = service
        self._now = now or (lambda: datetime.now(UTC))
        self._subscription: _Subscription | None = None

    @classmethod
    async def connect(
        cls,
        servers: Sequence[str],
        service: _HistoryService,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> NatsMarketHistoryServer:
        import nats

        client = await nats.connect(
            servers=list(servers),
            connect_timeout=2,
            max_reconnect_attempts=3,
            reconnect_time_wait=0.5,
        )
        return cls(cast(_NatsClient, client), service, now=now)

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._client.subscribe(
            MARKET_HISTORY_ENSURE_SUBJECT,
            cb=self._handle,
        )

    async def close(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        if not self._client.is_closed:
            await self._client.drain()

    async def _handle(self, message: _Message) -> None:
        request: MarketHistoryRequest | None = None
        try:
            request = MarketHistoryRequest.model_validate_json(message.data)
            response = await self._service.ensure(request)
        except Exception as error:
            values: dict[str, object] = {
                "status": MarketHistoryStatus.ERROR,
                "synced_through": self._now(),
                "persisted_bars": 0,
                "error": str(error) or type(error).__name__,
            }
            if request is not None:
                values["request_id"] = request.request_id
            response = MarketHistoryResponse.model_validate(values)
        await message.respond(response.model_dump_json().encode())
