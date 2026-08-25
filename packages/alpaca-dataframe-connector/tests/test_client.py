from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
import pytest

from alpaca_dataframe_connector import (
    AlpacaConfig,
    AlpacaDataClient,
    AlpacaDataError,
)

UTC = timezone.utc


def _client(handler: Any, **config_overrides: Any) -> AlpacaDataClient:
    config_values: dict[str, Any] = {
        "api_key_id": "key",
        "api_secret_key": "secret",
        "feed": "iex",
        "adjustment": "all",
    }
    config_values.update(config_overrides)
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return AlpacaDataClient(AlpacaConfig(**config_values), http_client=http_client)


def test_get_daily_bars_returns_one_yfinance_shaped_dataframe_per_ticker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/bars"
        assert request.headers["APCA-API-KEY-ID"] == "key"
        assert request.headers["APCA-API-SECRET-KEY"] == "secret"
        assert request.url.params["symbols"] == "AAPL,MSFT"
        assert request.url.params["timeframe"] == "1Day"
        assert request.url.params["feed"] == "iex"
        assert request.url.params["adjustment"] == "all"
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-01-02T05:00:00Z",
                            "o": 100.0,
                            "h": 110.0,
                            "l": 99.0,
                            "c": 108.0,
                            "v": 123456,
                            "n": 1000,
                            "vw": 105.0,
                        }
                    ],
                    "MSFT": [
                        {
                            "t": "2026-01-02T05:00:00Z",
                            "o": 200.0,
                            "h": 202.0,
                            "l": 195.0,
                            "c": 201.0,
                            "v": 654321,
                        }
                    ],
                },
                "next_page_token": None,
            },
        )

    client = _client(handler)

    result = client.get_daily_bars(
        [" aapl ", "MSFT", "AAPL"],
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 3, tzinfo=UTC),
    )

    assert list(result) == ["AAPL", "MSFT"]
    assert list(result["AAPL"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert result["AAPL"].index.name == "Date"
    assert result["AAPL"].index[0] == pd.Timestamp("2026-01-02")
    assert result["AAPL"].loc["2026-01-02", "Close"] == 108.0
    assert result["MSFT"].loc["2026-01-02", "Volume"] == 654321


def test_get_daily_bars_paginates_and_returns_empty_frame_for_missing_symbol() -> None:
    pages = iter(
        [
            {
                "bars": {
                    "AAPL": [
                        {"t": "2026-01-02T05:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}
                    ]
                },
                "next_page_token": "page-2",
            },
            {
                "bars": {
                    "AAPL": [
                        {"t": "2026-01-03T05:00:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 20}
                    ]
                },
                "next_page_token": None,
            },
        ]
    )
    tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.url.params.get("page_token"))
        return httpx.Response(200, json=next(pages))

    client = _client(handler)
    result = client.get_daily_bars(
        ["AAPL", "EMPTY"],
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 4, tzinfo=UTC),
    )

    assert tokens == [None, "page-2"]
    assert len(result["AAPL"]) == 2
    assert result["EMPTY"].empty
    assert list(result["EMPTY"].columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_download_accepts_yfinance_style_three_year_period() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["start"] == "2023-08-16T12:00:00Z"
        assert request.url.params["end"] == "2026-08-16T12:00:00Z"
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    client = _client(handler)
    result = client.download(
        ["AAPL"],
        period="3y",
        now=datetime(2026, 8, 16, 12, tzinfo=UTC),
    )

    assert calls == 1
    assert result["AAPL"].empty


def test_download_accepts_parameterized_period_and_four_hour_interval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "2026-02-16T12:00:00Z"
        assert request.url.params["end"] == "2026-08-16T12:00:00Z"
        assert request.url.params["timeframe"] == "4Hour"
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-14T14:00:00Z",
                            "o": 100,
                            "h": 105,
                            "l": 99,
                            "c": 104,
                            "v": 5000,
                        }
                    ]
                },
                "next_page_token": None,
            },
        )

    client = _client(handler)

    result = client.download(
        ["AAPL"],
        period="6mo",
        interval="4h",
        now=datetime(2026, 8, 16, 12, tzinfo=UTC),
    )

    index = result["AAPL"].index
    assert index.name == "Datetime"
    assert str(index.tz) == "America/New_York"
    assert index[0] == pd.Timestamp("2026-08-14 10:00:00", tz="America/New_York")


def test_get_bars_accepts_one_minute_interval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timeframe"] == "1Min"
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    client = _client(handler)

    result = client.get_bars(
        ["AAPL"],
        interval="1m",
        start=datetime(2026, 8, 14, 13, 30, tzinfo=UTC),
        end=datetime(2026, 8, 14, 14, 30, tzinfo=UTC),
    )

    assert result["AAPL"].index.name == "Datetime"
    assert str(result["AAPL"].index.tz) == "America/New_York"


@pytest.mark.parametrize(
    "interval",
    ["60m", "24h", "2d", "2wk", "4mo", "tick"],
)
def test_download_rejects_intervals_not_supported_by_alpaca(interval: str) -> None:
    client = _client(lambda _: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="interval"):
        client.download(["AAPL"], interval=interval)


@pytest.mark.parametrize(
    ("period", "expected_start"),
    [
        ("90m", "2026-08-16T10:30:00Z"),
        ("12h", "2026-08-16T00:00:00Z"),
        ("5d", "2026-08-11T12:00:00Z"),
        ("2wk", "2026-08-02T12:00:00Z"),
        ("3mo", "2026-05-16T12:00:00Z"),
        ("2y", "2024-08-16T12:00:00Z"),
        ("ytd", "2026-01-01T00:00:00Z"),
        ("max", "1970-01-01T00:00:00Z"),
    ],
)
def test_download_accepts_parameterized_periods(period: str, expected_start: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == expected_start
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    client = _client(handler)

    client.download(
        ["AAPL"], period=period, now=datetime(2026, 8, 16, 12, tzinfo=UTC)
    )


def test_http_error_does_not_leak_credentials() -> None:
    client = _client(
        lambda _: httpx.Response(401, text="key secret unauthorized"), max_retries=0
    )

    with pytest.raises(AlpacaDataError) as captured:
        client.download(["AAPL"], period="1y")

    assert "key" not in str(captured.value)
    assert "secret" not in str(captured.value)
    assert "HTTP 401" in str(captured.value)


def test_retries_rate_limit_response() -> None:
    statuses = iter([429, 200])
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 429:
            return httpx.Response(status, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    client = _client(handler, max_retries=1)
    client._sleep = sleeps.append

    client.download(["AAPL"], period="1y")

    assert sleeps == [0.0]
