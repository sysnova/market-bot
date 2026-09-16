from dataclasses import replace
from decimal import Decimal

from app.contracts import SwingTradeMaturity
from app.swing_trade_engine.models import SwingTradeContext
from app.swing_trade_engine.tests.test_v17 import append_bar, context_at, values
from app.swing_trade_engine.tests.test_v18 import bullish_bar
from app.swing_trade_engine.v111 import SwingTradeEngineV111
from app.swing_trade_engine.v112 import SwingTradeEngineV112

ENTERED = {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def damaged_daily_structure(ctx: SwingTradeContext) -> SwingTradeContext:
    bars = list(ctx.daily_bars)
    for index in range(len(bars) - 20, len(bars)):
        bars[index] = bars[index].model_copy(
            update={"open": Decimal("103.2"), "close": Decimal("103")}
        )
    return replace(ctx, daily_bars=tuple(bars))


def recovering_daily_structure(ctx: SwingTradeContext) -> SwingTradeContext:
    bars = list(ctx.daily_bars)
    for index in range(len(bars) - 20, len(bars)):
        bars[index] = bars[index].model_copy(
            update={"open": Decimal("109.8"), "close": Decimal("110")}
        )
    return replace(ctx, daily_bars=tuple(bars))


def flat_hour(ctx: SwingTradeContext) -> SwingTradeContext:
    for _ in range(6):
        ctx = append_bar(ctx, "102", high="102.2", low="101.8")
    return ctx


def test_damaged_daily_structure_rejects_simple_hour_rebound() -> None:
    ctx = flat_hour(damaged_daily_structure(context_at(6)))

    previous = SwingTradeEngineV111().analyze(ctx)
    assert previous.maturity == SwingTradeMaturity.ST3
    assert values(previous)["rebound_acceptance"] == "1H_CLOSE"

    result = SwingTradeEngineV112().analyze(ctx)
    metrics = values(result)
    assert result.maturity not in ENTERED
    assert metrics["rebound_daily_structure_damaged"] is True
    assert metrics["rebound_confirmation_stages_required"] == 2
    assert metrics["rebound_secondary_closes_required"] == 3
    assert "rebound_damaged_daily_structure_retest_pending" in result.reasons


def test_damaged_daily_structure_enters_after_three_post_breakout_rising_closes() -> None:
    ctx = damaged_daily_structure(context_at(6))
    for close, low in (("101.5", "101.0"), ("101.7", "101.4"), ("101.9", "101.1")):
        ctx = bullish_bar(ctx, close, low)

    result = SwingTradeEngineV112().analyze(ctx)
    metrics = values(result)
    assert result.maturity == SwingTradeMaturity.ST3
    assert metrics["rebound_acceptance"] == "RETEST"
    assert metrics["rebound_confirmation_stages_passed"] == 2


def test_breakout_candle_does_not_count_as_a_secondary_confirmation() -> None:
    ctx = damaged_daily_structure(context_at(6))
    for close, low in (("101.7", "101.0"), ("101.9", "101.4")):
        ctx = bullish_bar(ctx, close, low)

    result = SwingTradeEngineV112().analyze(ctx)
    assert result.maturity not in ENTERED


def test_recovering_daily_structure_keeps_existing_hour_acceptance() -> None:
    result = SwingTradeEngineV112().analyze(flat_hour(recovering_daily_structure(context_at(6))))
    metrics = values(result)
    assert result.maturity == SwingTradeMaturity.ST3
    assert metrics["rebound_acceptance"] == "1H_CLOSE"
    assert metrics["rebound_daily_structure_damaged"] is False
