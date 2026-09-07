from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.contracts import BarTimeframe, MarketBar, SwingTradeAssessment, SwingTradeMaturity
from app.swing_trade_engine.models import SwingTradeContext
from app.swing_trade_engine.tests.test_engine import daily_bars
from app.swing_trade_engine.v17 import SwingTradeEngineV17


def context_at(count: int = 8) -> SwingTradeContext:
    daily = daily_bars()
    start = (daily[-1].timestamp + timedelta(days=1)).replace(hour=13, minute=30)
    template = daily[-1].model_copy(update={"timeframe": BarTimeframe.MINUTE_15})
    rows = [
        ("100", "101", "99.8", "100"),
        ("100", "101", "99.7", "100"),
        ("100", "101", "99.6", "100"),
        ("100", "101", "99.5", "100"),
        ("99.8", "100", "98.5", "99"),
        ("99", "101.6", "98.8", "101.4"),
        ("101.1", "101.5", "101", "101.3"),
        ("101.2", "102", "101.1", "101.8"),
    ]
    bars: list[MarketBar] = []
    for day in range(6, 0, -1):
        for i in range(26):
            bars.append(
                template.model_copy(
                    update={
                        "timestamp": start - timedelta(days=day) + timedelta(minutes=15 * i),
                        "volume": Decimal("1000"),
                        "vwap": Decimal("100"),
                    }
                )
            )
    for i, row in enumerate(rows[:count]):
        o, h, low, c = map(Decimal, row)
        bars.append(
            template.model_copy(
                update={
                    "timestamp": start + timedelta(minutes=15 * i),
                    "open": o,
                    "high": h,
                    "low": low,
                    "close": c,
                    "volume": Decimal("2000"),
                    "vwap": (o + c) / 2,
                }
            )
        )
    at = bars[-1].timestamp + timedelta(minutes=15)
    return SwingTradeContext(
        symbol="AAPL",
        as_of=at,
        current_price=bars[-1].close,
        current_price_at=at,
        daily_bars=daily,
        confirmation_bars=tuple(bars),
    )


def values(result: SwingTradeAssessment) -> dict[str, Any]:
    return {m.name: m.value for m in result.metrics}


def test_reaction_and_breakout_alone_do_not_enter() -> None:
    result = SwingTradeEngineV17().analyze(context_at(6))
    assert result.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}
    assert values(result)["rebound_state"] == "BREAKOUT"


def test_retest_enters_outside_fibonacci_without_macd() -> None:
    result = SwingTradeEngineV17().analyze(context_at(7))
    m = values(result)
    assert result.maturity == SwingTradeMaturity.ST3
    assert not result.spot_in_fibonacci_zone
    assert m["macd_4h_status"] == "INSUFFICIENT_HISTORY"
    assert result.invalidation < m["operational_stop"] < result.current_price
    assert m["entry_risk_percent"] <= Decimal("4")


def test_risk_cap_blocks_without_clipping_stop() -> None:
    result = SwingTradeEngineV17(maximum_entry_risk_percent=Decimal("1")).analyze(context_at(7))
    assert result.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}
    assert "rebound_risk_limit_exceeded" in result.reasons


def test_stop_survives_serialization_and_loss_closes() -> None:
    engine = SwingTradeEngineV17()
    entered = engine.analyze(context_at(7))
    entered = type(entered).model_validate_json(entered.model_dump_json())
    ctx = context_at(8)
    stop = Decimal(str(values(entered)["operational_stop"]))
    last = ctx.confirmation_bars[-1].model_copy(
        update={"low": stop - 1, "close": stop - Decimal("0.2")}
    )
    ctx = replace(
        ctx,
        current_price=last.close,
        confirmation_bars=(*ctx.confirmation_bars[:-1], last),
        previous_assessment=entered,
    )
    result = engine.analyze(ctx)
    assert result.maturity is None
    assert "swing_trade_rebound_exit" in result.reasons
    assert values(result)["operational_stop"] == stop


def test_open_stop_never_widens_and_missing_macd_does_not_exit() -> None:
    engine = SwingTradeEngineV17()
    entered = engine.analyze(context_at(7))
    result = engine.analyze(replace(context_at(8), previous_assessment=entered))
    assert result.maturity == entered.maturity
    assert values(result)["operational_stop"] == values(entered)["operational_stop"]


