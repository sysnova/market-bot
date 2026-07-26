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
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
_ACTIVE = {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}
_ARMABLE_CLASSIFICATIONS = {"buy_zone", "extended", "setup", "watch_pullback"}
_SWING_CONFIRMATIONS = {"breakout", "pullback"}
_PRICE_PRIORITY = {
    AnalysisHorizon.LONG_TERM: 0,
    AnalysisHorizon.DILUTION: 1,
    AnalysisHorizon.SWING: 2,
    AnalysisHorizon.INTRADAY: 3,
}


def _default_max_ages() -> dict[AnalysisHorizon, timedelta]:
    return {
        AnalysisHorizon.LONG_TERM: timedelta(days=7),
        AnalysisHorizon.DILUTION: timedelta(hours=24),
        AnalysisHorizon.SWING: timedelta(hours=8),
        AnalysisHorizon.INTRADAY: timedelta(minutes=30),
    }


@dataclass(frozen=True, slots=True)
class EntryWatcherPolicy:
    ttl: timedelta = timedelta(weeks=8)
    max_ages: dict[AnalysisHorizon, timedelta] = field(default_factory=_default_max_ages)

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            raise ValueError("entry watch ttl must be positive")
        if set(self.max_ages) != set(AnalysisHorizon):
            raise ValueError("entry watch policy must configure every horizon")
        if any(value <= timedelta(0) for value in self.max_ages.values()):
            raise ValueError("entry watch freshness must be positive")


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

    async def ingest(
        self, result: AnalysisResult, *, now: datetime
    ) -> EntryWatchTransition | None:
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
                reasons=("multi_horizon_entry_confirmed",),
                analyses=latest,
            )

        reached_zone = active.invalidation < current_price <= active.zone_high
        if active.status is EntryWatchStatus.ARMED and reached_zone:
            return await self._change(
                active,
                EntryWatchStatus.IN_ZONE,
                now=now,
                price=current_price,
                reasons=("target_zone_reached", "awaiting_entry_confirmation"),
                analyses=latest,
            )
        if active.status is EntryWatchStatus.IN_ZONE and current_price > active.zone_high:
            return await self._change(
                active,
                EntryWatchStatus.ARMED,
                now=now,
                price=current_price,
                reasons=("left_target_zone_without_confirmation",),
                analyses=latest,
            )
        return None

    async def _arm(
        self, result: AnalysisResult, *, now: datetime
    ) -> EntryWatchTransition | None:
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
            correction_target_percent=correction.quantize(
                FOUR_PLACES, rounding=ROUND_HALF_UP
            ),
            source_analysis_id=result.analysis_id,
            source_context_hash=result.context_hash,
            anchor_snapshot={
                "classification": classification,
                "engine_version": result.engine_version,
                "score": str(result.score),
                "reasons": list(result.reasons),
                "metrics": _json_value(metrics),
                "confirmation_policy": {
                    "long": "bullish_and_not_avoid",
                    "dilution": "not_avoid",
                    "swing": "favorable_pullback_or_breakout",
                    "intraday": "favorable_bullish",
                },
            },
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
    ) -> EntryWatchTransition:
        terminal_at = now if status not in _ACTIVE else None
        updated = watch.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "current_price": price,
                "terminal_at": terminal_at,
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

    def _confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.DILUTION,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        if not required.issubset(analyses):
            return False
        if any(
            now - analyses[horizon].as_of > self._policy.max_ages[horizon]
            for horizon in required
        ):
            return False
        long_term = analyses[AnalysisHorizon.LONG_TERM]
        dilution = analyses[AnalysisHorizon.DILUTION]
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        return (
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict
            not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and dilution.verdict is not AnalysisVerdict.AVOID
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict is AnalysisVerdict.FAVORABLE
            and _metrics(swing).get("classification") in _SWING_CONFIRMATIONS
            and intraday.direction is PatternDirection.BULLISH
            and intraday.verdict is AnalysisVerdict.FAVORABLE
        )

    @staticmethod
    def _invalidation_reason(
        watch: EntryWatch, *, result: AnalysisResult, current_price: Decimal
    ) -> str | None:
        if result.horizon is AnalysisHorizon.DILUTION and result.verdict is AnalysisVerdict.AVOID:
            return "dilution_veto"
        if result.horizon is AnalysisHorizon.LONG_TERM and (
            result.verdict is AnalysisVerdict.AVOID
            or result.direction is PatternDirection.BEARISH
        ):
            return "long_structure_invalidated"
        if current_price <= watch.invalidation:
            return "original_invalidation_breached"
        return None

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
