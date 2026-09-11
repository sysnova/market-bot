"""Daily EMA21/SMA50 are mandatory first objectives for structural recovery."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import cast

from app.contracts import GeriMaturity, MarketBar, NamedValue, TradeSide

from .daily_recovery_levels import (
    average_snapshot,
    average_state,
    same_slot_rvol,
    structural_target,
)
from .engine import _duration_normalized_atr  # pyright: ignore[reportPrivateUsage]
from .models import Swing4HGeriContext
from .recovery_progression import progressive_recovery_metrics
from .tactical_levels import completed_at, directional_rr, obstacles, to_metrics
from .v19 import Swing4HGeriEngineV19


class Swing4HGeriEngineV111(Swing4HGeriEngineV19):
    engine_version = "1.11.0"

    def __init__(
        self,
        *,
        recovery_minimum_rvol: Decimal = Decimal("1.2"),
        recovery_rvol_sessions: int = 5,
        recovery_resistance_sessions: int = 20,
        structural_rebase_atr: Decimal = Decimal("3.00"),
        recovery_stop_atr: Decimal = Decimal("0.10"),
        recovery_maximum_risk_percent: Decimal = Decimal("4"),
        recovery_acceptance_bars: int = 1,
        short_minimum_rvol: Decimal = Decimal("1.5"),
        short_rvol_sessions: int = 5,
        **kwargs: object,
    ) -> None:
        super().__init__(
            structural_rebase_atr=structural_rebase_atr,
            recovery_stop_atr=recovery_stop_atr,
            recovery_maximum_risk_percent=recovery_maximum_risk_percent,
            recovery_acceptance_bars=recovery_acceptance_bars,
            short_minimum_rvol=short_minimum_rvol,
            short_rvol_sessions=short_rvol_sessions,
            **kwargs,
        )
        if (
            recovery_minimum_rvol <= 0
            or min(recovery_rvol_sessions, recovery_resistance_sessions) < 1
        ):
            raise ValueError("recovery volume and resistance settings must be positive")
        self._minimum_rvol = recovery_minimum_rvol
        self._rvol_sessions = recovery_rvol_sessions
        self._resistance_sessions = recovery_resistance_sessions

    def _target(self, context: Swing4HGeriContext, price: Decimal, at: datetime) -> Decimal | None:
        snapshot = average_snapshot(context.daily_bars, at)
        if snapshot is None:
            return None
        levels = [v for v in (snapshot.ema21, snapshot.sma50) if v > price]
        horizontal = structural_target(context, price, at, self._resistance_sessions)
        if horizontal is not None:
            levels.append(horizontal)
        return min(levels) if levels else None

    def _recovery_metrics(self, context: Swing4HGeriContext) -> tuple[NamedValue, ...]:
        prior = context.active_structure
        if prior is not None and prior.engine_version == self.engine_version:
            prior = prior.model_copy(update={"engine_version": "1.10.0"})
        m = {
            v.name: v.value
            for v in progressive_recovery_metrics(
                replace(context, active_structure=prior),
                self._recovery,
                target_selector=self._target,
                freeze_windows=True,
            )
        }
        at = context.current_price_at
        assert at is not None
        snapshot = average_snapshot(context.daily_bars, at)
        m.update(
            countertrend_target_policy="NEAREST_DAILY_MA_OR_RECENT_DAILY_RESISTANCE",
            countertrend_minimum_rvol=self._minimum_rvol,
            countertrend_ma_status="AVAILABLE" if snapshot is not None else "UNAVAILABLE",
        )
        reasons = [
            str(v)
            for v in cast(tuple[object, ...], m.get("countertrend_eligibility_reasons", ()))
            if v != "recovery_setup_eligible"
        ]
        entry_at = cast(datetime | None, m.get("countertrend_entry_accepted_at"))
        entry_bar = next(
            (b for b in context.confirmation_bars if completed_at(b) == entry_at), None
        )
        rvol = (
            same_slot_rvol(context.confirmation_bars, entry_bar, self._rvol_sessions)
            if entry_bar
            else None
        )
        m["countertrend_entry_rvol"] = rvol
        if entry_at is not None and (rvol is None or rvol < self._minimum_rvol):
            reasons.append(
                "recovery_volume_unavailable" if rvol is None else "recovery_volume_pending"
            )
        if snapshot is None:
            reasons.append("completed_daily_averages_missing")
        else:
            price = context.current_price
            # Use the structural engine's current ATR for display/retest tolerance only.
            # The entry's fixed stop and target remain owned by its acceptance window.
            atr = _duration_normalized_atr(context.bars)
            session = tuple(b for b in context.confirmation_bars if b.timestamp >= snapshot.as_of)

            def volume_ok(bar: MarketBar) -> bool:
                ratio = same_slot_rvol(context.confirmation_bars, bar, self._rvol_sessions)
                return ratio is not None and ratio >= self._minimum_rvol

            for name, level, slope in (
                ("ema21", snapshot.ema21, snapshot.ema21_slope),
                ("sma50", snapshot.sma50, snapshot.sma50_slope),
            ):
                state = average_state(session, level, atr * self._recovery.breakout_atr, volume_ok)
                m[f"countertrend_{name}_daily"] = level
                m[f"countertrend_{name}_slope"] = slope
                m[f"countertrend_{name}_state"] = state
            m["countertrend_ma_as_of"] = snapshot.as_of
            m["countertrend_ma_cluster"] = (
                abs(snapshot.ema21 - snapshot.sma50) <= atr * self._recovery.zone_atr
            )
            pending = sorted(v for v in (snapshot.ema21, snapshot.sma50) if v > price)
            m["countertrend_next_daily_ma"] = pending[0] if pending else None
            fixed = (
                average_snapshot(context.daily_bars, entry_at) if entry_at is not None else snapshot
            )
            target = m.get("countertrend_target")
            m["countertrend_target_source"] = (
                "EMA21_DAILY"
                if fixed is not None and target == fixed.ema21
                else "SMA50_DAILY"
                if fixed is not None and target == fixed.sma50
                else "RECENT_DAILY_PIVOT_HIGH"
                if target is not None
                else "UNAVAILABLE"
            )
            old = {v.name: v.value for v in prior.metrics} if prior is not None else {}
            old_entry = old.get("countertrend_entry_accepted_at")
            if isinstance(old_entry, str):
                old_entry = datetime.fromisoformat(old_entry)
            old_target = old.get("countertrend_target")
            if (
                entry_at is not None
                and old_entry == entry_at
                and old_target is not None
                and Decimal(str(old_target)) == target
                and old.get("countertrend_target_source") is not None
            ):
                m["countertrend_target_source"] = old["countertrend_target_source"]
            m["countertrend_target_as_of"] = fixed.as_of if fixed is not None else None
            stop = cast(Decimal | None, m.get("countertrend_invalidation"))
            m["countertrend_rr_to_next_ma"] = (
                directional_rr(
                    TradeSide.LONG,
                    price,
                    stop,
                    pending[0] if pending else None,
                )
                if stop is not None
                else None
            )
            m["countertrend_intermediate_horizontal_levels"] = tuple(
                v
                for v in sorted(
                    set(
                        (
                            *obstacles(context.bars, TradeSide.LONG),
                            *obstacles(context.daily_bars, TradeSide.LONG),
                        )
                    )
                )
                if v > price and (target is None or v < cast(Decimal, target))
            )
            # A falling daily mean may become a nearer objective after entry evaluation.
            # Do not silently move an existing window's target to improve the new-entry R/R.
            if entry_at is not None and pending and stop is not None:
                rr = directional_rr(TradeSide.LONG, price, stop, pending[0])
                if rr is None or rr <= self._recovery.minimum_rr:
                    reasons.append("insufficient_reward_risk_to_daily_ma")
        if reasons:
            m["countertrend_eligible"] = False
            if m.get("countertrend_state") != GeriMaturity.INVALIDATED:
                m["countertrend_state"] = GeriMaturity.BUILDING
            m["countertrend_eligibility_reasons"] = tuple(reasons)
        return to_metrics(m)
