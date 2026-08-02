from datetime import UTC, datetime, timedelta
from typing import Any

from app.contracts import (
    MARKET_HISTORY_ENSURE_SUBJECT,
    BarTimeframe,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryResponse,
    MarketHistoryStatus,
)
from app.integration.market_history_rpc import (
    NatsMarketHistoryClient,
    NatsMarketHistoryServer,
)

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def request() -> MarketHistoryRequest:
    return MarketHistoryRequest(
        engine_id="intraday-v2",
        symbols=("TGT",),
        requirements=(
            MarketHistoryRequirement(
                timeframe=BarTimeframe.MINUTE_1,
                lookback=timedelta(days=7),
                max_bars_per_symbol=500,
            ),
        ),
        requested_at=NOW,
    )


class FakeMessage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.responses: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self.responses.append(data)


class FakeSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeNats:
    def __init__(self, response: MarketHistoryResponse | None = None) -> None:
        self.response = response
        self.requests: list[tuple[str, bytes, float]] = []
        self.callback: Any = None
        self.subscription = FakeSubscription()
        self.drained = False
        self.is_closed = False

    async def request(
        self,
        subject: str,
        data: bytes,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> FakeMessage:
        self.requests.append((subject, data, timeout))
        assert self.response is not None
        return FakeMessage(self.response.model_dump_json().encode())

    async def subscribe(self, subject: str, *, cb: Any) -> FakeSubscription:
        assert subject == MARKET_HISTORY_ENSURE_SUBJECT
        self.callback = cb
        return self.subscription

    async def drain(self) -> None:
        self.drained = True
        self.is_closed = True


class FakeService:
    def __init__(self, response: MarketHistoryResponse) -> None:
        self.response = response
        self.requests: list[MarketHistoryRequest] = []

    async def ensure(self, item: MarketHistoryRequest) -> MarketHistoryResponse:
        self.requests.append(item)
        return self.response


async def test_rpc_client_uses_core_nats_subject_and_decodes_response() -> None:
    item = request()
    expected = MarketHistoryResponse(
        request_id=item.request_id,
        status=MarketHistoryStatus.READY,
        synced_through=NOW,
        persisted_bars=10,
    )
    nats = FakeNats(expected)
    client = NatsMarketHistoryClient(nats, timeout_seconds=123)  # type: ignore[arg-type]

    response = await client.ensure(item)

    assert response == expected
    assert nats.requests[0][0] == MARKET_HISTORY_ENSURE_SUBJECT
    assert nats.requests[0][2] == 123


async def test_rpc_server_validates_request_and_responds_without_jetstream() -> None:
    item = request()
    expected = MarketHistoryResponse(
        request_id=item.request_id,
        status=MarketHistoryStatus.READY,
        synced_through=NOW,
        persisted_bars=10,
    )
    nats = FakeNats()
    service = FakeService(expected)
    server = NatsMarketHistoryServer(nats, service, now=lambda: NOW)  # type: ignore[arg-type]
    await server.start()
    message = FakeMessage(item.model_dump_json().encode())

    await nats.callback(message)

    assert service.requests == [item]
    assert MarketHistoryResponse.model_validate_json(message.responses[0]) == expected
    await server.close()
    assert nats.subscription.unsubscribed
    assert nats.drained
