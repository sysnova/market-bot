"""LONG-only recovery maturity; independent of GERI's pinned structural direction."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import GeriLevelKind, GeriMaturity, MarketBar, NamedValue, TradeSide

from .engine import _duration_normalized_atr  # pyright: ignore[reportPrivateUsage]
from .models import Swing4HGeriContext
from .tactical_levels import (
    NY,
    completed_at,
    directional_rr,
    directional_target,
    obstacles,
    pivots,
    to_metrics,
    validate_context,
)


@dataclass(frozen=True)
class RecoveryRules:
    minimum_rr: Decimal = Decimal("1.5")
    ttl_sessions: int = 5
    breakout_atr: Decimal = Decimal("0.10")
    stop_atr: Decimal = Decimal("0.10")
    zone_atr: Decimal = Decimal("0.25")
    maximum_extension_atr: Decimal = Decimal("1.5")
    maximum_risk_percent: Decimal = Decimal("4")
    acceptance_bars: int = 1


def recovery_metrics(
    context: Swing4HGeriContext,
    rules: RecoveryRules,
    *,
    target_selector: Callable[[Swing4HGeriContext, Decimal, datetime], Decimal | None]
    | None = None,
) -> tuple[NamedValue, ...]:
    validate_context(context)
    bars = context.bars
    lows = pivots(bars, low=True)
    # A lower floor after a decline, not the inverse of an unrelated LONG/SHORT label.
    candidates = tuple(
        i for i in lows if i >= 3 and bars[i].low < min(b.low for b in bars[i - 3 : i])
    )
    previous = (
        {m.name: m.value for m in context.active_structure.metrics}
        if (
            context.active_structure is not None
            and context.active_structure.engine_version == "1.9.0"
        )
        else {}
    )
    source = previous.get("countertrend_level_source_at")
    pinned = next(
        (
            i
            for i in candidates
            if bars[i].timestamp.isoformat() == str(source) or bars[i].timestamp == source
        ),
        None,
    )
    # Preserve the setup throughout its lifecycle, including invalidation; a later floor
    # can create a new setup, but cannot silently move an already accepted entry's stop.
    accepted_before = previous.get("countertrend_accepted_at") is not None
    terminal_before = (
        previous.get("countertrend_expired") is True
        or previous.get("countertrend_state") == GeriMaturity.INVALIDATED
        or "countertrend_target_reached" in previous.get("countertrend_eligibility_reasons", ())
    )
    if pinned is not None and accepted_before and not terminal_before:
        index = pinned
    elif candidates:
        index = candidates[-1]
    else:
        return to_metrics(
            {
                "countertrend_side": TradeSide.LONG,
                "countertrend_state": "NO_RECOVERY",
                "countertrend_maturity": None,
                "countertrend_eligible": False,
                "countertrend_eligibility_reasons": ("no_confirmed_recovery_floor",),
            }
        )
    floor = bars[index]
    confirmed = completed_at(bars[index + 1])
    atr = _duration_normalized_atr(bars[: index + 2])
    # The last local ceiling is fixed at floor confirmation, never moved by the breakout.
    reference = max(b.high for b in bars[max(0, index - 1) : index + 2])
    stop = max(Decimal("0.0001"), floor.low - atr * rules.stop_atr)
    zone_low = max(stop + Decimal("0.0001"), reference - atr * rules.zone_atr)
    zone_high = reference + atr * rules.zone_atr
    fast = tuple(b for b in context.confirmation_bars if b.timestamp >= confirmed)
    breakout, accepted = _acceptance(fast, reference, atr, rules)
    if accepted is not None and breakout is not None:
        # Use the higher low that supports the acceptance, fixed at its first occurrence.
        stop = max(stop, min(b.low for b in fast[breakout : accepted + 1]) - atr * rules.stop_atr)
        zone_low = max(stop + Decimal("0.0001"), zone_low)
        zone_high = max(zone_high, zone_low)
    price = context.current_price
    entry = fast[accepted].close if accepted is not None else price
    known_at = completed_at(fast[accepted]) if accepted is not None else context.current_price_at
    known4 = tuple(b for b in bars if known_at is not None and completed_at(b) <= known_at)
    knownd = tuple(
        b for b in context.daily_bars if known_at is not None and completed_at(b) <= known_at
    )
    target = directional_target(
        TradeSide.LONG,
        entry,
        (*obstacles(known4, TradeSide.LONG), *obstacles(knownd, TradeSide.LONG)),
    )
    if target_selector is not None and known_at is not None:
        target = target_selector(context, entry, known_at)
    rr = directional_rr(TradeSide.LONG, price, stop, target)
    sessions = {
        b.timestamp.astimezone(NY).date() for b in (*bars, *fast) if b.timestamp >= confirmed
    }
    if context.current_price_at is not None:
        sessions.add(context.current_price_at.astimezone(NY).date())
    sessions.add(confirmed.astimezone(NY).date())
    age = max(0, len(sessions) - 1)
    expired = age >= rules.ttl_sessions
    after_entry = fast[accepted + 1 :] if accepted is not None else ()
    floor_lost = any(b.low <= floor.low for b in bars[index + 1 :]) or any(
        b.low <= floor.low for b in fast
    )
    invalidated = floor_lost or price <= stop or any(b.low <= stop for b in after_entry)
    target_hit = target is not None and (
        price >= target or any(b.high >= target for b in after_entry)
    )
    reasons: list[str] = []
    if invalidated:
        reasons.append("countertrend_invalidated")
    if expired:
        reasons.append("countertrend_expired")
    if target_hit:
        reasons.append("countertrend_target_reached")
    if target is None:
        reasons.append("NO_VALID_DIRECTIONAL_TARGET")
    elif rr is None:
        reasons.append("target_or_invalidation_order_failed")
    elif rr <= rules.minimum_rr:
        reasons.append("insufficient_reward_risk")
    if price > reference + atr * rules.maximum_extension_atr:
        reasons.append("countertrend_extended")
    if accepted is not None and (price - stop) / price * 100 > rules.maximum_risk_percent:
        reasons.append("recovery_risk_limit_exceeded")
    four = accepted is not None and any(
        completed_at(b) > completed_at(fast[accepted]) and b.close > reference for b in bars
    )
    continuation = (
        four
        and len(after_entry) >= 2
        and (
            after_entry[-1].close > max(b.high for b in after_entry[:-1])
            and after_entry[-1].low > stop
        )
    )
    stage = (
        "CT4"
        if continuation
        else "CT3"
        if four
        else "CT2"
        if accepted is not None
        else "CT1"
        if any(b.close > floor.close for b in bars[index + 1 :])
        or any(b.close > floor.close for b in fast)
        else "CT0"
    )
    state = {
        "CT0": GeriMaturity.ARMED,
        "CT1": GeriMaturity.IN_ZONE_4H,
        "CT2": GeriMaturity.L2_4H,
        "CT3": GeriMaturity.L3,
        "CT4": GeriMaturity.L4,
    }[stage]
    if reasons:
        state = (
            GeriMaturity.INVALIDATED
            if invalidated
            else GeriMaturity.EXTENDED
            if "countertrend_extended" in reasons
            else GeriMaturity.BUILDING
        )
    fib = fibonacci_confluence(bars, price, atr * rules.zone_atr)
    return to_metrics(
        {
            "countertrend_side": TradeSide.LONG,
            "countertrend_setup_kind": "STRUCTURE_RECOVERY",
            "countertrend_state": state,
            "countertrend_maturity": None if invalidated or expired or target_hit else stage,
            "countertrend_level_kind": GeriLevelKind.SUPPORT,
            "countertrend_level_price": floor.low,
            "countertrend_level_source_at": floor.timestamp,
            "countertrend_level_confirmed_at": confirmed,
            "countertrend_recovery_reference": reference,
            "countertrend_zone_low": zone_low,
            "countertrend_zone_high": zone_high,
            "countertrend_invalidation": stop,
            "countertrend_target": target,
            "countertrend_reward_risk": rr,
            "countertrend_minimum_reward_risk": rules.minimum_rr,
            "countertrend_eligible": not reasons,
            "countertrend_session_age": age,
            "countertrend_expired": expired,
            "countertrend_ttl_sessions": rules.ttl_sessions,
            "countertrend_eligibility_reasons": tuple(reasons) or ("recovery_setup_eligible",),
            "countertrend_fast_confirmation": accepted is not None,
            "countertrend_four_hour_confirmation": four,
            "countertrend_continuation_confirmation": continuation,
            "countertrend_accepted_at": completed_at(fast[accepted])
            if accepted is not None
            else None,
            "countertrend_entry_reference": entry if accepted is not None else None,
            "countertrend_places_orders": False,
            **fib,
        }
    )


def _acceptance(
    bars: tuple[MarketBar, ...],
    reference: Decimal,
    atr: Decimal,
    rules: RecoveryRules,
) -> tuple[int | None, int | None]:
    breakout: int | None = None
    for i, bar in enumerate(bars):
        if i and bar.timestamp - bars[i - 1].timestamp != timedelta(minutes=15):
            breakout = None
        if breakout is None:
            if bar.close > reference + atr * rules.breakout_atr and bar.close > bar.open:
                breakout = i
        elif bar.close < reference or bar.low <= bars[breakout].low:
            breakout = None
        elif i - breakout >= rules.acceptance_bars:
            accepted = bars[breakout + 1 : i + 1]
            if all(b.close > reference and b.low > bars[breakout].low for b in accepted):
                return breakout, i
    return breakout, None


def fibonacci_confluence(
    bars: tuple[MarketBar, ...],
    price: Decimal,
    padding: Decimal,
) -> dict[str, object]:
    result: dict[str, object] = {
        "countertrend_fibonacci_confluence": False,
        "countertrend_confluence_priority": 0,
    }
    lows, highs = pivots(bars, low=True), pivots(bars, low=False)
    for high in reversed(highs):
        before = tuple(i for i in lows if i < high)
        if not before:
            continue
        low = before[-1]
        bottom, top = bars[low].low, bars[high].high
        if top <= bottom or any(b.low <= bottom for b in bars[high + 1 :]):
            continue
        lo, hi = top - Decimal("0.618") * (top - bottom), top - Decimal("0.5") * (top - bottom)
        supports = tuple(
            bars[i].low
            for i in lows
            if i != low and i > high and all(b.close >= bars[i].low for b in bars[i + 1 :])
        )
        match = next((p for p in supports if p - padding <= price <= p + padding), None)
        confluence = lo <= price <= hi and match is not None
        result.update(
            countertrend_fibonacci_low=lo,
            countertrend_fibonacci_high=hi,
            countertrend_fibonacci_anchor_low_at=bars[low].timestamp,
            countertrend_fibonacci_anchor_high_at=bars[high].timestamp,
            countertrend_fibonacci_confirmed_at=completed_at(bars[high + 1]),
            countertrend_fibonacci_support=match,
            countertrend_fibonacci_confluence=confluence,
            countertrend_confluence_priority=1 if confluence else 0,
        )
        return result
    return result
