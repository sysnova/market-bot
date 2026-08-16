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


@pytest.mark.asyncio
async def test_fetch_news_paginates_and_normalizes_articles() -> None:
    transport = FakeHttpTransport(
        [
            FakeResponse(
                {
                    "news": [
                        {
                            "id": 101,
                            "headline": "Pfizer announces trial results",
                            "summary": "A concise summary.",
                            "author": "Benzinga Newsdesk",
                            "created_at": "2026-08-16T12:30:00Z",
                            "updated_at": "2026-08-16T12:31:00Z",
                            "url": "https://example.com/pfe",
                            "symbols": ["pfe", " spy "],
                            "source": "benzinga",
                        }
                    ],
                    "next_page_token": "page-2",
                }
            ),
            FakeResponse(
                {
                    "news": [
                        {
                            "id": 102,
                            "headline": "Microsoft update",
                            "summary": "",
                            "author": "Newswire",
                            "created_at": "2026-08-16T12:32:00+00:00",
                            "updated_at": "2026-08-16T12:32:00+00:00",
                            "url": "https://example.com/msft",
                            "symbols": ["MSFT"],
                            "source": "benzinga",
                        }
                    ],
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

    articles = await client.fetch_news(
        ("PFE", "MSFT"),
        start=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        limit=50,
    )

    assert [article.article_id for article in articles] == [101, 102]
    assert articles[0].symbols == ("PFE", "SPY")
    assert articles[0].created_at == datetime(2026, 8, 16, 12, 30, tzinfo=UTC)
    assert transport.calls[0][0] == "https://data.alpaca.markets/v1beta1/news"
    assert transport.calls[0][2] == {
        "exclude_contentless": "false",
        "include_content": "false",
        "limit": "50",
        "sort": "desc",
        "start": "2026-08-16T12:00:00Z",
        "symbols": "PFE,MSFT",
    }
    assert transport.calls[1][2]["page_token"] == "page-2"


def test_rest_rejects_a_trading_endpoint() -> None:
    with pytest.raises(ValueError, match="Stock Market Data"):
        AlpacaRestClient(
            api_key_id="key",
            api_secret_key="secret",
            base_url="https://paper-api.alpaca.markets",
            feed="iex",
            transport=FakeHttpTransport([]),
        )
