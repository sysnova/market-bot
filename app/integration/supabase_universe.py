"""Read the shared Stock Analyzer universe from its Supabase Edge Function."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import httpx

_FUNCTION_PATH = "/functions/v1/stock-messages-api"


class SupabaseUniverseError(RuntimeError):
    """Raised when the shared universe cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class SupabaseUniverseConfig:
    base_url: str
    desktop_api_key: str
    fallback_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    symbols: tuple[str, ...]
    source: str
    watchlist_updated_at: str | None = None
    holdings_updated_at: str | None = None


class SupabaseUniverseClient:
    """Mirror Stock Analyzer's watchlist + positive-holdings universe contract."""

    def __init__(
        self,
        config: SupabaseUniverseConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        if not config.base_url.strip() or not config.desktop_api_key.strip():
            raise ValueError("Supabase universe URL and desktop API key cannot be blank")
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.desktop_api_key
        self._fallback_symbols = _normalize_symbols(config.fallback_symbols)
        self._client = client

    async def get_universe(self) -> UniverseSnapshot:
        watchlist, holdings = await asyncio.gather(
            self._get_json("watchlist"),
            self._get_json("holdings"),
        )
        watchlist_symbols = _normalize_symbols(_string_sequence(watchlist.get("symbols")))
        holding_symbols = _positive_holding_symbols(holdings.get("positions"))
        symbols = _normalize_symbols(
            (*watchlist_symbols, *holding_symbols, *self._fallback_symbols)
        )
        if not symbols:
            raise SupabaseUniverseError("Supabase returned an empty market universe")
        return UniverseSnapshot(
            symbols=symbols,
            source="supabase",
            watchlist_updated_at=_optional_string(watchlist.get("updatedAt")),
            holdings_updated_at=_optional_string(holdings.get("updatedAt")),
        )

    async def _get_json(self, path: str) -> Mapping[str, object]:
        try:
            response = await self._client.get(
                f"{self._base_url}{_FUNCTION_PATH}/{path}",
                headers={"X-Stock-Desktop-Key": self._api_key},
            )
        except httpx.HTTPError as error:
            raise SupabaseUniverseError(f"Supabase universe request failed: {path}") from error
        if not response.is_success:
            raise SupabaseUniverseError(
                f"Supabase universe request failed: {path} returned {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise SupabaseUniverseError(
                f"Supabase universe response was not JSON: {path}"
            ) from error
        if not isinstance(payload, Mapping):
            raise SupabaseUniverseError(f"Supabase universe response was not an object: {path}")
        return cast("Mapping[str, object]", payload)


def fallback_universe(symbols: Sequence[str], *, source: str = "env-fallback") -> UniverseSnapshot:
    normalized = _normalize_symbols(symbols)
    if not normalized:
        raise ValueError("at least one fallback market symbol is required")
    return UniverseSnapshot(symbols=normalized, source=source)


def _positive_holding_symbols(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    symbols: list[str] = []
    for row in cast("list[object]", value):
        if not isinstance(row, Mapping):
            continue
        typed_row = cast("Mapping[str, object]", row)
        shares = typed_row.get("shares")
        try:
            numeric_shares = (
                float(shares)
                if isinstance(shares, (int, float, str)) and not isinstance(shares, bool)
                else 0.0
            )
        except (TypeError, ValueError):
            continue
        symbol = typed_row.get("symbol")
        if numeric_shares > 0 and isinstance(symbol, str):
            symbols.append(symbol)
    return _normalize_symbols(symbols)


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in cast("list[object]", value) if isinstance(item, str))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
    )
