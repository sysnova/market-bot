"""Cached ticker-to-CIK lookup using the SEC's official company ticker file."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import cast

import httpx

from .sec_adapter import (
    SecAdapterError,
    SecConfigurationError,
    SecEdgarAdapter,
    SecEdgarConfig,
    SecHttpStatusError,
    SecInvalidJsonError,
    SecPayloadError,
    SecRateLimitError,
    SecTimeoutError,
    SecTransportError,
)

_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKER_ENDPOINT = "/files/company_tickers.json"


class SecTickerNotFoundError(SecAdapterError):
    """The official SEC ticker map has no entry for the requested symbol."""


class SecTickerResolver:
    """Load the official map once per instance and resolve normalized symbols."""

    def __init__(
        self,
        config: SecEdgarConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._cache: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> SecTickerResolver:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def resolve(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if (
            not normalized
            or len(normalized) > 15
            or not normalized[0].isalpha()
            or any(not character.isalnum() and character not in ".-" for character in normalized)
        ):
            raise SecConfigurationError("SEC ticker symbol is invalid")
        mapping = await self._mapping()
        try:
            return mapping[normalized]
        except KeyError as error:
            raise SecTickerNotFoundError(
                f"SEC ticker map has no entry for {normalized}"
            ) from error

    async def _mapping(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        async with self._lock:
            if self._cache is None:
                self._cache = await self._load()
            return self._cache

    async def _load(self) -> dict[str, str]:
        try:
            response = await self._client.get(
                _TICKER_URL,
                headers={
                    "User-Agent": self._config.user_agent,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise SecTimeoutError("SEC ticker-map request timed out") from error
        except httpx.HTTPError as error:
            raise SecTransportError("SEC ticker-map transport error") from error
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SecRateLimitError(_TICKER_ENDPOINT, seconds)
        if not 200 <= response.status_code < 300:
            raise SecHttpStatusError(_TICKER_ENDPOINT, response.status_code)
        try:
            payload: object = response.json()
        except ValueError as error:
            raise SecInvalidJsonError("SEC ticker map is invalid JSON") from error
        if not isinstance(payload, Mapping):
            raise SecPayloadError("SEC ticker map must be an object")
        mapping: dict[str, str] = {}
        for raw_entry in cast("Mapping[object, object]", payload).values():
            if not isinstance(raw_entry, Mapping):
                raise SecPayloadError("SEC ticker map entry must be an object")
            entry = cast("Mapping[object, object]", raw_entry)
            ticker = entry.get("ticker")
            cik = entry.get("cik_str")
            if not isinstance(ticker, str) or isinstance(cik, bool) or not isinstance(cik, int):
                raise SecPayloadError("SEC ticker map entry is malformed")
            normalized_ticker = ticker.strip().upper()
            normalized_cik = SecEdgarAdapter.normalize_cik(cik)
            existing = mapping.get(normalized_ticker)
            if existing is not None and existing != normalized_cik:
                raise SecPayloadError(f"SEC ticker map conflicts for {normalized_ticker}")
            mapping[normalized_ticker] = normalized_cik
        if not mapping:
            raise SecPayloadError("SEC ticker map is empty")
        return mapping
