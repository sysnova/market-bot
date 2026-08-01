"""No-look-ahead outcome measurement for persisted PatreonCaps transitions."""

from __future__ import annotations

from decimal import Decimal

from app.contracts import MarketBar, PatreonCapsState, PatreonCapsTransition

from .indicators import rounded
from .models import ReplayOutcome

_BUY_STATES = {
    PatreonCapsState.CONFIRMED_V,
    PatreonCapsState.CONFIRMED_BASE,
    PatreonCapsState.IMPULSE_RETEST,
}


def replay_outcomes(
    transitions: tuple[PatreonCapsTransition, ...],
    daily_bars: dict[str, tuple[MarketBar, ...]],
) -> tuple[ReplayOutcome, ...]:
    """Measure only bars finalized after each signal timestamp."""
    armed_at = {
        item.watch_id: item.occurred_at
        for item in transitions
        if item.state is PatreonCapsState.WATCH_ZONE
    }
    outcomes: list[ReplayOutcome] = []
    for transition in transitions:
        if transition.state not in _BUY_STATES:
            continue
        future = tuple(
            bar
            for bar in daily_bars.get(transition.symbol, ())
            if bar.is_final and bar.timestamp > transition.occurred_at
        )
        measured = future[:60]
        entry = transition.current_price
        confirmation_start = armed_at.get(transition.watch_id)
        outcomes.append(ReplayOutcome(
            transition_id=transition.transition_id,
            watch_id=transition.watch_id,
            symbol=transition.symbol,
            occurred_at=transition.occurred_at,
            entry_price=entry,
            return_5d=_horizon_return(future, entry, 5),
            return_20d=_horizon_return(future, entry, 20),
            return_60d=_horizon_return(future, entry, 60),
            mfe_percent=(
                _percent(max(bar.high for bar in measured), entry) if measured else None
            ),
            mae_percent=(
                _percent(min(bar.low for bar in measured), entry) if measured else None
            ),
            invalidated=any(bar.close < transition.invalidation for bar in measured),
            confirmation_hours=(
                rounded(
                    Decimal(
                        str(
                            (transition.occurred_at - confirmation_start).total_seconds()
                            / 3600
                        )
                    )
                )
                if confirmation_start is not None
                else None
            ),
        ))
    return tuple(outcomes)


def _horizon_return(
    bars: tuple[MarketBar, ...], entry: Decimal, horizon: int
) -> Decimal | None:
    if len(bars) < horizon:
        return None
    return _percent(bars[horizon - 1].close, entry)


def _percent(price: Decimal, entry: Decimal) -> Decimal:
    return rounded((price / entry - Decimal("1")) * Decimal("100"))
