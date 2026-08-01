from decimal import Decimal
from pathlib import Path

from app.long_portfolio_engine import PortfolioAllocation, load_long_portfolio_policy


def test_production_artifact_combines_rules_with_database_allocations() -> None:
    root = Path(__file__).parents[3]
    policy = load_long_portfolio_policy(
        root / "configs/rules/long_portfolio/1.0.0.yaml",
        allocations=(PortfolioAllocation(symbol="HIMS", weight_percent=Decimal("75.73")),),
    )

    assert policy.portfolio_capital_usd == Decimal("103000")
    assert policy.cash_weight_percent == Decimal("11.20")
    assert policy.reserved_weight_percent == Decimal("13.07")
    assert policy.allocation_for("HIMS").weight_percent == Decimal("75.73")  # type: ignore[union-attr]
    assert policy.unallocated_weight_percent == Decimal("0")
