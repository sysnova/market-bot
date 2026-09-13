from dataclasses import replace
from decimal import Decimal

import pytest

from app.contracts import SwingTradeMaturity
from app.swing_trade_engine.models import SwingTradeContext
from app.swing_trade_engine.tests.test_v17 import append_bar, context_at, values
from app.swing_trade_engine.v17 import SwingTradeEngineV17
from app.swing_trade_engine.v18 import SwingTradeEngineV18

Candidate = SwingTradeEngineV18
ENTERED = {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def falling_hour() -> SwingTradeContext:
    ctx = context_at(6)
    for close in ("102.8", "102.7", "102.6", "102.4", "102.2", "102.0"):
        ctx = append_bar(
            ctx,
            close,
            high=str(Decimal(close) + Decimal("0.1")),
            low=str(Decimal(close) - Decimal("0.1")),
        )
    return ctx


def bullish_bar(ctx: SwingTradeContext, close: str, low: str) -> SwingTradeContext:
    ctx = append_bar(ctx, close, high=str(Decimal(close) + Decimal("0.1")), low=low)
    last = ctx.confirmation_bars[-1].model_copy(update={"open": Decimal(close) - Decimal("0.05")})
    return replace(ctx, confirmation_bars=(*ctx.confirmation_bars[:-1], last))


def test_falling_hour_above_vwap_waits_for_bullish_retest() -> None:
    ctx = falling_hour()
    old = SwingTradeEngineV17().analyze(ctx)
    assert old.maturity == SwingTradeMaturity.ST3
    assert values(old)["rebound_acceptance"] == "1H_CLOSE"
    assert Decimal(str(values(old)["session_vwap"])) < ctx.current_price
    result = Candidate().analyze(ctx)
    assert result.maturity not in ENTERED
    assert "rebound_bullish_retest_pending" in result.reasons


def test_pullback_requires_three_rising_closes_and_a_recent_retest() -> None:
    ctx = falling_hour()
    engine = Candidate(rebound_expiry_bars=16)
    for close, low in (("101.15", "101.05"), ("101.3", "101.1")):
        ctx = bullish_bar(ctx, close, low)
        assert engine.analyze(ctx).maturity not in ENTERED
    ctx = bullish_bar(ctx, "101.5", "101.3")
    result = engine.analyze(ctx)
    assert result.maturity == SwingTradeMaturity.ST3
    assert values(result)["rebound_acceptance"] == "RETEST"


def test_rising_hour_after_pullback_cannot_bypass_missing_retest() -> None:
    ctx = falling_hour()
    for close in ("102.1", "102.2", "102.3", "102.4"):
        ctx = bullish_bar(ctx, close, str(Decimal(close) - Decimal("0.05")))
    assert SwingTradeEngineV17(rebound_expiry_bars=16).analyze(ctx).maturity in ENTERED
    result = Candidate(rebound_expiry_bars=16).analyze(ctx)
    assert result.maturity not in ENTERED


def test_flat_hour_without_pullback_keeps_hour_acceptance() -> None:
    ctx = context_at(6)
    for _ in range(6):
        ctx = append_bar(ctx, "102", high="102.2", low="101.8")
    result = Candidate().analyze(ctx)
    assert result.maturity == SwingTradeMaturity.ST3
    assert values(result)["rebound_acceptance"] == "1H_CLOSE"


@pytest.mark.parametrize("last_close,last_open", [("101.3", "101.2"), ("101.5", "101.6")])
def test_equal_closes_or_bearish_final_candle_do_not_confirm(
    last_close: str,
    last_open: str,
) -> None:
    ctx = falling_hour()
    for close, low in (("101.15", "101.05"), ("101.3", "101.1"), (last_close, "101.2")):
        ctx = bullish_bar(ctx, close, low)
    last = ctx.confirmation_bars[-1].model_copy(update={"open": Decimal(last_open)})
    ctx = replace(ctx, confirmation_bars=(*ctx.confirmation_bars[:-1], last))
    assert Candidate(rebound_expiry_bars=16).analyze(ctx).maturity not in ENTERED


def test_confirmed_retest_still_requires_session_vwap() -> None:
    ctx = falling_hour()
    for close, low in (("101.15", "101.05"), ("101.3", "101.1"), ("101.5", "101.3")):
        ctx = bullish_bar(ctx, close, low)
    bars = tuple(b.model_copy(update={"vwap": Decimal("102")}) for b in ctx.confirmation_bars)
    result = Candidate(rebound_expiry_bars=16).analyze(replace(ctx, confirmation_bars=bars))
    assert result.maturity not in ENTERED
    assert "rebound_session_vwap_pending" in result.reasons


def test_upgrade_keeps_existing_stop_and_closed_candidate_cutoff() -> None:
    ctx = context_at(7)
    previous = SwingTradeEngineV17().analyze(ctx)
    ctx = append_bar(ctx, "96", high="97", low="95")
    exited = Candidate().analyze(replace(ctx, previous_assessment=previous))
    assert "rebound_stop_breached" in exited.reasons
    assert values(exited)["operational_stop"] == values(previous)["operational_stop"]
    legacy_exit = exited.model_copy(update={"engine_version": "1.7.0"})
    ctx = append_bar(ctx, "101.3", high="101.5", low="101.1")
    renewed = Candidate().analyze(replace(ctx, previous_assessment=legacy_exit))
    assert renewed.maturity not in ENTERED


def test_rising_closes_requires_multiple_bars() -> None:
    with pytest.raises(ValueError, match="at least two"):
        Candidate(rebound_rising_closes=1)
