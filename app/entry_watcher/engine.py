"""Persistent state machine that remembers desired entries across analyses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, cast
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

from .models import EntryWatch
from .ports import EntryWatchStore

FOUR_PLACES = Decimal("0.0001")
TWO_PLACES = Decimal("0.01")
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
_ACTIVE = {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}
_ARMABLE_CLASSIFICATIONS = {"buy_zone", "extended", "setup", "watch_pullback"}
_SWING_CONFIRMATIONS = {"breakout", "pullback"}
_PRICE_PRIORITY = {
    AnalysisHorizon.OPTIONS_GAMMA: -2,
    AnalysisHorizon.VOLUME_STRUCTURE: -1,
    AnalysisHorizon.LONG_TERM: 0,
    AnalysisHorizon.DILUTION: 1,
    AnalysisHorizon.SWING: 2,
    AnalysisHorizon.INTRADAY: 3,
}


def _default_max_ages() -> dict[AnalysisHorizon, timedelta]:
    return {
        AnalysisHorizon.OPTIONS_GAMMA: timedelta(minutes=60),
        AnalysisHorizon.VOLUME_STRUCTURE: timedelta(days=14),
        AnalysisHorizon.LONG_TERM: timedelta(days=7),
        AnalysisHorizon.DILUTION: timedelta(hours=24),
        AnalysisHorizon.SWING: timedelta(hours=8),
        AnalysisHorizon.INTRADAY: timedelta(minutes=30),
    }


@dataclass(frozen=True, slots=True)
class EntryWatcherPolicy:
    ttl: timedelta = timedelta(weeks=8)
    rearm_cooldown: timedelta = timedelta(days=14)
    max_ages: dict[AnalysisHorizon, timedelta] = field(default_factory=_default_max_ages)
    continuation_grace: timedelta = timedelta(hours=72)
    continuation_max_percent: Decimal = Decimal("4")
    continuation_max_atr: Decimal = Decimal("0.75")
    continuation_fallback_percent: Decimal = Decimal("2")
    continuation_min_reward_risk: Decimal = Decimal("2")
    trigger_rearm_cooldown: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            raise ValueError("entry watch ttl must be positive")
        if self.rearm_cooldown <= timedelta(0):
            raise ValueError("entry watch rearm cooldown must be positive")
        if set(self.max_ages) != set(AnalysisHorizon):
            raise ValueError("entry watch policy must configure every horizon")
        if any(value <= timedelta(0) for value in self.max_ages.values()):
            raise ValueError("entry watch freshness must be positive")
        if self.continuation_grace <= timedelta(0):
            raise ValueError("entry continuation grace must be positive")
        if self.trigger_rearm_cooldown <= timedelta(0):
            raise ValueError("trigger rearm cooldown must be positive")
        if any(
            value <= Decimal("0")
            for value in (
                self.continuation_max_percent,
                self.continuation_max_atr,
                self.continuation_fallback_percent,
                self.continuation_min_reward_risk,
            )
        ):
            raise ValueError("entry continuation limits must be positive")
        if self.continuation_fallback_percent > self.continuation_max_percent:
            raise ValueError("fallback continuation percent cannot exceed the hard cap")


class EntryWatcher:
    """Arm a fixed Long thesis and await multi-horizon entry confirmation."""

    engine_id = "entry-watcher"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
        id_factory: Callable[[], UUID] = new_uuid7,
    ) -> None:
        self._store = store
        self._policy = policy or EntryWatcherPolicy()
        self._id_factory = id_factory
        self._latest: dict[str, dict[AnalysisHorizon, AnalysisResult]] = {}

    async def ingest(self, result: AnalysisResult, *, now: datetime) -> EntryWatchTransition | None:
        self._validate_time(result, now)
        latest = self._latest.setdefault(result.symbol, {})
        existing_result = latest.get(result.horizon)
        if existing_result is not None and result.as_of < existing_result.as_of:
            return None
        latest[result.horizon] = result

        active = await self._store.load_active(result.symbol)
        if active is not None and now >= active.expires_at:
            return await self._change(
                active,
                EntryWatchStatus.EXPIRED,
                now=now,
                price=self._current_price(latest) or active.current_price,
                reasons=("entry_watch_expired",),
                analyses=latest,
            )
        if active is None:
            if result.horizon is AnalysisHorizon.LONG_TERM:
                previous = await self._store.load_latest(result.symbol)
                if (
                    previous is not None
                    and previous.status not in _ACTIVE
                    and now - previous.updated_at < self._policy.rearm_cooldown
                    and self._same_thesis(previous, result)
                ):
                    return None
                return await self._arm(result, now=now)
            return None

        current_price = self._current_price(latest) or active.current_price
        invalidation_reason = self._invalidation_reason(
            active, result=result, current_price=current_price
        )
        if invalidation_reason is not None:
            return await self._change(
                active,
                EntryWatchStatus.INVALIDATED,
                now=now,
                price=current_price,
                reasons=(invalidation_reason,),
                analyses=latest,
            )

        in_original_zone = active.zone_low <= current_price <= active.zone_high
        if in_original_zone and self._confirmed(latest, now=now):
            return await self._change(
                active,
                EntryWatchStatus.TRIGGERED,
                now=now,
                price=current_price,
                reasons=self._confirmation_reasons(latest),
                analyses=latest,
            )

        continuation = self._continuation_candidate(
            active,
            current_price=current_price,
            analyses=latest,
            now=now,
        )
        if continuation is not None and self._continuation_confirmed(latest, now=now):
            reward_risk = self._continuation_reward_risk(
                active,
                current_price=current_price,
                analyses=latest,
            )
            if reward_risk is not None and reward_risk >= self._policy.continuation_min_reward_risk:
                extension_percent, extension_atr = continuation
                return await self._change(
                    active,
                    EntryWatchStatus.TRIGGERED,
                    now=now,
                    price=current_price,
                    reasons=self._continuation_reasons(
                        active,
                        extension_percent=extension_percent,
                        extension_atr=extension_atr,
                        reward_risk=reward_risk,
                        analyses=latest,
                    ),
                    analyses=latest,
                )

        reached_zone = self._reached_target_zone(active, current_price=current_price)
        if active.status is EntryWatchStatus.ARMED and reached_zone:
            return await self._change(
                active,
                EntryWatchStatus.IN_ZONE,
                now=now,
                price=current_price,
                reasons=("target_zone_reached", "awaiting_entry_confirmation"),
                analyses=latest,
            )
        if active.status is EntryWatchStatus.IN_ZONE and self._left_target_zone(
            active,
            current_price=current_price,
        ):
            if continuation is not None:
                touched_at = self._zone_touched_at(active)
                return await self._change(
                    active,
                    EntryWatchStatus.ARMED,
                    now=now,
                    price=current_price,
                    reasons=(
                        "breakaway_continuation_pending",
                        "awaiting_fresh_intraday_confirmation",
                    ),
                    analyses=latest,
                    anchor_updates=(
                        {"zone_touched_at": touched_at.isoformat()}
                        if touched_at is not None
                        else None
                    ),
                )
            reasons = ["left_target_zone_without_confirmation"]
            if self._recent_zone_touch(active, now=now):
                reasons.append("continuation_chase_cap_exceeded")
            return await self._change(
                active,
                EntryWatchStatus.ARMED,
                now=now,
                price=current_price,
                reasons=tuple(reasons),
                analyses=latest,
            )
        return None

    def _reached_target_zone(self, watch: EntryWatch, *, current_price: Decimal) -> bool:
        return watch.invalidation < current_price <= watch.zone_high

    def _left_target_zone(self, watch: EntryWatch, *, current_price: Decimal) -> bool:
        return current_price > watch.zone_high

    @staticmethod
    def _same_thesis(watch: EntryWatch, result: AnalysisResult) -> bool:
        metrics = _metrics(result)
        return (
            _decimal(metrics.get("buy_zone_low")) == watch.zone_low
            and _decimal(metrics.get("buy_zone_high")) == watch.zone_high
            and _decimal(metrics.get("invalidation")) == watch.invalidation
        )

    async def _arm(self, result: AnalysisResult, *, now: datetime) -> EntryWatchTransition | None:
        metrics = _metrics(result)
        classification = metrics.get("classification")
        zone_low = _decimal(metrics.get("buy_zone_low"))
        zone_high = _decimal(metrics.get("buy_zone_high"))
        invalidation = _decimal(metrics.get("invalidation"))
        price = _decimal(metrics.get("reference_price"))
        if (
            result.direction is not PatternDirection.BULLISH
            or result.verdict in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            or classification not in _ARMABLE_CLASSIFICATIONS
            or zone_low is None
            or zone_high is None
            or invalidation is None
            or price is None
            or not invalidation < zone_low <= zone_high
            or price <= invalidation
        ):
            return None

        status = (
            EntryWatchStatus.IN_ZONE
            if invalidation < price <= zone_high
            else EntryWatchStatus.ARMED
        )
        correction = max(Decimal("0"), (price - zone_high) / price * Decimal("100"))
        anchor_snapshot: dict[str, Any] = {
            "classification": classification,
            "engine_version": result.engine_version,
            "watcher_engine_version": self.engine_version,
            "score": str(result.score),
            "reasons": list(result.reasons),
            "metrics": _json_value(metrics),
            "confirmation_policy": {
                "long": "bullish_and_not_avoid",
                "dilution": "warning_only_not_a_gate",
                "swing": "favorable_pullback_or_breakout",
                "intraday": "favorable_bullish",
                "continuation": "recent_zone_touch_with_distance_and_live_rr_caps",
            },
        }
        if status is EntryWatchStatus.IN_ZONE:
            anchor_snapshot["zone_touched_at"] = now.isoformat()
        watch = EntryWatch(
            watch_id=self._id_factory(),
            symbol=result.symbol,
            status=status,
            armed_at=now,
            updated_at=now,
            expires_at=now + self._policy.ttl,
            zone_low=zone_low,
            zone_high=zone_high,
            invalidation=invalidation,
            original_price=price,
            current_price=price,
            correction_target_percent=correction.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP),
            source_analysis_id=result.analysis_id,
            source_context_hash=result.context_hash,
            anchor_snapshot=anchor_snapshot,
        )
        transition = self._transition(
            watch,
            previous=None,
            status=status,
            now=now,
            price=price,
            reasons=(
                "long_entry_thesis_armed",
                (
                    "target_zone_reached"
                    if status is EntryWatchStatus.IN_ZONE
                    else "awaiting_pullback"
                ),
            ),
            analyses={AnalysisHorizon.LONG_TERM: result},
        )
        await self._store.create(watch, transition)
        return transition

    async def _change(
        self,
        watch: EntryWatch,
        status: EntryWatchStatus,
        *,
        now: datetime,
        price: Decimal,
        reasons: tuple[str, ...],
        analyses: dict[AnalysisHorizon, AnalysisResult],
        anchor_updates: dict[str, JsonValue] | None = None,
    ) -> EntryWatchTransition:
        terminal_at = now if status not in _ACTIVE else None
        snapshot = dict(watch.anchor_snapshot)
        if status is EntryWatchStatus.IN_ZONE:
            snapshot["zone_touched_at"] = now.isoformat()
        if anchor_updates:
            snapshot.update(anchor_updates)
        updated = watch.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "current_price": price,
                "terminal_at": terminal_at,
                "anchor_snapshot": snapshot,
            }
        )
        transition = self._transition(
            updated,
            previous=watch.status,
            status=status,
            now=now,
            price=price,
            reasons=reasons,
            analyses=analyses,
        )
        await self._store.transition(updated, transition)
        return transition

    @staticmethod
    def _transition(
        watch: EntryWatch,
        *,
        previous: EntryWatchStatus | None,
        status: EntryWatchStatus,
        now: datetime,
        price: Decimal,
        reasons: tuple[str, ...],
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> EntryWatchTransition:
        ordered = tuple(horizon for horizon in AnalysisHorizon if horizon in analyses)
        return EntryWatchTransition(
            watch_id=watch.watch_id,
            symbol=watch.symbol,
            previous_status=previous,
            status=status,
            occurred_at=now,
            zone_low=watch.zone_low,
            zone_high=watch.zone_high,
            invalidation=watch.invalidation,
            current_price=price,
            watch_expires_at=watch.expires_at,
            reasons=reasons,
            horizons=ordered,
            source_analysis_ids=tuple(analyses[horizon].analysis_id for horizon in ordered),
        )

    def _confirmed(self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime) -> bool:
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        if not required.issubset(analyses):
            return False
        if any(
            now - analyses[horizon].as_of > self._policy.max_ages[horizon] for horizon in required
        ):
            return False
        long_term = analyses[AnalysisHorizon.LONG_TERM]
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        return (
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict is AnalysisVerdict.FAVORABLE
            and _metrics(swing).get("classification") in _SWING_CONFIRMATIONS
            and intraday.direction is PatternDirection.BULLISH
            and intraday.verdict is AnalysisVerdict.FAVORABLE
        )

    def _continuation_confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        return self._confirmed(analyses, now=now)

    def _continuation_candidate(
        self,
        watch: EntryWatch,
        *,
        current_price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal | str] | None:
        touched_at = self._zone_touched_at(watch)
        if (
            touched_at is None
            or now - touched_at > self._policy.continuation_grace
            or current_price <= watch.zone_high
        ):
            return None
        extension = current_price - watch.zone_high
        extension_percent = (extension / watch.zone_high * Decimal("100")).quantize(
            FOUR_PLACES, rounding=ROUND_HALF_UP
        )
        if extension_percent > self._policy.continuation_max_percent:
            return None
        atr14 = self._latest_decimal_metric(
            analyses,
            "atr14",
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.LONG_TERM),
        )
        if atr14 is None or atr14 <= Decimal("0"):
            if extension_percent > self._policy.continuation_fallback_percent:
                return None
            return extension_percent, "unavailable"
        extension_atr = (extension / atr14).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        if extension_atr > self._policy.continuation_max_atr:
            return None
        return extension_percent, extension_atr

    def _continuation_reward_risk(
        self,
        watch: EntryWatch,
        *,
        current_price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> Decimal | None:
        target = self._latest_decimal_metric(
            analyses,
            "target_2r",
            "objective_level",
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        )
        invalidation_candidates = [watch.invalidation]
        for horizon, names in (
            (AnalysisHorizon.SWING, ("invalidation",)),
            (AnalysisHorizon.INTRADAY, ("invalidation_level",)),
        ):
            value = self._latest_decimal_metric(
                analyses,
                *names,
                horizons=(horizon,),
            )
            if value is not None and value < current_price:
                invalidation_candidates.append(value)
        invalidation = max(invalidation_candidates)
        if target is None or target <= current_price or invalidation >= current_price:
            return None
        return ((target - current_price) / (current_price - invalidation)).quantize(
            TWO_PLACES,
            rounding=ROUND_HALF_UP,
        )

    def _continuation_reasons(
        self,
        watch: EntryWatch,
        *,
        extension_percent: Decimal,
        extension_atr: Decimal | str,
        reward_risk: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[str, ...]:
        return (
            "breakaway_continuation_confirmed",
            f"continuation_extension_percent:{extension_percent}",
            f"continuation_extension_atr:{extension_atr}",
            f"continuation_reward_risk:{reward_risk}",
            self._dilution_warning(analyses),
        )

    def _recent_zone_touch(self, watch: EntryWatch, *, now: datetime) -> bool:
        touched_at = self._zone_touched_at(watch)
        return bool(
            touched_at is not None
            and timedelta(0) <= now - touched_at <= self._policy.continuation_grace
        )

    @staticmethod
    def _zone_touched_at(watch: EntryWatch) -> datetime | None:
        value = watch.anchor_snapshot.get("zone_touched_at")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            if parsed.tzinfo is not None:
                return parsed
        if watch.status is EntryWatchStatus.IN_ZONE:
            return watch.updated_at
        return None

    @staticmethod
    def _latest_decimal_metric(
        analyses: dict[AnalysisHorizon, AnalysisResult],
        *names: str,
        horizons: tuple[AnalysisHorizon, ...],
    ) -> Decimal | None:
        for horizon in horizons:
            result = analyses.get(horizon)
            if result is None:
                continue
            metrics = _metrics(result)
            for name in names:
                value = _decimal(metrics.get(name))
                if value is not None:
                    return value
        return None

    def _confirmation_reasons(
        self, analyses: dict[AnalysisHorizon, AnalysisResult]
    ) -> tuple[str, ...]:
        return (
            "multi_horizon_entry_confirmed",
            self._dilution_warning(analyses),
        )

    @staticmethod
    def _invalidation_reason(
        watch: EntryWatch, *, result: AnalysisResult, current_price: Decimal
    ) -> str | None:
        if result.horizon is AnalysisHorizon.LONG_TERM and (
            result.verdict is AnalysisVerdict.AVOID or result.direction is PatternDirection.BEARISH
        ):
            return "long_structure_invalidated"
        if current_price <= watch.invalidation:
            return "original_invalidation_breached"
        return None

    @staticmethod
    def _dilution_warning(
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> str:
        dilution = analyses.get(AnalysisHorizon.DILUTION)
        if dilution is None or dilution.verdict is AnalysisVerdict.INSUFFICIENT_DATA:
            return "dilution_warning:unavailable"
        return f"dilution_warning:{dilution.verdict.value.lower()}"

    @staticmethod
    def _current_price(analyses: dict[AnalysisHorizon, AnalysisResult]) -> Decimal | None:
        candidates = [
            (result.as_of, _PRICE_PRIORITY[horizon], price)
            for horizon, result in analyses.items()
            if (price := _decimal(_metrics(result).get("reference_price"))) is not None
        ]
        return max(candidates)[2] if candidates else None

    @staticmethod
    def _validate_time(result: AnalysisResult, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("entry watcher time must be timezone-aware UTC")
        if result.as_of > now:
            raise ValueError("analysis as_of cannot be in the future")


def _metrics(result: AnalysisResult) -> dict[str, Any]:
    return {item.name: item.value for item in result.metrics}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def _json_value(value: object) -> JsonValue:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_value(item) for key, item in mapping.items()}
    if isinstance(value, (tuple, list)):
        items = cast(tuple[object, ...] | list[object], value)
        return [_json_value(item) for item in items]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
