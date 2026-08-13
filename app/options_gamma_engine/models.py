"""Immutable provider-neutral inputs owned by Options Gamma."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class OptionContractSnapshot:
    symbol: str
    underlying_symbol: str
    expiration_date: date
    strike_price: Decimal
    option_type: Literal["call", "put"] | str
    open_interest: Decimal | None
    open_interest_date: date | None
    gamma: Decimal | None
    implied_volatility: Decimal | None
    bid_price: Decimal | None
    ask_price: Decimal | None
    latest_trade_price: Decimal | None
    snapshot_at: datetime | None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.underlying_symbol.strip():
            raise ValueError("option and underlying symbols are required")
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        if self.strike_price <= 0:
            raise ValueError("option strike must be positive")
        if self.open_interest is not None and self.open_interest < 0:
            raise ValueError("open interest cannot be negative")
        if self.gamma is not None and self.gamma < 0:
            raise ValueError("gamma cannot be negative")
        if self.snapshot_at is not None and (
            self.snapshot_at.tzinfo is None
            or self.snapshot_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("snapshot_at must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class OptionsGammaContext:
    symbol: str
    spot_price: Decimal
    spot_as_of: datetime
    generated_at: datetime
    expiration_from: date
    expiration_to: date
    contracts: tuple[OptionContractSnapshot, ...]

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or self.spot_price <= 0:
            raise ValueError("Options Gamma requires a symbol and positive spot")
        for timestamp in (self.spot_as_of, self.generated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
                raise ValueError("Options Gamma timestamps must be timezone-aware UTC")
        if self.spot_as_of > self.generated_at:
            raise ValueError("spot_as_of cannot be after generated_at")
        if self.expiration_to < self.expiration_from:
            raise ValueError("expiration_to cannot precede expiration_from")
        if any(item.underlying_symbol.strip().upper() != symbol for item in self.contracts):
            raise ValueError("all option contracts must belong to the context symbol")
