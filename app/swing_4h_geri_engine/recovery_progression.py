"""Keep the recovery thesis separate from successive, independently priced entries."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import cast

from app.contracts import GeriMaturity, MarketBar, NamedValue, TradeSide

from .engine import _duration_normalized_atr  # pyright: ignore[reportPrivateUsage]
from .models import Swing4HGeriContext
from .recovery import (
    RecoveryRules,
    _acceptance,  # pyright: ignore[reportPrivateUsage]
    recovery_metrics,
)
from .tactical_levels import completed_at, directional_rr, directional_target, obstacles, to_metrics


@dataclass(frozen=True)
class EntryWindow:
    accepted: int
    reference: Decimal
    stop: Decimal
    target: Decimal | None
    sequence: int = 0


def _target(context: Swing4HGeriContext, price: Decimal, at: datetime) -> Decimal | None:
    return directional_target(
        TradeSide.LONG,
        price,
        (
            *obstacles(tuple(b for b in context.bars if completed_at(b) <= at), TradeSide.LONG),
            *obstacles(
                tuple(b for b in context.daily_bars if completed_at(b) <= at), TradeSide.LONG
            ),
        ),
    )


def _seed(context: Swing4HGeriContext, rules: RecoveryRules) -> dict[str, object]:
    # V19 owns the original floor/acceptance rules. Its terminal target semantics
    # must not unpin a still-live recovery in V110. No operational order state is inferred.
    previous = context.active_structure
    if previous is not None and previous.engine_version == "1.10.0":
        metrics = tuple(
            NamedValue(
                name=m.name,
                value=tuple(
                    reason
                    for reason in cast(tuple[str, ...], m.value)
                    if reason != "countertrend_target_reached"
                ),
            )
            if m.name == "countertrend_eligibility_reasons"
            else m
            for m in previous.metrics
        )
        previous = previous.model_copy(update={"engine_version": "1.9.0", "metrics": metrics})
    return {
        m.name: m.value
        for m in recovery_metrics(replace(context, active_structure=previous), rules)
    }


def _renew(
    context: Swing4HGeriContext,
    fast: tuple[MarketBar, ...],
    entry: EntryWindow,
    hit: int,
    atr: Decimal,
    rules: RecoveryRules,
) -> EntryWindow | None:
    assert entry.target is not None
    offset = hit
    while offset < len(fast):
        breakout, accepted = _acceptance(fast[offset:], entry.target, atr, rules)
        if breakout is None or accepted is None:
            return None
        stop = (
            min(b.low for b in fast[offset + breakout : offset + accepted + 1])
            - atr * rules.stop_atr
        )
        if stop > entry.stop:
            index = offset + accepted
            return EntryWindow(
                index,
                entry.target,
                stop,
                _target(context, fast[index].close, completed_at(fast[index])),
                entry.sequence + 1,
            )
        offset += accepted + 1
    return None


def progressive_recovery_metrics(
    context: Swing4HGeriContext,
    rules: RecoveryRules,
) -> tuple[NamedValue, ...]:
    m = _seed(context, rules)
    m["countertrend_lifecycle"] = "PROGRESSIVE_RECOVERY"
    origin_at = cast(datetime | None, m.get("countertrend_accepted_at"))
    if origin_at is None:
        return to_metrics(m)
    floor_at = m["countertrend_level_source_at"]
    floor_index = next(i for i, b in enumerate(context.bars) if b.timestamp == floor_at)
    floor = context.bars[floor_index]
    confirmed = cast(datetime, m["countertrend_level_confirmed_at"])
    fast = tuple(b for b in context.confirmation_bars if b.timestamp >= confirmed)
    original = next(i for i, b in enumerate(fast) if completed_at(b) == origin_at)
    atr = _duration_normalized_atr(context.bars[: floor_index + 2])
    reference = cast(Decimal, m["countertrend_recovery_reference"])
    entry = EntryWindow(
        original,
        reference,
        cast(Decimal, m["countertrend_invalidation"]),
        _target(context, fast[original].close, origin_at),
    )
    pending = False
    while entry.target is not None:
        hit = next(
            (i for i in range(entry.accepted + 1, len(fast)) if fast[i].high >= entry.target), None
        )
        if hit is None:
            break
        renewed = _renew(context, fast, entry, hit, atr, rules)
        if renewed is None:
            pending = True
            break
        entry = renewed

    # Structural maturity survives a reached resistance or an invalid tactical stop.
    # It ends only when the structural floor fails or the original thesis expires.
    floor_lost = any(b.low <= floor.low for b in context.bars[floor_index + 1 :]) or any(
        b.low <= floor.low for b in fast
    )
    expired = m["countertrend_expired"] is True
    four = any(completed_at(b) > origin_at and b.close > reference for b in context.bars)
    continued = four and entry.sequence > 0
    stage = "CT4" if continued else "CT3" if four else "CT2"
    previous = context.active_structure
    if previous is not None and previous.engine_version == "1.10.0":
        old = {v.name: v.value for v in previous.metrics}
        if old.get("countertrend_level_source_at") == floor_at:
            peak = str(old.get("countertrend_maturity", ""))
            if peak in {"CT2", "CT3", "CT4"}:
                stage = max(stage, peak)
    price = context.current_price
    rr = directional_rr(TradeSide.LONG, price, entry.stop, entry.target)
    reasons: list[str] = []
    if floor_lost:
        reasons.append("countertrend_invalidated")
    if expired:
        reasons.append("countertrend_expired")
    if pending:
        reasons.append("resistance_acceptance_pending")
    if price <= entry.stop or any(b.low <= entry.stop for b in fast[entry.accepted + 1 :]):
        reasons.append("recovery_entry_invalidated")
    if entry.target is None:
        reasons.append("NO_VALID_DIRECTIONAL_TARGET")
    elif rr is None:
        reasons.append("target_or_invalidation_order_failed")
    elif rr <= rules.minimum_rr:
        reasons.append("insufficient_reward_risk")
    if price > entry.reference + atr * rules.maximum_extension_atr:
        reasons.append("countertrend_extended")
    if (price - entry.stop) / price * 100 > rules.maximum_risk_percent:
        reasons.append("recovery_risk_limit_exceeded")
    state = {"CT2": GeriMaturity.L2_4H, "CT3": GeriMaturity.L3, "CT4": GeriMaturity.L4}[stage]
    if floor_lost:
        state = GeriMaturity.INVALIDATED
    elif reasons:
        state = GeriMaturity.BUILDING
    m.update(
        countertrend_state=state,
        countertrend_maturity=None if floor_lost or expired else stage,
        countertrend_eligible=not reasons,
        countertrend_eligibility_reasons=tuple(reasons) or ("recovery_setup_eligible",),
        countertrend_entry_sequence=entry.sequence,
        countertrend_entry_accepted_at=completed_at(fast[entry.accepted]),
        countertrend_entry_reference=fast[entry.accepted].close,
        countertrend_entry_recovery_level=entry.reference,
        countertrend_invalidation=entry.stop,
        countertrend_target=entry.target,
        countertrend_reward_risk=rr,
        countertrend_zone_low=max(
            entry.stop + Decimal("0.0001"), entry.reference - atr * rules.zone_atr
        ),
        countertrend_zone_high=max(
            entry.stop + Decimal("0.0001"), entry.reference + atr * rules.zone_atr
        ),
        countertrend_four_hour_confirmation=four,
        countertrend_continuation_confirmation=continued,
    )
    return to_metrics(m)
