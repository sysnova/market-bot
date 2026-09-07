# pyright: reportPrivateUsage=false
"""Causal rebound confirmation and immutable paper-entry risk, independent of MACD."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from app.contracts import MarketBar, NamedValue, SwingTradeAssessment, SwingTradeMaturity

from .engine import (  # pyright: ignore[reportPrivateUsage]
    _NEW_YORK,
    _rounded,
    _session_normalized_rvol,
    _upsert_metrics,
    _validate_v11_context,
)
from .models import SwingTradeContext
from .v16 import SwingTradeEngineV16

_ENTERED = {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}


def metric(item: SwingTradeAssessment, name: str) -> object:
    return next((m.value for m in item.metrics if m.name == name), None)


class SwingTradeEngineV17(SwingTradeEngineV16):
    engine_version = "1.7.0"

    def __init__(
        self,
        *,
        rebound_reference_bars: int = 4,
        rebound_expiry_bars: int = 8,
        maximum_entry_risk_percent: Decimal = Decimal("4"),
        rebound_stop_buffer: Decimal = Decimal("0.25"),
        acceptance_failure_bars: int = 2,
        no_progress_bars: int = 8,
        minimum_progress_r: Decimal = Decimal("0.5"),
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if (
            min(
                rebound_reference_bars,
                rebound_expiry_bars,
                acceptance_failure_bars,
                no_progress_bars,
            )
            < 1
        ):
            raise ValueError("Rebound windows must be positive")
        if (
            not 0 < maximum_entry_risk_percent < 100
            or min(rebound_stop_buffer, minimum_progress_r) <= 0
        ):
            raise ValueError("Rebound risk parameters are out of range")
        self._ref = rebound_reference_bars
        self._expiry = rebound_expiry_bars
        self._risk_cap = maximum_entry_risk_percent
        self._buffer = rebound_stop_buffer
        self._failure = acceptance_failure_bars
        self._progress_bars = no_progress_bars
        self._progress_r = minimum_progress_r

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if previous is not None and (
            previous.symbol != context.symbol or previous.occurred_at >= context.as_of
        ):
            raise ValueError("Previous rebound assessment must be causal and match symbol")
        if (
            previous is not None
            and previous.engine_version == self.engine_version
            and metric(previous, "rebound_state") == "OPEN"
        ):
            _validate_v11_context(context)
            try:
                native = super().analyze(context)
            except ValueError as error:
                if str(error) not in {
                    "SwingTrade LONG impulse requires low before high",
                    "SwingTrade requires the configured completed daily window",
                }:
                    raise
                native = previous.model_copy(
                    update={
                        "occurred_at": context.as_of,
                        "current_price": context.current_price,
                        "metrics": _upsert_metrics(
                            previous,
                            NamedValue(name="rebound_geometry_unavailable", value=str(error)),
                            NamedValue(name="macd_4h_status", value="NOT_REFRESHED"),
                            NamedValue(name="macd_daily_status", value="NOT_REFRESHED"),
                        ),
                    }
                )
            return self._manage(native, previous, context)
        native = super().analyze(context)
        bars = context.confirmation_bars
        fallback = native.maturity if native.maturity not in _ENTERED else SwingTradeMaturity.ST2
        state: dict[str, object] = {
            "rebound_state": "WATCHING",
            "maximum_entry_risk_percent": self._risk_cap,
        }
        reasons = [r for r in native.reasons if not r.startswith("recovery_observation_")]
        cutoff = None
        if previous is not None and previous.engine_version == self.engine_version:
            value = metric(previous, "rebound_exit_at")
            cutoff = datetime.fromisoformat(value) if isinstance(value, str) else None
            if cutoff is not None:
                state["rebound_exit_at"] = cutoff.isoformat()
        if not bars:
            return self._finish(native, fallback, state, [*reasons, "rebound_confirmation_pending"])
        # A candidate is fixed at the touch; never move its reference with the breakout.
        for i in range(max(self._ref, len(bars) - self._expiry - 1), len(bars) - 1):
            touch = bars[i]
            window = bars[i - self._ref :]
            if cutoff is not None and touch.timestamp < cutoff:
                continue
            if not self._continuous(window):
                continue
            if not (touch.low <= native.zone_high and touch.high >= native.zone_low):
                continue
            reference = max(b.high for b in bars[i - self._ref : i])
            buffer = (
                sum((b.high - b.low for b in bars[i - self._ref : i + 1]), Decimal(0))
                / Decimal(self._ref + 1)
                * self._buffer
            )
            stop = _rounded(max(native.invalidation, touch.low - buffer))
            breakout_index = None
            for j in range(i + 1, len(bars)):
                bar = bars[j]
                if bar.low <= touch.low:
                    break
                if breakout_index is None:
                    if bar.close > reference and bar.close > bar.open:
                        rvol = _session_normalized_rvol(
                            bars[: j + 1], minimum_samples=self._minimum_rvol_samples
                        )
                        if rvol is not None and rvol >= self._minimum_intraday_rvol:
                            breakout_index = j
                    continue
                state.update(
                    rebound_state="BREAKOUT", rebound_reference=reference, operational_stop=stop
                )
                retest = (
                    bar.low <= reference + buffer and bar.close > reference and bar.close > bar.open
                )
                hour = self._hour_accepted(bars, breakout_index, j, reference)
                if not (retest or hour) or j != len(bars) - 1:
                    continue
                session_vwap = self._session_vwap(bars[: j + 1])
                if self._require_vwap_gate and (session_vwap is None or bar.close <= session_vwap):
                    reasons.append("rebound_session_vwap_pending")
                    continue
                risk = context.current_price - stop
                pct = risk / context.current_price * 100
                rr = (
                    (native.primary_target - context.current_price) / risk
                    if risk > 0
                    else Decimal(0)
                )
                state.update(
                    entry_risk_percent=_rounded(pct),
                    operational_reward_risk=_rounded(rr),
                    session_vwap=session_vwap,
                )
                if risk <= 0 or pct > self._risk_cap:
                    reasons.append("rebound_risk_limit_exceeded")
                    continue
                if (
                    rr <= self._minimum_rr
                    or context.current_price
                    > native.zone_high + native.atr14 * self._maximum_distance_atr
                    or not native.support_confluence
                ):
                    reasons.append("rebound_geometry_or_reward_risk_pending")
                    continue
                maturity = (
                    SwingTradeMaturity.ST4
                    if native.geri_confluence and metric(native, "geri_reaction_confirmed") is True
                    else SwingTradeMaturity.ST3
                )
                if not context.allow_new_entry:
                    state["rebound_state"] = "ACCEPTED_NOT_ACTIONABLE"
                    return self._finish(
                        native, fallback, state, ["rebound_bootstrap_confirmation_stale"]
                    )
                state.update(
                    rebound_state="OPEN",
                    rebound_entry_price=context.current_price,
                    rebound_entry_at=context.as_of.isoformat(),
                    rebound_bars_open=0,
                    rebound_failed_closes=0,
                    rebound_max_price=context.current_price,
                    rebound_acceptance="RETEST" if retest else "1H_CLOSE",
                )
                native = native.model_copy(
                    update={
                        "metrics": _upsert_metrics(
                            native,
                            NamedValue(
                                name="setup_id",
                                value=(
                                    f"{metric(native, 'setup_id')}:rebound:"
                                    f"{touch.timestamp.isoformat()}"
                                ),
                            ),
                        )
                    }
                )
                return self._finish(
                    native, maturity, state, ["rebound_entry_confirmed", "macd_4h_observation_only"]
                )
            if breakout_index is not None:
                state.update(
                    rebound_state="BREAKOUT", rebound_reference=reference, operational_stop=stop
                )
        return self._finish(native, fallback, state, [*reasons, "rebound_confirmation_pending"])

    @staticmethod
    def _continuous(bars: tuple[MarketBar, ...]) -> bool:
        return all(
            b.timestamp - a.timestamp == timedelta(minutes=15)
            and a.timestamp.astimezone(_NEW_YORK).date() == b.timestamp.astimezone(_NEW_YORK).date()
            for a, b in pairwise(bars)
        )

    @staticmethod
    def _session_vwap(bars: tuple[MarketBar, ...]) -> Decimal | None:
        day = bars[-1].timestamp.astimezone(_NEW_YORK).date()
        session = tuple(b for b in bars if b.timestamp.astimezone(_NEW_YORK).date() == day)
        first = session[0].timestamp.astimezone(_NEW_YORK)
        if (
            (first.hour, first.minute) != (9, 30)
            or not SwingTradeEngineV17._continuous(session)
            or any(b.vwap is None for b in session)
        ):
            return None
        volume = sum((b.volume for b in session), Decimal(0))
        return (
            sum((b.volume * b.vwap for b in session if b.vwap is not None), Decimal(0)) / volume
            if volume > 0
            else None
        )

    @staticmethod
    def _hour_accepted(
        bars: tuple[MarketBar, ...], breakout: int, end: int, level: Decimal
    ) -> bool:
        start = end - 3
        if start < breakout:
            return False
        local = bars[start].timestamp.astimezone(_NEW_YORK)
        return (
            local.minute == 30
            and local.hour in range(9, 15)
            and all(b.close > level for b in bars[start : end + 1])
        )

    def _manage(
        self,
        native: SwingTradeAssessment,
        previous: SwingTradeAssessment,
        context: SwingTradeContext,
    ) -> SwingTradeAssessment:
        state = {
            m.name: m.value
            for m in previous.metrics
            if m.name.startswith("rebound_")
            or m.name
            in {
                "operational_stop",
                "entry_risk_percent",
                "operational_reward_risk",
                "maximum_entry_risk_percent",
            }
        }
        stop = Decimal(str(state["operational_stop"]))
        entry = Decimal(str(state["rebound_entry_price"]))
        reference = Decimal(str(state["rebound_reference"]))
        maximum = Decimal(str(state["rebound_max_price"]))
        count = int(str(state["rebound_bars_open"]))
        failed = int(str(state["rebound_failed_closes"]))
        reason = None
        exit_price = context.current_price
        for bar in context.confirmation_bars:
            if bar.timestamp < previous.occurred_at:
                continue
            count += 1
            maximum = max(maximum, bar.high)
            failed = failed + 1 if bar.close < reference else 0
            if bar.low <= stop:
                reason = "rebound_stop_breached"
                exit_price = min(bar.open, stop)
            elif failed >= self._failure:
                reason = "rebound_acceptance_failed"
                exit_price = bar.close
            elif (
                count >= self._progress_bars
                and maximum - entry < (entry - stop) * self._progress_r
                and bar.close <= entry
            ):
                reason = "rebound_no_progress"
                exit_price = bar.close
            elif bar.high >= previous.primary_target:
                reason = "rebound_target_reached"
                exit_price = max(bar.open, previous.primary_target)
            if reason:
                break
        state.update(
            operational_stop=stop,
            rebound_bars_open=count,
            rebound_failed_closes=failed,
            rebound_max_price=maximum,
        )
        # Preserve original setup and targets across daily geometry changes.
        basis = native.model_copy(
            update={
                "primary_target": previous.primary_target,
                "resistance_20d": previous.resistance_20d,
                "extended_target": previous.extended_target,
                "metrics": _upsert_metrics(
                    native, NamedValue(name="setup_id", value=metric(previous, "setup_id"))
                ),
            }
        )
        if reason:
            state.update(
                rebound_state="EXITED",
                rebound_exit_at=context.as_of.isoformat(),
                rebound_exit_price=exit_price,
            )
            return self._finish(basis, None, state, ["swing_trade_rebound_exit", reason])
        return self._finish(
            basis,
            (
                SwingTradeMaturity.ST3
                if previous.maturity is SwingTradeMaturity.ST4 and not basis.geri_confluence
                else previous.maturity
            ),
            state,
            ["rebound_position_open", "macd_4h_observation_only"],
        )

    @staticmethod
    def _finish(
        native: SwingTradeAssessment,
        maturity: SwingTradeMaturity | None,
        state: dict[str, object],
        reasons: list[str],
    ) -> SwingTradeAssessment:
        if state.get("rebound_state") == "OPEN":
            state["structural_reward_risk"] = native.reward_risk
            entry = Decimal(str(state["rebound_entry_price"]))
            risk = entry - Decimal(str(state["operational_stop"]))
            native = native.model_copy(
                update={
                    "reward_risk": Decimal(str(state["operational_reward_risk"])),
                    "extended_reward_risk": _rounded((native.extended_target - entry) / risk)
                    if risk > 0
                    else Decimal(0),
                }
            )
        state["macd_4h_required"] = False
        state["swing_trade_entry_trigger_passed"] = maturity in _ENTERED
        state["recovery_quality"] = (
            "ACCEPTED_REBOUND"
            if maturity in _ENTERED
            else str(state.get("rebound_state", "WATCHING"))
        )
        metrics = _upsert_metrics(native, *(NamedValue(name=k, value=v) for k, v in state.items()))
        digest = hashlib.sha256(
            json.dumps([native.context_hash, maturity, state], default=str, sort_keys=True).encode()
        ).hexdigest()
        return native.model_copy(
            update={
                "maturity": maturity,
                "eligible": maturity is not None,
                "metrics": metrics,
                "reasons": tuple(dict.fromkeys(reasons)),
                "context_hash": f"sha256:{digest}",
            }
        )
