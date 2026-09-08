"""Independent bearish breakdown assessment. Never emits countertrend or broker orders."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.contracts import NamedValue, TradeSide

from .models import Swing4HGeriContext
from .tactical_levels import (
    NY,
    directional_rr,
    directional_target,
    obstacles,
    pivots,
    to_metrics,
    validate_context,
)


@dataclass(frozen=True)
class ShortRules:
    minimum_rvol: Decimal = Decimal("1.5")
    minimum_rr: Decimal = Decimal("1.5")
    rvol_sessions: int = 5


def short_metrics(context: Swing4HGeriContext, rules: ShortRules) -> tuple[NamedValue, ...]:
    validate_context(context)
    daily, bars, fast = context.daily_bars, context.bars, context.confirmation_bars
    metrics: dict[str, object] = {
        "short_side": TradeSide.SHORT,
        "short_eligible": False,
        "short_places_orders": False,
        "short_setup_kind": "STRUCTURAL_BREAKDOWN",
    }
    reasons: list[str] = []
    if len(daily) < 50:
        return to_metrics(
            {
                **metrics,
                "short_state": "UNAVAILABLE",
                "short_reasons": ("completed_daily_history_missing",),
            }
        )
    closes = tuple(b.close for b in daily)
    ema = sum(closes[:8], Decimal(0)) / 8
    for close in closes[8:]:
        ema += Decimal(2) / 9 * (close - ema)
    sma21, sma50 = sum(closes[-21:], Decimal(0)) / 21, sum(closes[-50:], Decimal(0)) / 50
    price = context.current_price
    if not price < min(ema, sma21, sma50):
        reasons.append("daily_averages_not_broken")
    lows = pivots(daily, low=True)
    broken = next(
        (i for i in reversed(lows) if daily[-1].close < daily[i].low and price < daily[i].low), None
    )
    if broken is None:
        reasons.append("daily_support_not_broken")
    highs = pivots(bars, low=False)
    anchor = highs[-1] if highs else None
    avwap: Decimal | None = None
    stop: Decimal | None = None
    if anchor is not None:
        anchored = bars[anchor:]
        volume = sum((b.volume for b in anchored), Decimal(0))
        if volume > 0:
            avwap = (
                sum(
                    ((b.vwap or (b.high + b.low + b.close) / 3) * b.volume for b in anchored),
                    Decimal(0),
                )
                / volume
            )
        stop = bars[anchor].high
    if avwap is None:
        reasons.append("pivot_vwap_unavailable")
    elif price >= avwap:
        reasons.append("pivot_vwap_not_broken")
    rvol: Decimal | None = None
    if fast:
        latest = fast[-1]
        local = latest.timestamp.astimezone(NY)
        baseline = tuple(
            b.volume
            for b in fast[:-1]
            if b.timestamp.astimezone(NY).date() < local.date()
            and b.timestamp.astimezone(NY).time() == local.time()
        )[-rules.rvol_sessions :]
        if len(baseline) == rules.rvol_sessions and sum(baseline, Decimal(0)) > 0:
            rvol = latest.volume / (sum(baseline, Decimal(0)) / len(baseline))
    if rvol is None:
        reasons.append("session_normalized_rvol_unavailable")
    elif rvol < rules.minimum_rvol:
        reasons.append("bearish_rvol_insufficient")
    bearish = (
        len(fast) >= 2
        and fast[-1].timestamp - fast[-2].timestamp == timedelta(minutes=15)
        and fast[-1].close < fast[-1].open
        and fast[-1].close < fast[-2].low
        and context.current_price_at is not None
        and context.current_price_at - fast[-1].timestamp <= timedelta(minutes=30)
        and price <= fast[-1].close
    )
    if not bearish:
        reasons.append("bearish_price_confirmation_missing")
    target = directional_target(
        TradeSide.SHORT,
        price,
        (*obstacles(bars, TradeSide.SHORT), *obstacles(daily, TradeSide.SHORT)),
    )
    rr = directional_rr(TradeSide.SHORT, price, stop, target) if stop is not None else None
    if target is None:
        reasons.append("NO_VALID_DIRECTIONAL_TARGET")
    if rr is None:
        reasons.append("target_or_invalidation_order_failed")
    elif rr <= rules.minimum_rr:
        reasons.append("insufficient_reward_risk")
    return to_metrics(
        {
            **metrics,
            "short_eligible": not reasons,
            "short_state": "CONFIRMED" if not reasons else "INELIGIBLE",
            "short_reasons": tuple(reasons) or ("structural_breakdown_confirmed",),
            "short_ema8_daily": ema,
            "short_sma21_daily": sma21,
            "short_sma50_daily": sma50,
            "short_broken_daily_support": daily[broken].low if broken is not None else None,
            "short_pivot_vwap": avwap,
            "short_pivot_at": bars[anchor].timestamp if anchor is not None else None,
            "short_rvol": rvol,
            "short_minimum_rvol": rules.minimum_rvol,
            "short_target": target,
            "short_invalidation": stop,
            "short_reward_risk": rr,
        }
    )
