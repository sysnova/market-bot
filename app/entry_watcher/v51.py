"""Durable Entry Watcher v5 checkpointing while preserving v5 for replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    EntryWatchStatus,
    EntryWatchTransition,
    new_uuid7,
)

from .engine import EntryWatcherPolicy
from .models import EntryWatch
from .ports import EntryWatchStore
from .v2 import EntryWatcherV2
from .v5 import EntryWatcherV5

_DECISION_STATE_KEY = "entry_watcher_decision_state"


class EntryWatcherV51(EntryWatcherV5):
    """Persist current evidence and reconfirmation candidates in the active watch."""

    engine_version = "5.1.0"

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
    ) -> None:
        super().__init__(
            store=store,
            policy=policy,
            id_factory=id_factory,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            no_retest_higher_low_enabled=no_retest_higher_low_enabled,
        )
        if not zone_exit_buffer_percent.is_finite() or zone_exit_buffer_percent < Decimal("0"):
            raise ValueError("zone exit buffer percent must be finite and non-negative")
        self._zone_exit_buffer_percent = zone_exit_buffer_percent

    def _left_target_zone(self, watch: EntryWatch, *, current_price: Decimal) -> bool:
        exit_level = watch.zone_high * (
            Decimal("1") + self._zone_exit_buffer_percent / Decimal("100")
        )
        return current_price > exit_level

    async def ingest(self, result: AnalysisResult, *, now: datetime) -> EntryWatchTransition | None:
        active = await self._store.load_active(result.symbol)
        if active is not None:
            self._restore_decision_state(active.anchor_snapshot)
        if result.horizon is AnalysisHorizon.LONG_TERM and active is None:
            previous = await self._store.load_latest(result.symbol)
            if (
                previous is not None
                and previous.status is EntryWatchStatus.TRIGGERED
                and now - previous.updated_at < self._policy.trigger_rearm_cooldown
            ):
                return None
        if result.horizon is AnalysisHorizon.INTRADAY:
            self._observe_mature_candidate(result)
        transition = await EntryWatcherV2.ingest(self, result, now=now)
        if transition is not None and transition.status not in {
            EntryWatchStatus.ARMED,
            EntryWatchStatus.IN_ZONE,
        }:
            self._mature_candidates.pop(result.symbol, None)
            self._reconfirmed_ids.difference_update(transition.source_analysis_ids)
            return transition
        await self._persist_decision_state(result.symbol)
        return transition

    def _restore_decision_state(self, anchor_snapshot: dict[str, object]) -> None:
        raw_state_value = anchor_snapshot.get(_DECISION_STATE_KEY)
        if not isinstance(raw_state_value, dict):
            return
        raw_state = cast("dict[str, object]", raw_state_value)
        if raw_state.get("schema_version") != "1.0.0":
            return
        raw_analyses = raw_state.get("latest_analyses", ())
        if isinstance(raw_analyses, list):
            for raw_analysis in cast("list[object]", raw_analyses):
                try:
                    restored = AnalysisResult.model_validate(raw_analysis, strict=False)
                except (TypeError, ValueError):
                    continue
                latest = self._latest.setdefault(restored.symbol, {})
                existing = latest.get(restored.horizon)
                if existing is None or restored.as_of >= existing.as_of:
                    latest[restored.horizon] = restored
        raw_candidate = raw_state.get("mature_candidate")
        if isinstance(raw_candidate, dict):
            candidate = cast("dict[str, object]", raw_candidate)
            try:
                symbol = str(candidate["symbol"]).strip().upper()
                self._mature_candidates[symbol] = (
                    UUID(str(candidate["analysis_id"])),
                    datetime.fromisoformat(str(candidate["observed_at"])),
                )
            except (KeyError, TypeError, ValueError):
                pass
        raw_reconfirmed = raw_state.get("reconfirmed_analysis_ids", ())
        if isinstance(raw_reconfirmed, list):
            for value in cast("list[object]", raw_reconfirmed):
                try:
                    self._reconfirmed_ids.add(UUID(str(value)))
                except ValueError:
                    continue

    async def _persist_decision_state(self, symbol: str) -> None:
        active = await self._store.load_active(symbol)
        if active is None:
            return
        latest = self._latest.get(active.symbol, {})
        candidate = self._mature_candidates.get(active.symbol)
        state: dict[str, object] = {
            "schema_version": "1.0.0",
            "engine_version": self.engine_version,
            "latest_analyses": [
                latest[horizon].model_dump(mode="json")
                for horizon in AnalysisHorizon
                if horizon in latest
            ],
            "mature_candidate": (
                {
                    "symbol": active.symbol,
                    "analysis_id": str(candidate[0]),
                    "observed_at": candidate[1].isoformat(),
                }
                if candidate is not None
                else None
            ),
            "reconfirmed_analysis_ids": sorted(str(value) for value in self._reconfirmed_ids),
        }
        snapshot = dict(active.anchor_snapshot)
        snapshot[_DECISION_STATE_KEY] = state
        await self._store.update_anchor_snapshot(
            active.model_copy(update={"anchor_snapshot": snapshot})
        )
