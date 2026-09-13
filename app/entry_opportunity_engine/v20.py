# pyright: reportPrivateUsage=false
"""Exit failed recoveries using original evidence and two completed 15m closes."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryCheckpointStatus,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntrySignal,
    EntrySignalFamily,
    MarketBar,
    PatternDirection,
)
from app.contracts.entry_opportunity import RecoveryExitState

from .engine import _close_checkpoint, _close_leg
from .v18 import _same_entry
from .v19 import EntryOpportunityEngineV19


class EntryOpportunityEngineV20(EntryOpportunityEngineV19):
    engine_version = "20.0.0"

    @staticmethod
    def _capture_entry_evidence(
        opportunity: EntryOpportunity, signal: EntrySignal
    ) -> EntryOpportunity:
        analyses = tuple(
            a
            for a in signal.entry_analyses
            if a.symbol == signal.symbol and a.as_of <= signal.created_at
        )
        recovery = _entry_recovery(signal, analyses)
        checkpoints = tuple(
            cp.model_copy(update={"entry_analyses": analyses, "recovery_exit": recovery})
            if cp.signal_family is signal.family
            and cp.setup_id == signal.setup_id
            and cp.reached_at == signal.created_at
            and not cp.entry_analyses
            and cp.status is EntryCheckpointStatus.OPEN
            else cp
            for cp in opportunity.checkpoints
        )
        return opportunity.model_copy(update={"checkpoints": checkpoints})

    @staticmethod
    def _mark_checkpoint(
        checkpoint: EntryMaturityCheckpoint, bar: MarketBar
    ) -> EntryMaturityCheckpoint:
        cp = checkpoint
        state = cp.recovery_exit
        if cp.status is EntryCheckpointStatus.CLOSED or bar.timestamp <= cp.reached_at:
            return cp
        if (
            state is not None
            and state.pending_exit_at is not None
            and bar.timestamp >= state.pending_exit_at
        ):
            # A pending decision executes at the next observed open, before this
            # bar's later OHLC. Existing stop/target gaps retain their exit reason.
            at_open = bar.model_copy(update={"high": bar.open, "low": bar.open, "close": bar.open})
            marked = EntryOpportunityEngineV19._mark_checkpoint(cp, at_open)
            if marked.status is EntryCheckpointStatus.CLOSED:
                return marked
            return _close_checkpoint(
                cp.model_copy(
                    update={
                        "highest_price": max(cp.highest_price, bar.open),
                        "lowest_price": min(cp.lowest_price, bar.open),
                    }
                ),
                price=bar.open,
                now=bar.timestamp,
                outcome=EntryLegStatus.RECOVERY_FAILED,
            )
        marked = EntryOpportunityEngineV19._mark_checkpoint(cp, bar)
        if state is None or marked.status is EntryCheckpointStatus.CLOSED:
            return marked
        return marked.model_copy(update={"recovery_exit": _observe(state, bar)})

    def _mark_legs_for_bar(
        self, opportunity: EntryOpportunity, bar: MarketBar
    ) -> tuple[EntryHorizonLeg, ...]:
        # Resolve pending exits before marking the next candle's later extrema.
        pending = [
            (cp, self._mark_checkpoint(cp, bar))
            for cp in opportunity.checkpoints
            if cp.status is EntryCheckpointStatus.OPEN
            and cp.recovery_exit is not None
            and cp.recovery_exit.pending_exit_at is not None
            and bar.timestamp >= cp.recovery_exit.pending_exit_at
        ]
        legs = list(opportunity.legs)
        for index, leg in enumerate(legs):
            for before, after in pending:
                if _same_entry(opportunity, before, leg) and leg.status is EntryLegStatus.OPEN:
                    assert after.exit_price is not None and after.outcome is not None
                    legs[index] = _close_leg(
                        leg, price=after.exit_price, now=bar.timestamp, status=after.outcome
                    )
                    break
        return super()._mark_legs_for_bar(opportunity.model_copy(update={"legs": tuple(legs)}), bar)

    @staticmethod
    def _protection_reasons(
        before: tuple[EntryMaturityCheckpoint, ...], after: tuple[EntryMaturityCheckpoint, ...]
    ) -> list[str]:
        reasons = EntryOpportunityEngineV19._protection_reasons(before, after)
        prior = {cp.checkpoint_id: cp.recovery_exit for cp in before}
        for cp in after:
            state, old = cp.recovery_exit, prior.get(cp.checkpoint_id)
            if state is None or cp.status is EntryCheckpointStatus.CLOSED:
                continue
            if state.pending_exit_at is not None and (old is None or old.pending_exit_at is None):
                reasons.append("recovery_failure_exit_pending")
            elif state.previous_failed_close is not None and (
                old is None or old.previous_failed_close is None
            ):
                reasons.append("recovery_failure_warning")
            elif (
                state.previous_failed_close is None
                and old is not None
                and old.previous_failed_close is not None
            ):
                reasons.append("recovery_failure_confirmation_reset")
        return list(dict.fromkeys(reasons))


def _entry_recovery(
    signal: EntrySignal, analyses: tuple[AnalysisResult, ...]
) -> RecoveryExitState | None:
    if signal.family is not EntrySignalFamily.CORE_RECOVERY or signal.maturity not in {
        EntryMaturityLevel.L1,
        EntryMaturityLevel.L2,
        EntryMaturityLevel.L3,
        EntryMaturityLevel.L4,
    }:
        return None
    for a in analyses:
        m = {v.name: v.value for v in a.metrics}
        if not (
            a.engine_id == "swing"
            and a.horizon is AnalysisHorizon.SWING
            and a.verdict is AnalysisVerdict.FAVORABLE
            and a.direction is PatternDirection.BULLISH
            and m.get("classification") == "recovery"
            and m.get("entry_lane") == "STRUCTURE_RECOVERY"
            and m.get("swing_entry_gate_passed") is True
            and m.get("recovery_entry_gate_passed") is True
            and m.get("recovery_setup_id") == signal.setup_id
            and timedelta(0) <= signal.created_at - a.as_of <= timedelta(minutes=45)
        ):
            continue
        try:
            avwap, breakout, rebound, reaction = (
                Decimal(str(m[k]))
                for k in (
                    "recovery_avwap",
                    "recovery_breakout_level",
                    "recovery_intraday_rebound_low",
                    "recovery_reaction_low",
                )
            )
            pivot = datetime.fromisoformat(str(m["recovery_pivot_at"]))
            if not all(v.is_finite() and v > 0 for v in (avwap, breakout, rebound, reaction)):
                continue
            if pivot.tzinfo is None or pivot > a.as_of or max(avwap, breakout) > signal.entry_price:
                continue
            return RecoveryExitState(
                analysis_id=a.analysis_id,
                evidence_at=a.as_of,
                pivot_at=pivot,
                avwap=avwap,
                breakout_level=breakout,
                rebound_low=rebound,
                reaction_low=reaction,
            )
        except KeyError, ValueError, ArithmeticError:
            continue
    return None


def _observe(state: RecoveryExitState, bar: MarketBar) -> RecoveryExitState:
    if state.last_bar_at is not None and bar.timestamp <= state.last_bar_at:
        return state
    bucket = bar.timestamp.replace(minute=bar.timestamp.minute // 15 * 15, second=0, microsecond=0)
    if bucket != state.bucket_at:
        count = 1 if bar.timestamp == bucket else 0
    elif state.bar_count and state.last_bar_at == bar.timestamp - timedelta(minutes=1):
        count = state.bar_count + 1
    else:
        count = 0
    updated = state.model_copy(
        update={"bucket_at": bucket, "last_bar_at": bar.timestamp, "bar_count": count}
    )
    if bar.timestamp != bucket + timedelta(minutes=14):
        return updated
    if count != 15 or bar.close >= min(state.avwap, state.breakout_level):
        return updated.model_copy(
            update={"previous_failed_close": None, "previous_failed_bucket": None}
        )
    pending = (
        state.previous_failed_bucket == bucket - timedelta(minutes=15)
        and state.previous_failed_close is not None
        and bar.close < state.previous_failed_close
    )
    return updated.model_copy(
        update={
            "previous_failed_close": bar.close,
            "previous_failed_bucket": bucket,
            "pending_exit_at": bar.timestamp + timedelta(minutes=1) if pending else None,
        }
    )
