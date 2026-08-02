"""Immutable values owned by the Peter Lynch engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class LynchCategory(StrEnum):
    """Operational version of the six Peter Lynch categories."""

    TURNAROUND = "TURNAROUND"
    ASSET_PLAY = "ASSET_PLAY"
    CYCLICAL = "CYCLICAL"
    SLOW_GROWER = "SLOW_GROWER"
    STALWART = "STALWART"
    FAST_GROWER = "FAST_GROWER"
    UNCLASSIFIED = "UNCLASSIFIED"


class CriterionName(StrEnum):
    """The six required financial criteria and one informational signal."""

    TRAILING_PE = "trailing_pe"
    PROJECTED_FORWARD_PE = "projected_forward_pe"
    DEBT_TO_EQUITY = "debt_to_equity"
    EPS_GROWTH = "eps_growth"
    PEG = "peg"
    MARKET_CAP = "market_cap"
    INSIDER_BUYING = "insider_buying"


@dataclass(frozen=True, slots=True)
class AnnualEps:
    """Diluted EPS for one completed fiscal year."""

    fiscal_year: int
    period_end: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class PeterLynchSnapshot:
    """Normalized provider facts consumed by the pure engine."""

    symbol: str
    as_of: date
    price: Decimal | None
    price_as_of: date | None
    ttm_eps: Decimal | None
    prior_ttm_eps: Decimal | None
    annual_eps: tuple[AnnualEps, ...]
    debt: Decimal | None
    equity: Decimal | None
    goodwill: Decimal | None
    intangibles_ex_goodwill: Decimal | None
    shares_outstanding: Decimal | None
    sic: int | None
    insider_open_market_purchase_count: int | None
    fundamentals_as_of: date | None
    latest_insider_purchase_at: date | None


@dataclass(frozen=True, slots=True)
class LynchMetrics:
    """Calculated values retained as evidence."""

    trailing_pe: Decimal | None
    projected_forward_eps: Decimal | None
    projected_forward_pe: Decimal | None
    debt_to_equity_percent: Decimal | None
    eps_cagr_percent: Decimal | None
    peg: Decimal | None
    market_cap: Decimal | None
    tangible_book_value: Decimal | None
    market_cap_to_tangible_book: Decimal | None


@dataclass(frozen=True, slots=True)
class CriterionResult:
    """Traceable result of one required criterion or informational signal."""

    name: CriterionName
    passed: bool
    value: Decimal | int | None
    threshold: str
    reason: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class PeterLynchEvaluation:
    """Complete current evaluation for one watchlist symbol."""

    symbol: str
    as_of: date
    eligible: bool
    category: LynchCategory
    metrics: LynchMetrics
    criteria: tuple[CriterionResult, ...]
    price_as_of: date | None
    fundamentals_as_of: date | None
    latest_insider_purchase_at: date | None
    engine_version: str = "1.0.0"
    policy_version: str = "peter-lynch-screen-1.0.0"

    @property
    def passed_count(self) -> int:
        return sum(item.passed for item in self.criteria if item.required)

    @property
    def required_count(self) -> int:
        return sum(item.required for item in self.criteria)

    @property
    def failed_criteria(self) -> tuple[CriterionName, ...]:
        return tuple(
            item.name for item in self.criteria if item.required and not item.passed
        )
