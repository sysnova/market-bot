from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.peter_lynch_engine import (
    AnnualEps,
    CriterionName,
    LynchCategory,
    PeterLynchEngine,
    PeterLynchSnapshot,
)

AS_OF = date(2026, 8, 2)


def snapshot(**changes: object) -> PeterLynchSnapshot:
    values: dict[str, object] = {
        "symbol": "TEST",
        "as_of": AS_OF,
        "price": Decimal("20"),
        "price_as_of": date(2026, 7, 31),
        "ttm_eps": Decimal("2"),
        "prior_ttm_eps": Decimal("1.6"),
        "annual_eps": (
            AnnualEps(2022, date(2022, 12, 31), Decimal("1.00")),
            AnnualEps(2023, date(2023, 12, 31), Decimal("1.25")),
            AnnualEps(2024, date(2024, 12, 31), Decimal("1.55")),
            AnnualEps(2025, date(2025, 12, 31), Decimal("1.90")),
        ),
        "debt": Decimal("20"),
        "equity": Decimal("100"),
        "goodwill": Decimal("5"),
        "intangibles_ex_goodwill": Decimal("5"),
        "shares_outstanding": Decimal("300000000"),
        "sic": 7372,
        "insider_open_market_purchase_count": 1,
        "fundamentals_as_of": date(2026, 6, 30),
        "latest_insider_purchase_at": date(2026, 6, 1),
    }
    values.update(changes)
    return PeterLynchSnapshot(**values)  # type: ignore[arg-type]


def criteria(evaluation: object) -> dict[CriterionName, object]:
    return {item.name: item for item in evaluation.criteria}  # type: ignore[attr-defined]


def test_candidate_requires_all_six_financial_criteria() -> None:
    result = PeterLynchEngine().evaluate(snapshot())

    assert result.eligible is True
    assert result.passed_count == 6
    assert result.required_count == 6
    assert result.category is LynchCategory.FAST_GROWER
    assert result.metrics.trailing_pe == Decimal("10")
    assert result.metrics.debt_to_equity_percent == Decimal("20")
    assert result.metrics.market_cap == Decimal("6000000000")
    assert result.metrics.eps_cagr_percent is not None
    assert result.metrics.projected_forward_pe is not None
    assert all(item.passed for item in result.criteria if item.required)
    assert criteria(result)[CriterionName.INSIDER_BUYING].required is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("changes", "criterion"),
    [
        ({"price": Decimal("50")}, CriterionName.TRAILING_PE),
        ({"price": Decimal("38")}, CriterionName.PROJECTED_FORWARD_PE),
        ({"debt": Decimal("35")}, CriterionName.DEBT_TO_EQUITY),
        (
            {
                "annual_eps": (
                    AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                    AnnualEps(2023, date(2023, 12, 31), Decimal("1.03")),
                    AnnualEps(2024, date(2024, 12, 31), Decimal("1.08")),
                    AnnualEps(2025, date(2025, 12, 31), Decimal("1.15")),
                )
            },
            CriterionName.EPS_GROWTH,
        ),
        (
            {
                "price": Decimal("44"),
                "annual_eps": (
                    AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                    AnnualEps(2023, date(2023, 12, 31), Decimal("1.18")),
                    AnnualEps(2024, date(2024, 12, 31), Decimal("1.39")),
                    AnnualEps(2025, date(2025, 12, 31), Decimal("1.64")),
                ),
            },
            CriterionName.PEG,
        ),
        ({"shares_outstanding": Decimal("250000000")}, CriterionName.MARKET_CAP),
    ],
)
def test_each_threshold_can_reject_candidate(
    changes: dict[str, object], criterion: CriterionName
) -> None:
    result = PeterLynchEngine().evaluate(snapshot(**changes))

    assert result.eligible is False
    assert criteria(result)[criterion].passed is False  # type: ignore[attr-defined]


def test_insider_buying_is_informational_and_does_not_reject_candidate() -> None:
    result = PeterLynchEngine().evaluate(
        snapshot(
            insider_open_market_purchase_count=0,
            latest_insider_purchase_at=None,
        )
    )

    insider = criteria(result)[CriterionName.INSIDER_BUYING]
    assert result.eligible is True
    assert result.passed_count == result.required_count == 6
    assert insider.passed is False  # type: ignore[attr-defined]
    assert insider.required is False  # type: ignore[attr-defined]
    assert CriterionName.INSIDER_BUYING not in result.failed_criteria


def test_missing_and_non_positive_values_fail_closed() -> None:
    result = PeterLynchEngine().evaluate(
        snapshot(
            ttm_eps=Decimal("0"),
            equity=Decimal("0"),
            shares_outstanding=None,
            annual_eps=(),
            insider_open_market_purchase_count=None,
        )
    )

    assert result.eligible is False
    assert result.metrics.trailing_pe is None
    assert result.metrics.eps_cagr_percent is None
    assert result.metrics.debt_to_equity_percent is None
    assert result.metrics.market_cap is None
    assert result.failed_criteria == tuple(
        item.name for item in result.criteria if item.required
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"ttm_eps": Decimal("1"), "prior_ttm_eps": Decimal("-1")}, LynchCategory.TURNAROUND),
        (
            {
                "price": Decimal("2"),
                "shares_outstanding": Decimal("100"),
                "equity": Decimal("1000"),
                "goodwill": Decimal("100"),
                "intangibles_ex_goodwill": Decimal("100"),
            },
            LynchCategory.ASSET_PLAY,
        ),
        ({"sic": 3711}, LynchCategory.CYCLICAL),
        (
            {
                "annual_eps": (
                    AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                    AnnualEps(2023, date(2023, 12, 31), Decimal("1.02")),
                    AnnualEps(2024, date(2024, 12, 31), Decimal("1.04")),
                    AnnualEps(2025, date(2025, 12, 31), Decimal("1.08")),
                )
            },
            LynchCategory.SLOW_GROWER,
        ),
        (
            {
                "annual_eps": (
                    AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                    AnnualEps(2023, date(2023, 12, 31), Decimal("1.11")),
                    AnnualEps(2024, date(2024, 12, 31), Decimal("1.23")),
                    AnnualEps(2025, date(2025, 12, 31), Decimal("1.37")),
                )
            },
            LynchCategory.STALWART,
        ),
        (
            {
                "annual_eps": (
                    AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                    AnnualEps(2023, date(2023, 12, 31), Decimal("1.22")),
                    AnnualEps(2024, date(2024, 12, 31), Decimal("1.49")),
                    AnnualEps(2025, date(2025, 12, 31), Decimal("1.82")),
                )
            },
            LynchCategory.FAST_GROWER,
        ),
    ],
)
def test_classification_heuristics_and_precedence(
    changes: dict[str, object], expected: LynchCategory
) -> None:
    assert PeterLynchEngine().evaluate(snapshot(**changes)).category is expected


def test_cagr_requires_four_consecutive_positive_fiscal_years() -> None:
    result = PeterLynchEngine().evaluate(
        snapshot(
            annual_eps=(
                AnnualEps(2021, date(2021, 12, 31), Decimal("1")),
                AnnualEps(2023, date(2023, 12, 31), Decimal("1.3")),
                AnnualEps(2024, date(2024, 12, 31), Decimal("1.6")),
                AnnualEps(2025, date(2025, 12, 31), Decimal("2")),
            )
        )
    )

    assert result.metrics.eps_cagr_percent is None
    assert criteria(result)[CriterionName.EPS_GROWTH].passed is False  # type: ignore[attr-defined]
