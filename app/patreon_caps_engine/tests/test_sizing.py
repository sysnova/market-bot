from decimal import Decimal

from app.contracts import MacroRegime
from app.patreon_caps_engine.sizing import size_portfolio_tranche


def test_sizing_is_capped_by_target_and_structural_risk() -> None:
    result = size_portfolio_tranche(
        portfolio_capital_usd=Decimal("103000"),
        target_weight_percent=Decimal("8.84"),
        held_quantity=Decimal("0"),
        entry_price=Decimal("100"),
        invalidation=Decimal("95"),
        stage=1,
        macro_regime=MacroRegime.RISK_ON,
    )

    assert result is not None
    assert result.risk_budget_usd == Decimal("1030.00")
    assert result.suggested_whole_shares == Decimal("18")
    assert result.suggested_tranche_usd == Decimal("1800.00")


def test_risk_off_halves_budget_and_non_portfolio_has_no_sizing() -> None:
    risk_off = size_portfolio_tranche(
        portfolio_capital_usd=Decimal("103000"),
        target_weight_percent=Decimal("10"),
        held_quantity=Decimal("0"),
        entry_price=Decimal("50"),
        invalidation=Decimal("45"),
        stage=1,
        macro_regime=MacroRegime.RISK_OFF,
    )

    assert risk_off is not None
    assert risk_off.risk_budget_usd == Decimal("515.00")
    assert size_portfolio_tranche(
        portfolio_capital_usd=Decimal("103000"),
        target_weight_percent=None,
        held_quantity=Decimal("0"),
        entry_price=Decimal("50"),
        invalidation=Decimal("45"),
        stage=1,
        macro_regime=MacroRegime.RISK_ON,
    ) is None
