"""Read-only Alpaca Option Chain adapter for the Options Gamma composition."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlsplit

from app.alpaca_market_data.ports import HttpTransport
from app.options_gamma_engine import OptionContractSnapshot

_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z0-9.]{1,6})(?P<date>\d{6})(?P<type>[CP])(?P<strike>\d{8})$"
)


class AlpacaOptionsDataError(RuntimeError):
    """Raised when Alpaca rejects or malforms an option-chain request."""


class AlpacaOptionContractsError(RuntimeError):
    """Raised when Alpaca rejects or malforms option contract metadata."""


@dataclass(frozen=True, slots=True)
class OptionOpenInterest:
    """Open-interest fields supplied by the separate Trading API catalog."""

    symbol: str
    open_interest: Decimal | None
    open_interest_date: date | None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("option contract symbol cannot be blank")
        if self.open_interest is not None and self.open_interest < 0:
            raise ValueError("option open interest cannot be negative")


class AlpacaOptionsDataClient:
    """Fetch latest option-chain snapshots without exposing provider shapes."""

    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str,
        feed: str | None,
        transport: HttpTransport,
        max_pages: int = 100,
    ) -> None:
        if not api_key_id.strip() or not api_secret_key.strip():
            raise ValueError("Alpaca credentials cannot be blank")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname != "data.alpaca.markets":
            raise ValueError("Alpaca options client must use the Options Market Data endpoint")
        if feed not in {None, "opra", "indicative"}:
            raise ValueError("unsupported Alpaca options feed")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self._headers = {
            "APCA-API-KEY-ID": api_key_id,
            "APCA-API-SECRET-KEY": api_secret_key,
        }
        self._base_url = base_url.rstrip("/")
        self._feed = feed
        self._transport = transport
        self._max_pages = max_pages

    async def fetch_chain(
        self,
        symbol: str,
        *,
        expiration_from: date,
        expiration_to: date,
        strike_from: str,
        strike_to: str,
    ) -> tuple[OptionContractSnapshot, ...]:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("option-chain symbol cannot be blank")
        if expiration_to < expiration_from:
            raise ValueError("option-chain expiration range is invalid")
        params = {
            "expiration_date_gte": expiration_from.isoformat(),
            "expiration_date_lte": expiration_to.isoformat(),
            "strike_price_gte": strike_from,
            "strike_price_lte": strike_to,
            "limit": "1000",
        }
        if self._feed is not None:
            params["feed"] = self._feed
        collected: list[OptionContractSnapshot] = []
        for _ in range(self._max_pages):
            payload = await self._get(
                f"/v1beta1/options/snapshots/{normalized}", params
            )
            raw_snapshots = payload.get("snapshots")
            if not isinstance(raw_snapshots, Mapping):
                raise AlpacaOptionsDataError("Alpaca option chain has no snapshots object")
            snapshots = cast("Mapping[object, object]", raw_snapshots)
            for contract_symbol, raw_snapshot in snapshots.items():
                if not isinstance(contract_symbol, str) or not isinstance(raw_snapshot, Mapping):
                    raise AlpacaOptionsDataError("Alpaca option snapshot is malformed")
                item = _snapshot(
                    contract_symbol,
                    normalized,
                    cast("Mapping[str, object]", raw_snapshot),
                )
                if item is not None:
                    collected.append(item)
            token = payload.get("next_page_token")
            if token is None:
                return tuple(collected)
            if not isinstance(token, str) or not token:
                raise AlpacaOptionsDataError("Alpaca option pagination token is malformed")
            params["page_token"] = token
        raise AlpacaOptionsDataError("Alpaca option pagination exceeded max_pages")

    async def close(self) -> None:
        await self._transport.close()

    async def _get(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        response = await self._transport.get(
            f"{self._base_url}{path}",
            headers=self._headers,
            params=dict(params),
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaOptionsDataError(
                f"Alpaca options request failed with HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise AlpacaOptionsDataError("Alpaca options response must be an object")
        return cast("Mapping[str, object]", payload)


class AlpacaOptionContractsClient:
    """Fetch read-only option metadata and OI from Alpaca's contract catalog."""

    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str,
        transport: HttpTransport,
        max_pages: int = 100,
    ) -> None:
        if not api_key_id.strip() or not api_secret_key.strip():
            raise ValueError("Alpaca credentials cannot be blank")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "api.alpaca.markets",
            "paper-api.alpaca.markets",
        }:
            raise ValueError(
                "Alpaca option contracts client must use the Options Contracts endpoint"
            )
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self._headers = {
            "APCA-API-KEY-ID": api_key_id,
            "APCA-API-SECRET-KEY": api_secret_key,
        }
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._max_pages = max_pages

    async def fetch_open_interest(
        self,
        symbol: str,
        *,
        expiration_from: date,
        expiration_to: date,
    ) -> tuple[OptionOpenInterest, ...]:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("option-chain symbol cannot be blank")
        if expiration_to < expiration_from:
            raise ValueError("option-chain expiration range is invalid")
        params = {
            "expiration_date_gte": expiration_from.isoformat(),
            "expiration_date_lte": expiration_to.isoformat(),
            "limit": "10000",
            "status": "active",
            "underlying_symbols": normalized,
        }
        collected: list[OptionOpenInterest] = []
        for _ in range(self._max_pages):
            payload = await self._get("/v2/options/contracts", params)
            raw_contracts = payload.get("option_contracts")
            if not isinstance(raw_contracts, list):
                raise AlpacaOptionContractsError(
                    "Alpaca option contracts response has no contract list"
                )
            for raw_contract in cast("list[object]", raw_contracts):
                if not isinstance(raw_contract, Mapping):
                    raise AlpacaOptionContractsError(
                        "Alpaca option contract metadata is malformed"
                    )
                item = _open_interest(
                    cast("Mapping[str, object]", raw_contract),
                    underlying_symbol=normalized,
                )
                if item is not None:
                    collected.append(item)
            token = payload.get("next_page_token")
            if token is None:
                return tuple(collected)
            if not isinstance(token, str) or not token:
                raise AlpacaOptionContractsError(
                    "Alpaca option contracts pagination token is malformed"
                )
            params["page_token"] = token
        raise AlpacaOptionContractsError(
            "Alpaca option contracts pagination exceeded max_pages"
        )

    async def close(self) -> None:
        await self._transport.close()

    async def _get(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        response = await self._transport.get(
            f"{self._base_url}{path}",
            headers=self._headers,
            params=dict(params),
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaOptionContractsError(
                "Alpaca option contracts request failed with HTTP "
                f"{response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise AlpacaOptionContractsError(
                "Alpaca option contracts response must be an object"
            )
        return cast("Mapping[str, object]", payload)


def _snapshot(
    contract_symbol: str,
    underlying_symbol: str,
    raw: Mapping[str, object],
) -> OptionContractSnapshot | None:
    parsed = _parse_occ_symbol(contract_symbol)
    if parsed is None:
        return None
    expiration, option_type, strike = parsed
    latest_trade = _mapping(raw.get("latestTrade") or raw.get("latest_trade"))
    latest_quote = _mapping(raw.get("latestQuote") or raw.get("latest_quote"))
    greeks = _mapping(raw.get("greeks"))
    snapshot_at = max(
        (
            item
            for item in (
                _datetime(latest_trade.get("t")),
                _datetime(latest_quote.get("t")),
            )
            if item is not None
        ),
        default=None,
    )
    return OptionContractSnapshot(
        symbol=contract_symbol.strip().upper(),
        underlying_symbol=underlying_symbol,
        expiration_date=expiration,
        strike_price=strike,
        option_type=option_type,
        open_interest=_decimal(raw.get("openInterest") or raw.get("open_interest")),
        open_interest_date=_date(
            raw.get("openInterestDate") or raw.get("open_interest_date")
        ),
        gamma=_decimal(greeks.get("gamma")),
        implied_volatility=_decimal(
            raw.get("impliedVolatility") or raw.get("implied_volatility")
        ),
        bid_price=_decimal(latest_quote.get("bp") or latest_quote.get("bid_price")),
        ask_price=_decimal(latest_quote.get("ap") or latest_quote.get("ask_price")),
        latest_trade_price=_decimal(
            latest_trade.get("p") or latest_trade.get("price")
        ),
        snapshot_at=snapshot_at,
    )


def _open_interest(
    raw: Mapping[str, object], *, underlying_symbol: str
) -> OptionOpenInterest | None:
    symbol = raw.get("symbol")
    underlying = raw.get("underlying_symbol")
    if not isinstance(symbol, str) or underlying != underlying_symbol:
        return None
    return OptionOpenInterest(
        symbol=symbol.strip().upper(),
        open_interest=_decimal(raw.get("open_interest")),
        open_interest_date=_date(raw.get("open_interest_date")),
    )


def _parse_occ_symbol(value: str) -> tuple[date, str, Decimal] | None:
    match = _OCC_SYMBOL.fullmatch(value.strip().upper())
    if match is None:
        return None
    expiration = datetime.strptime(match.group("date"), "%y%m%d").date()
    option_type = "call" if match.group("type") == "C" else "put"
    strike = Decimal(match.group("strike")) / Decimal("1000")
    return expiration, option_type, strike


def _mapping(value: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", value) if isinstance(value, Mapping) else {}


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
