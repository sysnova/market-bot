"""Read-only Alpaca Stock Market Data REST adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from .ports import HttpTransport


class AlpacaMarketDataError(RuntimeError):
    """Raised when Alpaca rejects or malforms a market-data request."""


@dataclass(frozen=True, slots=True)
class AlpacaNewsArticle:
    """Normalized, terminal-safe-independent projection of one Alpaca news record."""

    article_id: int
    headline: str
    summary: str
    author: str
    created_at: datetime
    updated_at: datetime
    url: str
    symbols: tuple[str, ...]
    source: str


class AlpacaRestClient:
    """Client exposing read-only market data and news, never Trading API calls."""

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

    async def fetch_news(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime | None = None,
        limit: int = 50,
    ) -> tuple[AlpacaNewsArticle, ...]:
        """Fetch and normalize paginated news for a bounded group of symbols."""

        normalized_symbols = _symbols(symbols)
        if start.tzinfo is None or (end is not None and end.tzinfo is None):
            raise ValueError("news boundaries must be timezone-aware")
        if end is not None and end <= start:
            raise ValueError("news end must be later than start")
        if not 1 <= limit <= 50:
            raise ValueError("news limit must be between 1 and 50")
        params = {
            "exclude_contentless": "false",
            "include_content": "false",
            "limit": str(limit),
            "sort": "desc",
            "start": _rfc3339(start),
            "symbols": ",".join(normalized_symbols),
        }
        if end is not None:
            params["end"] = _rfc3339(end)

        collected: list[AlpacaNewsArticle] = []
        for _ in range(self._max_pages):
            payload = await self._get("/v1beta1/news", params)
            records = payload.get("news")
            if not isinstance(records, list):
                raise AlpacaMarketDataError("Alpaca news response has no news list")
            for record in cast("list[object]", records):
                if not isinstance(record, Mapping):
                    raise AlpacaMarketDataError("Alpaca news record is malformed")
                collected.append(_news_article(cast("Mapping[str, object]", record)))
            token = payload.get("next_page_token")
            if token is None:
                return tuple(collected)
            if not isinstance(token, str) or not token:
                raise AlpacaMarketDataError("Alpaca news next page token is malformed")
            params["page_token"] = token
        raise AlpacaMarketDataError("Alpaca news pagination exceeded max_pages")

    async def close(self) -> None:
        await self._transport.close()

    async def _get(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        try:
            response = await self._transport.get(
                f"{self._base_url}{path}", headers=self._headers, params=dict(params)
            )
        except Exception as error:
            raise AlpacaMarketDataError("Alpaca market-data request failed") from error
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaMarketDataError(
                f"Alpaca market-data request failed with HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise AlpacaMarketDataError("Alpaca market-data response is not JSON") from error
        if not isinstance(payload, Mapping):
            raise AlpacaMarketDataError("Alpaca market-data response must be an object")
        return cast("Mapping[str, object]", payload)


def _symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("at least one non-blank symbol is required")
    return normalized


def _news_article(record: Mapping[str, object]) -> AlpacaNewsArticle:
    try:
        article_id = record["id"]
        headline = record["headline"]
        created_at = record["created_at"]
        symbols = record["symbols"]
        if not isinstance(article_id, int) or isinstance(article_id, bool):
            raise TypeError
        if not isinstance(headline, str) or not headline.strip():
            raise TypeError
        if not isinstance(created_at, str):
            raise TypeError
        if not isinstance(symbols, list):
            raise TypeError
        raw_symbols = cast("list[object]", symbols)
        if not all(isinstance(symbol, str) for symbol in raw_symbols):
            raise TypeError
        updated_value = record.get("updated_at", created_at)
        if not isinstance(updated_value, str):
            raise TypeError
        return AlpacaNewsArticle(
            article_id=article_id,
            headline=headline.strip(),
            summary=_optional_text(record.get("summary")),
            author=_optional_text(record.get("author")),
            created_at=_timestamp(created_at),
            updated_at=_timestamp(updated_value),
            url=_optional_text(record.get("url")),
            symbols=_symbols(tuple(cast("list[str]", raw_symbols))),
            source=_optional_text(record.get("source")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AlpacaMarketDataError("Alpaca news record is malformed") from error


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("news timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
