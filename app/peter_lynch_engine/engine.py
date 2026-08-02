"""Pure Peter Lynch screening calculations."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, localcontext

from .models import (
    AnnualEps,
    CriterionName,
    CriterionResult,
    LynchCategory,
    LynchMetrics,
    PeterLynchEvaluation,
    PeterLynchSnapshot,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
FIVE_BILLION = Decimal("5000000000")

# Conservative, versioned v1 proxy for industries whose results are strongly cycle-sensitive.
_CYCLICAL_SIC_RANGES: tuple[tuple[int, int], ...] = (
    (1000, 1499),  # mining, oil and gas extraction
    (2420, 2499),  # lumber and wood products
    (2800, 2899),  # chemicals and allied products
    (3310, 3399),  # primary metals
    (3720, 3728),  # aircraft and parts
    (4210, 4231),  # trucking and terminals
    (5010, 5015),  # motor vehicles and parts distribution
)
_CYCLICAL_SIC_CODES = frozenset({2911, 3011, 3711, 4011, 4412, 4512, 5511})


class PeterLynchEngine:
    """Evaluate one normalized snapshot without I/O, clocks, or mutable state."""

    ENGINE_VERSION = "1.0.0"
    POLICY_VERSION = "peter-lynch-screen-1.0.0"

    def evaluate(self, snapshot: PeterLynchSnapshot) -> PeterLynchEvaluation:
        price = _positive(snapshot.price)
        ttm_eps = _positive(snapshot.ttm_eps)
        trailing_pe = _divide(price, ttm_eps)
        eps_growth = _three_year_cagr_percent(snapshot.annual_eps)
        projected_eps = (
            ttm_eps * (ONE + eps_growth / HUNDRED)
            if ttm_eps is not None and eps_growth is not None and eps_growth > Decimal("-100")
            else None
        )
        projected_eps = _positive(projected_eps)
        projected_pe = _divide(price, projected_eps)

        equity = _positive(snapshot.equity)
        debt = _non_negative(snapshot.debt)
        debt_to_equity = (
            debt / equity * HUNDRED if debt is not None and equity is not None else None
        )
        peg = (
            trailing_pe / eps_growth
            if trailing_pe is not None and eps_growth is not None and eps_growth > ZERO
            else None
        )
        shares = _positive(snapshot.shares_outstanding)
        market_cap = price * shares if price is not None and shares is not None else None
        tangible_book = _tangible_book_value(snapshot)
        market_cap_to_tangible_book = _divide(market_cap, _positive(tangible_book))

        metrics = LynchMetrics(
            trailing_pe=trailing_pe,
            projected_forward_eps=projected_eps,
            projected_forward_pe=projected_pe,
            debt_to_equity_percent=debt_to_equity,
            eps_cagr_percent=eps_growth,
            peg=peg,
            market_cap=market_cap,
            tangible_book_value=tangible_book,
            market_cap_to_tangible_book=market_cap_to_tangible_book,
        )
        criteria = (
            _criterion(CriterionName.TRAILING_PE, trailing_pe, "< 25", lambda x: ZERO < x < 25),
            _criterion(
                CriterionName.PROJECTED_FORWARD_PE,
                projected_pe,
                "< 15",
                lambda x: ZERO < x < 15,
            ),
            _criterion(
                CriterionName.DEBT_TO_EQUITY,
                debt_to_equity,
                "< 35%",
                lambda x: ZERO <= x < 35,
            ),
            _criterion(
                CriterionName.EPS_GROWTH,
                eps_growth,
                "> 15% CAGR 3Y",
                lambda x: x > 15,
            ),
            _criterion(CriterionName.PEG, peg, "< 1.2", lambda x: ZERO <= x < Decimal("1.2")),
            _criterion(
                CriterionName.MARKET_CAP,
                market_cap,
                "> USD 5B",
                lambda x: x > FIVE_BILLION,
            ),
            _criterion(
                CriterionName.INSIDER_BUYING,
                snapshot.insider_open_market_purchase_count,
                ">= 1 open-market purchase in 365d",
                lambda x: x >= 1,
            ),
        )
        return PeterLynchEvaluation(
            symbol=snapshot.symbol.strip().upper(),
            as_of=snapshot.as_of,
            eligible=all(item.passed for item in criteria),
            category=_classify(snapshot, metrics),
            metrics=metrics,
            criteria=criteria,
            price_as_of=snapshot.price_as_of,
            fundamentals_as_of=snapshot.fundamentals_as_of,
            latest_insider_purchase_at=snapshot.latest_insider_purchase_at,
            engine_version=self.ENGINE_VERSION,
            policy_version=self.POLICY_VERSION,
        )


def _criterion(
    name: CriterionName,
    value: Decimal | int | None,
    threshold: str,
    predicate: Callable[[Decimal | int], bool],
) -> CriterionResult:
    if value is None:
        return CriterionResult(name, False, None, threshold, "required_data_unavailable")
    passed = predicate(value)
    return CriterionResult(
        name=name,
        passed=bool(passed),
        value=value,
        threshold=threshold,
        reason="passed" if passed else "threshold_not_met",
    )


def _three_year_cagr_percent(values: tuple[AnnualEps, ...]) -> Decimal | None:
    by_year: dict[int, AnnualEps] = {}
    for item in sorted(values, key=lambda value: (value.fiscal_year, value.period_end)):
        by_year[item.fiscal_year] = item
    ordered = tuple(by_year[year] for year in sorted(by_year))
    if len(ordered) < 4:
        return None
    window = ordered[-4:]
    if tuple(item.fiscal_year for item in window) != tuple(
        range(window[0].fiscal_year, window[0].fiscal_year + 4)
    ):
        return None
    start = _positive(window[0].value)
    end = _positive(window[-1].value)
    if start is None or end is None:
        return None
    with localcontext() as context:
        context.prec = 28
        return ((_cube_root(end / start) - ONE) * HUNDRED).quantize(Decimal("0.0001"))


def _cube_root(value: Decimal) -> Decimal:
    guess = value if value >= ONE else ONE
    for _ in range(40):
        updated = (Decimal(2) * guess + value / (guess * guess)) / Decimal(3)
        if abs(updated - guess) < Decimal("1e-24"):
            return updated
        guess = updated
    return guess


def _tangible_book_value(snapshot: PeterLynchSnapshot) -> Decimal | None:
    equity = _positive(snapshot.equity)
    goodwill = _non_negative(snapshot.goodwill)
    intangibles = _non_negative(snapshot.intangibles_ex_goodwill)
    if equity is None or goodwill is None or intangibles is None:
        return None
    return equity - goodwill - intangibles


def _classify(snapshot: PeterLynchSnapshot, metrics: LynchMetrics) -> LynchCategory:
    if (
        snapshot.ttm_eps is not None
        and snapshot.prior_ttm_eps is not None
        and snapshot.ttm_eps > ZERO
        and snapshot.prior_ttm_eps <= ZERO
    ):
        return LynchCategory.TURNAROUND
    if (
        metrics.market_cap_to_tangible_book is not None
        and ZERO <= metrics.market_cap_to_tangible_book < ONE
    ):
        return LynchCategory.ASSET_PLAY
    if snapshot.sic is not None and _is_cyclical_sic(snapshot.sic):
        return LynchCategory.CYCLICAL
    growth = metrics.eps_cagr_percent
    if growth is None:
        return LynchCategory.UNCLASSIFIED
    if Decimal("2") <= growth <= Decimal("4"):
        return LynchCategory.SLOW_GROWER
    if Decimal("10") <= growth <= Decimal("12"):
        return LynchCategory.STALWART
    if Decimal("20") <= growth <= Decimal("25"):
        return LynchCategory.FAST_GROWER
    return LynchCategory.UNCLASSIFIED


def _is_cyclical_sic(sic: int) -> bool:
    return sic in _CYCLICAL_SIC_CODES or any(
        start <= sic <= end for start, end in _CYCLICAL_SIC_RANGES
    )


def _positive(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value.is_finite() and value > ZERO else None


def _non_negative(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value.is_finite() and value >= ZERO else None


def _divide(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= ZERO:
        return None
    return numerator / denominator
