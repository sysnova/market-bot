from collections.abc import Mapping
from datetime import date

import pytest

from app.integration.options_gamma_alpaca import (
    AlpacaOptionContractsClient,
    AlpacaOptionsDataClient,
)


class Response:
    status_code = 200
    text = "ok"

    def __init__(self, payload: Mapping[str, object]) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


class Transport:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.closed = False

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> Response:
        assert headers == {
            "APCA-API-KEY-ID": "key",
            "APCA-API-SECRET-KEY": "secret",
        }
        self.calls.append((url, params))
        return Response(self.payload)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.unit
async def test_option_chain_adapter_parses_occ_contract_and_snapshot() -> None:
    transport = Transport(
        {
            "snapshots": {
                "AAPL260814C00100000": {
                    "latestTrade": {"p": 3.05, "t": "2026-08-12T15:00:00Z"},
                    "latestQuote": {
                        "bp": 3.0,
                        "ap": 3.1,
                        "t": "2026-08-12T15:00:01Z",
                    },
                    "greeks": {"gamma": 0.08},
                    "impliedVolatility": 0.4,
                }
            },
            "next_page_token": None,
        }
    )
    client = AlpacaOptionsDataClient(
        api_key_id="key",
        api_secret_key="secret",
        base_url="https://data.alpaca.markets",
        feed=None,
        transport=transport,
    )

    items = await client.fetch_chain(
        "AAPL",
        expiration_from=date(2026, 8, 12),
        expiration_to=date(2026, 9, 25),
        strike_from="80",
        strike_to="120",
    )

    assert len(items) == 1
    assert items[0].underlying_symbol == "AAPL"
    assert items[0].expiration_date == date(2026, 8, 14)
    assert str(items[0].strike_price) == "100"
    assert items[0].option_type == "call"
    assert items[0].open_interest is None
    assert items[0].open_interest_date is None
    assert transport.calls[0][0].endswith("/v1beta1/options/snapshots/AAPL")
    assert "feed" not in transport.calls[0][1]

    await client.close()
    assert transport.closed is True


@pytest.mark.unit
async def test_option_contracts_adapter_parses_open_interest_catalog() -> None:
    transport = Transport(
        {
            "option_contracts": [
                {
                    "symbol": "AAPL260814C00100000",
                    "underlying_symbol": "AAPL",
                    "expiration_date": "2026-08-14",
                    "strike_price": "100",
                    "type": "call",
                    "open_interest": "1200",
                    "open_interest_date": "2026-08-11",
                    "status": "active",
                }
            ],
            "next_page_token": None,
        }
    )
    client = AlpacaOptionContractsClient(
        api_key_id="key",
        api_secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        transport=transport,
    )

    items = await client.fetch_open_interest(
        "AAPL",
        expiration_from=date(2026, 8, 12),
        expiration_to=date(2026, 9, 25),
    )

    assert len(items) == 1
    assert items[0].symbol == "AAPL260814C00100000"
    assert items[0].open_interest == 1200
    assert items[0].open_interest_date == date(2026, 8, 11)
    assert transport.calls[0][0].endswith("/v2/options/contracts")
    assert transport.calls[0][1]["underlying_symbols"] == "AAPL"

    await client.close()
    assert transport.closed is True


@pytest.mark.unit
def test_option_chain_adapter_rejects_non_alpaca_endpoint() -> None:
    with pytest.raises(ValueError, match="Options Market Data"):
        AlpacaOptionsDataClient(
            api_key_id="key",
            api_secret_key="secret",
            base_url="https://example.com",
            feed="indicative",
            transport=Transport({}),  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_option_contracts_adapter_rejects_market_data_endpoint() -> None:
    with pytest.raises(ValueError, match="Options Contracts"):
        AlpacaOptionContractsClient(
            api_key_id="key",
            api_secret_key="secret",
            base_url="https://data.alpaca.markets",
            transport=Transport({}),  # type: ignore[arg-type]
        )
