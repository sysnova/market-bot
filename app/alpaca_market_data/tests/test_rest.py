from datetime import UTC, datetime
from typing import Any

import pytest

from app.alpaca_market_data.rest import AlpacaRestClient


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = "fake response"

    def json(self) -> object:
        return self._payload


class FakeHttpTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> FakeResponse:
        self.calls.append((url, headers, params))
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_bars_paginates_and_authenticates() -> None:
    transport = FakeHttpTransport(
        [
            FakeResponse(
                {
                    "bars": {"AAPL": [{"t": "2026-07-24T14:30:00Z", "c": 1}]},
                    "next_page_token": "next",
                }
            ),
            FakeResponse(
                {
                    "bars": {"AAPL": [{"t": "2026-07-24T14:31:00Z", "c": 2}]},
                    "next_page_token": None,
                }
            ),
        ]
    )
    client = AlpacaRestClient(
        api_key_id="key",
        api_secret_key="secret",
        base_url="https://data.alpaca.markets",
        feed="sip",
        transport=transport,
    )

    result = await client.fetch_bars(
        ("AAPL",),
        timeframe="1Min",
        start=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 24, 14, 32, tzinfo=UTC),
    )

    assert len(result["AAPL"]) == 2
    assert transport.calls[0][0] == "https://data.alpaca.markets/v2/stocks/bars"
    assert transport.calls[0][1] == {
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": "secret",
    }
    assert transport.calls[0][2]["feed"] == "sip"
    assert transport.calls[0][2]["adjustment"] == "split"
    assert transport.calls[1][2]["page_token"] == "next"


@pytest.mark.asyncio
async def test_fetch_snapshots_uses_only_market_data_endpoint() -> None:
    payload: dict[str, Any] = {"AAPL": {"latestTrade": {"p": 1}}}
    transport = FakeHttpTransport([FakeResponse(payload)])
    client = AlpacaRestClient(
        api_key_id="key",
        api_secret_key="secret",
        base_url="https://data.alpaca.markets",
        feed="iex",
        transport=transport,
    )

    snapshots = await client.fetch_snapshots(("AAPL", "MSFT"))

    assert snapshots == payload
    url, _, params = transport.calls[0]
    assert url.endswith("/v2/stocks/snapshots")
    assert params == {"symbols": "AAPL,MSFT", "feed": "iex"}
    assert "order" not in url.lower()


def test_rest_rejects_a_trading_endpoint() -> None:
    with pytest.raises(ValueError, match="Stock Market Data"):
        AlpacaRestClient(
            api_key_id="key",
            api_secret_key="secret",
            base_url="https://paper-api.alpaca.markets",
            feed="iex",
            transport=FakeHttpTransport([]),
        )
