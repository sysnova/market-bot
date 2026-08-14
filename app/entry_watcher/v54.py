"""Early partial entries and impulse-pullback recovery for Entry Watcher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    EntryWatchTransition,
    PatternDirection,
    new_uuid7,
)

from .engine import EntryWatcherPolicy, JsonValue
from .models import EntryWatch
from .ports import EntryWatchStore
from .v53 import EntryWatcherV53

ZERO = Decimal("0")
HUNDRED = Decimal("100")
FOUR_PLACES = Decimal("0.0001")
TWO_PLACES = Decimal("0.01")
_IMPULSE_STATE = "impulse_pullback_state"


class EntryWatcherV54(EntryWatcherV53):
    """Open an efficient L1 early and rebuild entry structure after a missed impulse."""

    engine_version = "5.4.0"

    def __init__(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
        id_factory: Callable[[], UUID] = new_uuid7,
        minimum_reconfirmation_delay: timedelta = timedelta(minutes=3),
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        no_retest_higher_low_enabled: bool = True,
        zone_exit_buffer_percent: Decimal = Decimal("0.25"),
        initial_arm_min_score: Decimal = Decimal("50"),
        initial_arm_max_distance_percent: Decimal = Decimal("4"),
        initial_arm_max_distance_atr: Decimal = Decimal("2"),
        trigger_on_first_mature_confirmation: bool = True,
        early_entry_max_extension_percent: Decimal = Decimal("4"),
        early_entry_max_extension_atr: Decimal = Decimal("1"),
        early_entry_min_reward_risk: Decimal = Decimal("1.5"),
        pullback_min_retracement: Decimal = Decimal("0.382"),
        pullback_max_retracement: Decimal = Decimal("0.618"),
        pullback_stop_atr_buffer: Decimal = Decimal("0.25"),
        pullback_min_reward_risk: Decimal = Decimal("2"),
    ) -> None:
        super().__init__(
            store=store,
            policy=policy,
            id_factory=id_factory,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            no_retest_higher_low_enabled=no_retest_higher_low_enabled,
            zone_exit_buffer_percent=zone_exit_buffer_percent,
            initial_arm_min_score=initial_arm_min_score,
            initial_arm_max_distance_percent=initial_arm_max_distance_percent,
            initial_arm_max_distance_atr=initial_arm_max_distance_atr,
            trigger_on_first_mature_confirmation=trigger_on_first_mature_confirmation,
        )
        values = (
            early_entry_max_extension_percent,
            early_entry_max_extension_atr,
            early_entry_min_reward_risk,
            pullback_stop_atr_buffer,
            pullback_min_reward_risk,
        )
        if any(not value.is_finite() or value <= ZERO for value in values):
            raise ValueError("entry and pullback thresholds must be finite and positive")
        if not ZERO < pullback_min_retracement < pullback_max_retracement < Decimal("1"):
            raise ValueError("pullback retracements must satisfy 0 < min < max < 1")
        self._early_max_percent = early_entry_max_extension_percent
        self._early_max_atr = early_entry_max_extension_atr
        self._early_min_rr = early_entry_min_reward_risk
        self._pullback_min = pullback_min_retracement
        self._pullback_max = pullback_max_retracement
        self._pullback_stop_buffer = pullback_stop_atr_buffer
        self._pullback_min_rr = pullback_min_reward_risk

    async def ingest(self, result: AnalysisResult, *, now: datetime) -> EntryWatchTransition | None:
        transition = await super().ingest(result, now=now)
        if transition is not None:
            return transition
        active = await self._store.load_active(result.symbol)
        if active is None:
            return None
        analyses = self._latest.get(active.symbol, {})
        price = self._current_price(analyses) or active.current_price

        if active.status in {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}:
            levels = self._early_entry_levels(active, price=price, analyses=analyses, now=now)
            if levels is not None:
                invalidation, target, reward_risk = levels
                return await self._change(
                    active,
                    EntryWatchStatus.EARLY_ENTRY,
                    now=now,
                    price=price,
                    reasons=(
                        "early_entry_confirmed",
                        "partial_position_before_full_maturity",
                        f"early_entry_reward_risk:{reward_risk}",
                    ),
                    analyses=analyses,
                    entry_invalidation=invalidation,
                    entry_target=target,
                )

        extension = self._zone_extension_percent(active, price)
        if (
            active.status in {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}
            and extension > self._early_max_percent
        ):
            state = self._new_impulse_state(active, price, analyses)
            return await self._change(
                active,
                EntryWatchStatus.IMPULSE_EXTENDED,
                now=now,
                price=price,
                reasons=(
                    "entry_window_missed",
                    "impulse_extended_awaiting_pullback",
                    f"extension_percent:{extension}",
                ),
                analyses=analyses,
                anchor_updates={_IMPULSE_STATE: state},
            )

        if active.status is not EntryWatchStatus.IMPULSE_EXTENDED:
            return None
        state = self._updated_impulse_state(active, price)
        snapshot = dict(active.anchor_snapshot)
        snapshot[_IMPULSE_STATE] = state
        tracked = active.model_copy(update={"anchor_snapshot": snapshot})
        await self._store.update_anchor_snapshot(tracked)
        levels = self._pullback_entry_levels(state, price=price, analyses=analyses, now=now)
        if levels is None:
            return None
        invalidation, target, reward_risk, zone_low, zone_high = levels
        return await self._change(
            tracked,
            EntryWatchStatus.EARLY_ENTRY,
            now=now,
            price=price,
            reasons=(
                "impulse_pullback_reclaimed",
                "dynamic_pullback_entry_confirmed",
                f"pullback_zone:{zone_low}-{zone_high}",
                f"pullback_reward_risk:{reward_risk}",
            ),
            analyses=analyses,
            anchor_updates={_IMPULSE_STATE: {**state, "phase": "EARLY_ENTRY"}},
            entry_invalidation=invalidation,
            entry_target=target,
        )

    def _early_entry_levels(
        self,
        watch: EntryWatch,
        *,
        price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        if not self._fresh_core_analyses(analyses, now=now) or not _early_confirmation(analyses):
            return None
        extension = max(ZERO, price - watch.zone_high)
        extension_percent = extension / watch.zone_high * HUNDRED
        atr = _metric_decimal(analyses, "atr14", AnalysisHorizon.SWING)
        if (
            extension_percent > self._early_max_percent
            or atr is None
            or atr <= ZERO
            or extension / atr > self._early_max_atr
        ):
            return None
        target = _target(analyses, price)
        invalidation = _nearest_invalidation(analyses, price, fallback=watch.invalidation)
        if target is None or invalidation >= price:
            return None
        reward_risk = (target - price) / (price - invalidation)
        if reward_risk < self._early_min_rr:
            return None
        return invalidation, target, _rounded(reward_risk, TWO_PLACES)

    def _pullback_entry_levels(
        self,
        state: dict[str, JsonValue],
        *,
        price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
        if not self._fresh_core_analyses(analyses, now=now):
            return None
        start = _state_decimal(state, "start")
        peak = _state_decimal(state, "peak")
        pullback_low = _state_decimal(state, "pullback_low")
        atr = _metric_decimal(analyses, "atr14", AnalysisHorizon.INTRADAY)
        if start is None or peak is None or pullback_low is None or atr is None:
            return None
        impulse = peak - start
        if impulse <= ZERO or atr <= ZERO:
            return None
        zone_high = peak - impulse * self._pullback_min
        zone_low = peak - impulse * self._pullback_max
        if not zone_low <= price <= zone_high or not _pullback_reclaim(analyses, price):
            return None
        invalidation = pullback_low - atr * self._pullback_stop_buffer
        if invalidation <= ZERO or invalidation >= price or peak <= price:
            return None
        reward_risk = (peak - price) / (price - invalidation)
        if reward_risk < self._pullback_min_rr:
            return None
        return (
            _rounded(invalidation),
            _rounded(peak),
            _rounded(reward_risk, TWO_PLACES),
            _rounded(zone_low),
            _rounded(zone_high),
        )

    def _fresh_core_analyses(
        self,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        *,
        now: datetime,
    ) -> bool:
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        return required.issubset(analyses) and all(
            timedelta(0) <= now - analyses[horizon].as_of <= self._policy.max_ages[horizon]
            for horizon in required
        )

    @staticmethod
    def _zone_extension_percent(watch: EntryWatch, price: Decimal) -> Decimal:
        return _rounded(max(ZERO, price - watch.zone_high) / watch.zone_high * HUNDRED)

    @staticmethod
    def _new_impulse_state(
        watch: EntryWatch,
        price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> dict[str, JsonValue]:
        structural_high = _metric_decimal(analyses, "liquidity_high", AnalysisHorizon.SWING)
        peak = max(price, structural_high or price)
        return {
            "schema_version": "1.0.0",
            "phase": "AWAITING_PULLBACK",
            "start": str(watch.original_price),
            "peak": str(peak),
            "pullback_low": str(price),
        }

    @staticmethod
    def _updated_impulse_state(watch: EntryWatch, price: Decimal) -> dict[str, JsonValue]:
        raw = watch.anchor_snapshot.get(_IMPULSE_STATE)
        state = dict(cast("dict[str, JsonValue]", raw)) if isinstance(raw, dict) else {}
        start = _state_decimal(state, "start") or watch.original_price
        old_peak = _state_decimal(state, "peak") or price
        old_low = _state_decimal(state, "pullback_low") or price
        if price >= old_peak:
            peak = price
            pullback_low = price
        else:
            peak = old_peak
            pullback_low = min(old_low, price)
        return {
            "schema_version": "1.0.0",
            "phase": "AWAITING_PULLBACK",
            "start": str(start),
            "peak": str(peak),
            "pullback_low": str(pullback_low),
        }


def _early_confirmation(analyses: dict[AnalysisHorizon, AnalysisResult]) -> bool:
    required = {AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY}
    if not required.issubset(analyses):
        return False
    long_term = analyses[AnalysisHorizon.LONG_TERM]
    swing = analyses[AnalysisHorizon.SWING]
    intraday = analyses[AnalysisHorizon.INTRADAY]
    intraday_metrics = _metrics(intraday)
    return bool(
        long_term.direction is PatternDirection.BULLISH
        and long_term.verdict not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
        and swing.direction is PatternDirection.BULLISH
        and swing.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.CAUTION}
        and _metrics(swing).get("anchored_vwap_gate_passed") is True
        and intraday.direction is PatternDirection.BULLISH
        and intraday.verdict is AnalysisVerdict.FAVORABLE
        and intraday_metrics.get("confirmation_gate_passed") is True
        and intraday_metrics.get("entry_efficiency_gate_passed") is True
    )


def _pullback_reclaim(analyses: dict[AnalysisHorizon, AnalysisResult], price: Decimal) -> bool:
    intraday = analyses.get(AnalysisHorizon.INTRADAY)
    if intraday is None:
        return False
    metrics = _metrics(intraday)
    trigger = _decimal(metrics.get("entry_trigger_level"))
    return bool(
        intraday.direction is PatternDirection.BULLISH
        and intraday.verdict is AnalysisVerdict.FAVORABLE
        and metrics.get("confirmation_gate_passed") is True
        and metrics.get("entry_efficiency_gate_passed") is True
        and metrics.get("five_minute_higher_low") is True
        and trigger is not None
        and price >= trigger
    )


def _target(analyses: dict[AnalysisHorizon, AnalysisResult], price: Decimal) -> Decimal | None:
    for horizon in (AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY):
        for name in ("target_2r", "objective_level"):
            value = _metric_decimal(analyses, name, horizon)
            if value is not None and value > price:
                return value
    return None


def _nearest_invalidation(
    analyses: dict[AnalysisHorizon, AnalysisResult],
    price: Decimal,
    *,
    fallback: Decimal,
) -> Decimal:
    values = [fallback]
    for horizon, name in (
        (AnalysisHorizon.SWING, "invalidation"),
        (AnalysisHorizon.INTRADAY, "invalidation_level"),
    ):
        value = _metric_decimal(analyses, name, horizon)
        if value is not None and value < price:
            values.append(value)
    return max(values)


def _metric_decimal(
    analyses: dict[AnalysisHorizon, AnalysisResult], name: str, horizon: AnalysisHorizon
) -> Decimal | None:
    result = analyses.get(horizon)
    return None if result is None else _decimal(_metrics(result).get(name))


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _state_decimal(state: dict[str, JsonValue], name: str) -> Decimal | None:
    return _decimal(state.get(name))


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except ValueError, ArithmeticError:
        return None
    return parsed if parsed.is_finite() else None


def _rounded(value: Decimal, quantum: Decimal = FOUR_PLACES) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
