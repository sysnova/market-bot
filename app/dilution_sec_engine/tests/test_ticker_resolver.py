import httpx
import pytest

from app.dilution_sec_engine import (
    SecEdgarConfig,
    SecTickerNotFoundError,
    SecTickerResolver,
)


@pytest.mark.asyncio
async def test_ticker_resolver_loads_official_map_once_and_returns_padded_cik() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        resolver = SecTickerResolver(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        assert await resolver.resolve("aapl") == "0000320193"
        assert await resolver.resolve("MSFT") == "0000789019"

    assert len(requests) == 1
    assert requests[0].url == "https://www.sec.gov/files/company_tickers.json"
    assert requests[0].headers["user-agent"] == "MarketBot/0.1 operator@marketbot.test"


@pytest.mark.asyncio
async def test_ticker_resolver_reports_unknown_symbol() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        resolver = SecTickerResolver(
            SecEdgarConfig(user_agent="MarketBot/0.1 operator@marketbot.test"),
            client=client,
        )
        with pytest.raises(SecTickerNotFoundError, match="UNKNOWN"):
            await resolver.resolve("unknown")
