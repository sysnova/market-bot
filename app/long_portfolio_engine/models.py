"""Immutable policy values for long-portfolio accumulation alerts."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PortfolioAllocation(_FrozenModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,14}$")
    weight_percent: Decimal = Field(gt=Decimal("0"), le=Decimal("100"))


class LongPortfolioPolicy(_FrozenModel):
    rule_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    horizon_end: str
    portfolio_capital_usd: Decimal = Field(gt=Decimal("0"))
    cash_weight_percent: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    excluded_symbols: tuple[str, ...]
    reserved_allocations: tuple[PortfolioAllocation, ...]
    allocations: tuple[PortfolioAllocation, ...]
    minimum_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    minimum_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    minimum_setup_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    minimum_entry_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    minimum_trend_template_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    minimum_qualified_sessions: int = Field(ge=1, le=10)
    initial_tranche_percent: Decimal = Field(gt=Decimal("0"), le=Decimal("100"))
    maximum_signal_age: timedelta = Field(gt=timedelta())
    cooldown: timedelta = Field(gt=timedelta())
    alert_ttl: timedelta = Field(gt=timedelta())
    allowed_market_regimes: tuple[str, ...]
    blocked_risk_flags: tuple[str, ...]

    @model_validator(mode="after")
    def validate_portfolio(self) -> LongPortfolioPolicy:
        allocation_symbols = tuple(item.symbol for item in self.allocations)
        if len(allocation_symbols) != len(set(allocation_symbols)):
            raise ValueError("allocation symbols must be unique")
        if set(allocation_symbols) & set(self.excluded_symbols):
            raise ValueError("excluded symbols cannot have allocations")
        total = self.cash_weight_percent + sum(
            (item.weight_percent for item in (*self.reserved_allocations, *self.allocations)),
            Decimal(),
        )
        if total > Decimal("100"):
            raise ValueError("portfolio weights cannot exceed 100 percent")
        return self

    def allocation_for(self, symbol: str) -> PortfolioAllocation | None:
        normalized = symbol.strip().upper()
        return next((item for item in self.allocations if item.symbol == normalized), None)

    @property
    def configured_weight_percent(self) -> Decimal:
        return self.cash_weight_percent + sum(
            (item.weight_percent for item in (*self.reserved_allocations, *self.allocations)),
            Decimal(),
        )

    @property
    def unallocated_weight_percent(self) -> Decimal:
        return Decimal("100") - self.configured_weight_percent
