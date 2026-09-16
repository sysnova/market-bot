# pyright: reportPrivateUsage=false
"""Observed impulse chronology and explicit Swing approval for early entries."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    PatternDirection,
)

from .engine import JsonValue
from .models import EntryWatch
from .v54 import _metrics, _state_decimal
from .v56 import EntryWatcherV56

_STATE = "impulse_pullback_state"


class EntryWatcherV57(EntryWatcherV56):
    engine_version = "5.7.0"

    @staticmethod
    def _new_impulse_state(
        watch: EntryWatch, price: Decimal, analyses: Mapping[AnalysisHorizon, AnalysisResult]
    ) -> dict[str, JsonValue]:
        observation = EntryWatcherV56._current_price_observation(analyses)
        if observation is None or observation[0] < watch.armed_at:
            return {}
        at, observed = observation
        if observed != price or not price.is_finite() or price <= 0:
            return {}
        return _initial_state(watch.original_price, watch.armed_at, price, at)

    def _updated_impulse_state(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, watch: EntryWatch, price: Decimal
    ) -> dict[str, JsonValue]:
        raw = watch.anchor_snapshot.get(_STATE)
        state = dict(cast("dict[str, JsonValue]", raw)) if isinstance(raw, dict) else {}
        observation = self._current_price_observation(self._latest.get(watch.symbol, {}))
        if observation is None or observation[1] != price or not price.is_finite() or price <= 0:
            return state
        at = observation[0]
        last_at = _at(state.get("last_observed_at"))
        peak = _state_decimal(state, "peak")
        if state.get("schema_version") != "2.0.0" or last_at is None or peak is None:
            # Pending legacy geometry has no observed peak timestamp. Start afresh;
            # this hook is never called for an already confirmed EARLY_ENTRY.
            return _initial_state(price, at, price, at)
        if at <= last_at:
            return state
        previous_price = _state_decimal(state, "last_price")
        state.update(
            last_observed_at=at.isoformat(),
            last_price=str(price),
            previous_price=str(previous_price),
        )
        if price > peak:
            state.update(
                peak=str(price), peak_at=at.isoformat(), pullback_low=str(price), pullback_at=None
            )
        elif price < peak:
            low = _state_decimal(state, "pullback_low")
            if state.get("pullback_at") is None or low is None or price < low:
                state.update(pullback_low=str(price), pullback_at=at.isoformat())
        return state

    def _early_entry_levels(
        self,
        watch: EntryWatch,
        *,
        price: Decimal,
        analyses: Mapping[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        # An extended impulse must pass the causal pullback lane; the old second-leg
        # fallback must not undo its rejection.
        if watch.status is EntryWatchStatus.IMPULSE_EXTENDED or not _swing_approved(analyses):
            return None
        return super()._early_entry_levels(watch, price=price, analyses=analyses, now=now)

    def _pullback_entry_levels(
        self,
        state: dict[str, JsonValue],
        *,
        price: Decimal,
        analyses: Mapping[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
        if state.get("schema_version") != "2.0.0" or not _swing_approved(analyses):
            return None
        start, peak, low, last = (
            _at(state.get(k)) for k in ("start_at", "peak_at", "pullback_at", "last_observed_at")
        )
        previous = _state_decimal(state, "previous_price")
        observation = self._current_price_observation(analyses)
        if (
            start is None
            or peak is None
            or low is None
            or last is None
            or not start < peak < low < last <= now
            or previous is None
            or price <= previous
            or _state_decimal(state, "last_price") != price
            or observation != (last, price)
            or now - last > self._transition_price_max_age
        ):
            return None
        return super()._pullback_entry_levels(state, price=price, analyses=analyses, now=now)


def _initial_state(
    start: Decimal, start_at: datetime, price: Decimal, at: datetime
) -> dict[str, JsonValue]:
    return {
        "schema_version": "2.0.0",
        "phase": "AWAITING_PULLBACK",
        "start": str(start),
        "start_at": start_at.isoformat(),
        "peak": str(price),
        "peak_at": at.isoformat(),
        "pullback_low": str(price),
        "pullback_at": None,
        "last_price": str(price),
        "previous_price": str(price),
        "last_observed_at": at.isoformat(),
        "peak_source": "OBSERVED_PRICE",
    }


def _at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _swing_approved(analyses: Mapping[AnalysisHorizon, AnalysisResult]) -> bool:
    long = analyses.get(AnalysisHorizon.LONG_TERM)
    swing = analyses.get(AnalysisHorizon.SWING)
    if (
        long is None
        or swing is None
        or long.direction is not PatternDirection.BULLISH
        or long.verdict in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
        or swing.direction is not PatternDirection.BULLISH
        or swing.verdict is not AnalysisVerdict.FAVORABLE
    ):
        return False
    metrics = _metrics(swing)
    if metrics.get("entry_lane") == "STRUCTURE_RECOVERY":
        return (
            metrics.get("classification") == "recovery"
            and metrics.get("recovery_entry_gate_passed") is True
        )
    return (
        metrics.get("swing_entry_gate_passed") is True
        and metrics.get("structure_broken_confirmed") is not True
    )
