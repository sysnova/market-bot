import json

import pytest

from app.alpaca_market_data.websocket import AlpacaMarketDataStream


class FakeSocket:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = incoming
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.urls: list[str] = []

    async def connect(self, url: str) -> FakeSocket:
        self.urls.append(url)
        return self.socket


@pytest.mark.asyncio
async def test_stream_authenticates_subscribes_and_yields_market_data() -> None:
    socket = FakeSocket(
        [
            '[{"T":"success","msg":"connected"}]',
            '[{"T":"success","msg":"authenticated"}]',
            '[{"T":"subscription","trades":["AAPL"],"quotes":["AAPL"],"bars":["AAPL"]}]',
            '[{"T":"t","S":"AAPL","p":224.1,"s":2,"t":"2026-07-24T14:30:00Z"}]',
        ]
    )
    connector = FakeConnector(socket)
    stream = AlpacaMarketDataStream(
        api_key_id="key",
        api_secret_key="secret",
        base_url="wss://stream.data.alpaca.markets/v2",
        feed="sip",
        connector=connector,
    )

    iterator = stream.messages(("AAPL",), trades=True, quotes=True, bars=True)
    message = await anext(iterator)
    await iterator.aclose()

    assert connector.urls == ["wss://stream.data.alpaca.markets/v2/sip"]
    assert socket.sent == [
        {"action": "auth", "key": "key", "secret": "secret"},
        {
            "action": "subscribe",
            "trades": ["AAPL"],
            "quotes": ["AAPL"],
            "bars": ["AAPL"],
            "updatedBars": ["AAPL"],
            "dailyBars": ["AAPL"],
        },
    ]
    assert message["T"] == "t"
    assert socket.closed is True


@pytest.mark.asyncio
async def test_stream_rejects_failed_authentication() -> None:
    socket = FakeSocket(
        [
            '[{"T":"success","msg":"connected"}]',
            '[{"T":"error","code":401,"msg":"not authenticated"}]',
        ]
    )
    stream = AlpacaMarketDataStream(
        api_key_id="bad",
        api_secret_key="bad",
        base_url="wss://stream.data.alpaca.markets/v2",
        feed="iex",
        connector=FakeConnector(socket),
    )

    with pytest.raises(RuntimeError, match="authentication failed"):
        await anext(stream.messages(("AAPL",)))
    assert socket.closed is True
