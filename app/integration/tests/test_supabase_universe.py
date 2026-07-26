import json

import httpx
import pytest

from app.integration.supabase_universe import (
    SupabaseUniverseClient,
    SupabaseUniverseConfig,
    SupabaseUniverseError,
)


@pytest.mark.unit
async def test_universe_merges_watchlist_holdings_and_fallback_symbols() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/watchlist"):
            return httpx.Response(
                200,
                json={"symbols": ["asts", "IREN", "ASTS"], "updatedAt": "watchlist-time"},
            )
        return httpx.Response(
            200,
            json={
                "positions": [
                    {"symbol": "nbis", "shares": 2, "tradePrice": 31.5},
                    {"symbol": "ZERO", "shares": 0, "tradePrice": 1},
                    {"symbol": "BAD", "shares": "not-a-number"},
                ],
                "updatedAt": "holdings-time",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseUniverseClient(
            SupabaseUniverseConfig(
                base_url="https://example.supabase.co",
                desktop_api_key="desktop-key",
                fallback_symbols=("SPY", "asts"),
            ),
            client=http,
        )

        universe = await client.get_universe()

    assert universe.symbols == ("ASTS", "IREN", "NBIS", "SPY")
    assert universe.source == "supabase"
    assert universe.watchlist_updated_at == "watchlist-time"
    assert universe.holdings_updated_at == "holdings-time"
    assert {request.url.path for request in requests} == {
        "/functions/v1/stock-messages-api/watchlist",
        "/functions/v1/stock-messages-api/holdings",
    }
    assert all(request.headers["X-Stock-Desktop-Key"] == "desktop-key" for request in requests)


@pytest.mark.unit
async def test_universe_rejects_failed_edge_function_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=json.dumps({"error": request.url.path}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseUniverseClient(
            SupabaseUniverseConfig(
                base_url="https://example.supabase.co",
                desktop_api_key="desktop-key",
                fallback_symbols=("SPY",),
            ),
            client=http,
        )

        with pytest.raises(SupabaseUniverseError, match="503"):
            await client.get_universe()
