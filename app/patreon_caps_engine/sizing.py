"""Risk- and allocation-bounded staged sizing for PatreonCaps."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.contracts import MacroRegime

from .models import TrancheSizing


def size_portfolio_tranche(
    *,
    portfolio_capital_usd: Decimal,
    target_weight_percent: Decimal | None,
    held_quantity: Decimal,
    entry_price: Decimal,
    invalidation: Decimal,
    stage: int,
    macro_regime: MacroRegime,
) -> TrancheSizing | None:
    if target_weight_percent is None or not 1 <= stage <= 5:
        return None
    risk_per_share = entry_price - invalidation
    if risk_per_share <= 0 or macro_regime in {MacroRegime.SHOCK, MacroRegime.UNKNOWN}:
        return None
    multiplier = Decimal("0.5") if macro_regime is MacroRegime.RISK_OFF else Decimal("1")
    risk_budget = _money(portfolio_capital_usd * Decimal("0.01") * multiplier)
    target_capital = _money(portfolio_capital_usd * target_weight_percent / Decimal("100"))
    current_value = _money(held_quantity * entry_price)
    remaining = max(Decimal(), target_capital - current_value)
    allowed_cumulative = target_capital * Decimal(stage) * Decimal("0.20")
    stage_remaining = max(Decimal(), allowed_cumulative - current_value)
    stage_capital = min(target_capital * Decimal("0.20") * multiplier, stage_remaining)
    max_risk_shares = (risk_budget / risk_per_share).quantize(Decimal("1"), rounding=ROUND_DOWN)
    available_risk_shares = max(Decimal(), max_risk_shares - held_quantity)
    shares = min(
        (stage_capital / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN),
        (remaining / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN),
        available_risk_shares,
    )
    if shares <= 0:
        return None
    return TrancheSizing(
        stage=stage,
        risk_budget_usd=risk_budget,
        target_capital_usd=target_capital,
        remaining_target_usd=_money(remaining),
        suggested_tranche_usd=_money(shares * entry_price),
        suggested_whole_shares=shares,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
