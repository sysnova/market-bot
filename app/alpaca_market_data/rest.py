"""Read-only Alpaca Stock Market Data REST adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from .ports import HttpTransport


class AlpacaMarketDataError(RuntimeError):
    """Raised when Alpaca rejects or malforms a market-data request."""


class AlpacaRestClient:
    """Client exposing only historical bars and snapshots, never Trading API calls."""

    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str,
        feed: str,
        adjustment: str = "split",
        transport: HttpTransport,
        max_pages: int = 100,
    ) -> None:
        if not api_key_id.strip() or not api_secret_key.strip():
            raise ValueError("Alpaca credentials cannot be blank")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "data.alpaca.markets":
            raise ValueError("Alpaca REST client must use the Stock Market Data endpoint")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        if adjustment not in {"raw", "split", "dividend", "all"}:
            raise ValueError("unsupported Alpaca bar adjustment")
        self._headers = {
            "APCA-API-KEY-ID": api_key_id,
            "APCA-API-SECRET-KEY": api_secret_key,
        }
        self._base_url = base_url.rstrip("/")
        self._feed = feed
        self._adjustment = adjustment
        self._transport = transport
        self._max_pages = max_pages

    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]:
        normalized_symbols = _symbols(symbols)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("bar boundaries must be timezone-aware")
        if end <= start:
            raise ValueError("bar end must be later than start")
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        params = {
            "adjustment": self._adjustment,
            "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "feed": self._feed,
            "limit": str(limit),
            "sort": "asc",
            "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "symbols": ",".join(normalized_symbols),
            "timeframe": timeframe,
        }
        collected: dict[str, list[Mapping[str, object]]] = {
            symbol: [] for symbol in normalized_symbols
        }
        for _ in range(self._max_pages):
            payload = await self._get("/v2/stocks/bars", params)
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, Mapping):
                raise AlpacaMarketDataError("Alpaca bars response has no bars object")
            for symbol, records in cast("Mapping[object, object]", raw_bars).items():
                if not isinstance(symbol, str) or not isinstance(records, list):
                    raise AlpacaMarketDataError("Alpaca bars response is malformed")
                target = collected.setdefault(symbol, [])
                typed_records = cast("list[object]", records)
                for record in typed_records:
                    if not isinstance(record, Mapping):
                        raise AlpacaMarketDataError("Alpaca bar record is malformed")
                    target.append(cast("Mapping[str, object]", record))
            token = payload.get("next_page_token")
            if token is None:
                return collected
            if not isinstance(token, str) or not token:
                raise AlpacaMarketDataError("Alpaca next page token is malformed")
            params["page_token"] = token
        raise AlpacaMarketDataError("Alpaca bars pagination exceeded max_pages")

    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, Mapping[str, object]]:
        normalized_symbols = _symbols(symbols)
        payload = await self._get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(normalized_symbols), "feed": self._feed},
        )
        result: dict[str, Mapping[str, object]] = {}
        for symbol, snapshot in payload.items():
            if not isinstance(snapshot, Mapping):
                raise AlpacaMarketDataError("Alpaca snapshots response is malformed")
            result[symbol] = cast("Mapping[str, object]", snapshot)
        return result

    async def close(self) -> None:
        await self._transport.close()

    async def _get(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        response = await self._transport.get(
            f"{self._base_url}{path}", headers=self._headers, params=dict(params)
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaMarketDataError(
                f"Alpaca market-data request failed with HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise AlpacaMarketDataError("Alpaca market-data response must be an object")
        return cast("Mapping[str, object]", payload)


def _symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("at least one non-blank symbol is required")
    return normalized