def test_gap_between_touch_and_breakout_cannot_confirm() -> None:
    ctx = context_at(7)
    bars = tuple(
        b.model_copy(update={"timestamp": b.timestamp + timedelta(minutes=15)})
        if i >= len(ctx.confirmation_bars) - 2
        else b
        for i, b in enumerate(ctx.confirmation_bars)
    )
    ctx = replace(
        ctx,
        confirmation_bars=bars,
        as_of=ctx.as_of + timedelta(minutes=15),
        current_price_at=ctx.current_price_at + timedelta(minutes=15),
    )
    result = SwingTradeEngineV17().analyze(ctx)
    assert result.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def append_bar(
    ctx: SwingTradeContext, close: str, *, high: str = "101.5", low: str = "100.5"
) -> SwingTradeContext:
    last = ctx.confirmation_bars[-1]
    bar = last.model_copy(
        update={
            "timestamp": last.timestamp + timedelta(minutes=15),
            "open": Decimal(close),
            "close": Decimal(close),
            "high": Decimal(high),
            "low": Decimal(low),
        }
    )
    at = bar.timestamp + timedelta(minutes=15)
    return replace(
        ctx,
        as_of=at,
        current_price_at=at,
        current_price=bar.close,
        confirmation_bars=(*ctx.confirmation_bars, bar),
    )


def test_two_failed_closes_exit_above_operational_stop() -> None:
    engine = SwingTradeEngineV17()
    ctx = context_at(7)
    opened = engine.analyze(ctx)
    ctx = append_bar(ctx, "100.8")
    first = engine.analyze(replace(ctx, previous_assessment=opened))
    assert first.maturity == SwingTradeMaturity.ST3
    ctx = append_bar(ctx, "100.7")
    exited = engine.analyze(replace(ctx, previous_assessment=first))
    assert exited.maturity is None
    assert exited.current_price > Decimal(str(values(exited)["operational_stop"]))
    assert "rebound_acceptance_failed" in exited.reasons


def test_no_progress_exit_and_no_immediate_reentry() -> None:
    engine = SwingTradeEngineV17(no_progress_bars=2)
    ctx = context_at(7)
    result = engine.analyze(ctx)
    for _ in range(2):
        ctx = append_bar(ctx, "101.2", high="101.4", low="101.1")
        result = engine.analyze(replace(ctx, previous_assessment=result))
    assert "rebound_no_progress" in result.reasons
    ctx = append_bar(ctx, "101.3", high="101.5", low="101.1")
    renewed = engine.analyze(replace(ctx, previous_assessment=result))
    assert renewed.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def test_incomplete_and_stale_macd_are_only_observations() -> None:
    ctx = context_at(7)
    engine = SwingTradeEngineV17()
    expected = engine.analyze(ctx)
    for final, age in [(False, 0), (True, 10)]:
        bars = tuple(
            ctx.daily_bars[-1].model_copy(
                update={
                    "timeframe": BarTimeframe.HOUR_4,
                    "timestamp": ctx.daily_bars[-1].timestamp.replace(hour=13, minute=30)
                    - timedelta(days=age + 40 - i),
                    "is_final": final,
                }
            )
            for i in range(40)
        )
        observed = engine.analyze(replace(ctx, four_hour_bars=bars))
        assert observed.maturity == expected.maturity
        assert values(observed)["operational_stop"] == values(expected)["operational_stop"]
        assert values(observed)["macd_4h_status"] in {"STALE", "INSUFFICIENT_HISTORY"}


def test_hour_acceptance_without_retest() -> None:
    ctx = context_at(6)
    for _ in range(6):
        ctx = append_bar(ctx, "102", high="102.2", low="101.8")
    result = SwingTradeEngineV17().analyze(ctx)
    assert result.maturity == SwingTradeMaturity.ST3
    assert values(result)["rebound_acceptance"] == "1H_CLOSE"


def test_wide_stop_and_missing_volume_cannot_enter() -> None:
    ctx = context_at(7)
    bars = list(ctx.confirmation_bars)
    bars[-3] = bars[-3].model_copy(update={"low": Decimal("94")})
    result = SwingTradeEngineV17().analyze(replace(ctx, confirmation_bars=tuple(bars)))
    assert "rebound_risk_limit_exceeded" in result.reasons
    missing = SwingTradeEngineV17().analyze(
        replace(ctx, confirmation_bars=ctx.confirmation_bars[-7:])
    )
    assert missing.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def test_stale_bootstrap_does_not_create_phantom_open_position() -> None:
    engine = SwingTradeEngineV17()
    result = engine.analyze(replace(context_at(7), allow_new_entry=False))
    assert values(result)["rebound_state"] == "ACCEPTED_NOT_ACTIONABLE"
    assert result.maturity not in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def test_broken_daily_impulse_does_not_disable_existing_stop() -> None:
    ctx = context_at(7)
    engine = SwingTradeEngineV17()
    previous = engine.analyze(ctx)
    ctx = append_bar(ctx, "96", high="97", low="95")
    daily = (*ctx.daily_bars[:-1], ctx.daily_bars[-1].model_copy(update={"low": Decimal("70")}))
    result = engine.analyze(replace(ctx, daily_bars=daily, previous_assessment=previous))
    assert "rebound_stop_breached" in result.reasons
