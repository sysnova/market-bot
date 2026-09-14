from dataclasses import replace
from decimal import Decimal

import pytest

from app.contracts import SwingTradeMaturity
from app.swing_trade_engine.models import SwingTradeContext
from app.swing_trade_engine.tests.test_engine import daily_bars
from app.swing_trade_engine.tests.test_v17 import append_bar, context_at, values
from app.swing_trade_engine.tests.test_v18 import bullish_bar
from app.swing_trade_engine.v18 import SwingTradeEngineV18
from app.swing_trade_engine.v19 import SwingTradeEngineV19

Candidate = SwingTradeEngineV19
ENTERED = {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def recovered_support(count: int = 3) -> SwingTradeContext:
    ctx = replace(context_at(6), daily_bars=daily_bars(support=Decimal("90")))
    for close, low in (("101.5", "101.0"), ("101.7", "101.4"), ("101.9", "101.1"))[:count]:
        ctx = bullish_bar(ctx, close, low)
    return ctx


def test_confirmed_local_support_can_replace_distant_daily_support() -> None:
    ctx = recovered_support()
    old = SwingTradeEngineV18().analyze(ctx)
    assert not old.support_confluence
    assert old.maturity not in ENTERED
    result = Candidate().analyze(ctx)
    assert result.maturity == SwingTradeMaturity.ST3
    assert not result.support_confluence
    assert values(result)["rebound_support_source"] == "LOCAL_RETEST"
    assert values(result)["rebound_acceptance"] == "RETEST"
    assert values(result)["rebound_reference"] == Decimal("101")
    assert values(result)["operational_stop"] < Decimal("98.5")
    assert result.primary_target == old.primary_target


@pytest.mark.parametrize("count", [0, 1, 2])
def test_breakout_or_short_retest_does_not_replace_support(count: int) -> None:
    assert Candidate().analyze(recovered_support(count)).maturity not in ENTERED


def test_hour_without_retest_cannot_replace_support() -> None:
    ctx = recovered_support(0)
    for _ in range(6):
        ctx = append_bar(ctx, "102", high="102.2", low="101.8")
    assert Candidate().analyze(ctx).maturity not in ENTERED


@pytest.mark.parametrize("failure", ["vwap", "volume", "risk", "lower_low", "flat_close"])
def test_local_support_keeps_confirmation_and_risk_gates(failure: str) -> None:
    ctx = recovered_support()
    bars = list(ctx.confirmation_bars)
    kwargs: dict[str, object] = {}
    if failure == "vwap":
        bars = [b.model_copy(update={"vwap": Decimal("110")}) for b in bars]
    elif failure == "volume":
        bars = [b.model_copy(update={"volume": Decimal("1000")}) for b in bars]
    elif failure == "risk":
        kwargs["maximum_entry_risk_percent"] = Decimal("1")
    elif failure == "lower_low":
        bars[-3] = bars[-3].model_copy(update={"low": Decimal("98.4")})
    else:
        bars[-2] = bars[-2].model_copy(update={"close": bars[-3].close})
    result = Candidate(**kwargs).analyze(replace(ctx, confirmation_bars=tuple(bars)))
    assert result.maturity not in ENTERED


def test_native_support_preserves_existing_entry() -> None:
    ctx = replace(recovered_support(), daily_bars=daily_bars())
    old = SwingTradeEngineV18().analyze(ctx)
    result = Candidate().analyze(ctx)
    assert result.maturity == old.maturity == SwingTradeMaturity.ST3
    assert values(result)["operational_stop"] == values(old)["operational_stop"]


def test_recovered_support_position_exits_on_acceptance_failure() -> None:
    ctx = recovered_support()
    engine = Candidate()
    result = engine.analyze(ctx)
    assert result.maturity in ENTERED
    for close in ("100.8", "100.7"):
        ctx = append_bar(ctx, close)
        result = engine.analyze(replace(ctx, previous_assessment=result))
    assert "rebound_acceptance_failed" in result.reasons


def test_upgrade_preserves_old_open_stop() -> None:
    ctx = replace(recovered_support(), daily_bars=daily_bars())
    previous = SwingTradeEngineV18().analyze(ctx)
    ctx = append_bar(ctx, "96", high="97", low="95")
    result = Candidate().analyze(replace(ctx, previous_assessment=previous))
    assert "rebound_stop_breached" in result.reasons
    assert values(result)["operational_stop"] == values(previous)["operational_stop"]


def test_early_retest_followed_by_rising_closes_can_confirm() -> None:
    ctx = recovered_support()
    last = ctx.confirmation_bars[-1].model_copy(update={"low": Decimal("101.6")})
    result = Candidate().analyze(
        replace(ctx, confirmation_bars=(*ctx.confirmation_bars[:-1], last))
    )
    assert result.maturity == SwingTradeMaturity.ST3
    assert values(result)["rebound_support_source"] == "LOCAL_RETEST"


def test_local_support_still_requires_reward_risk() -> None:
    result = Candidate(minimum_reward_risk=Decimal("100")).analyze(recovered_support())
    assert result.maturity not in ENTERED


def test_bootstrap_cannot_open_recovered_support_trade() -> None:
    result = Candidate().analyze(replace(recovered_support(), allow_new_entry=False))
    assert result.maturity not in ENTERED
    assert values(result)["rebound_state"] == "ACCEPTED_NOT_ACTIONABLE"


def test_rising_closes_without_retest_do_not_replace_support() -> None:
    ctx = recovered_support()
    bars = list(ctx.confirmation_bars)
    for index in range(-3, 0):
        bars[index] = bars[index].model_copy(update={"low": bars[index].close - Decimal("0.05")})
    assert Candidate().analyze(replace(ctx, confirmation_bars=tuple(bars))).maturity not in ENTERED
