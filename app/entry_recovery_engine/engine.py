"""Reconfirm a still-valid higher-horizon thesis after a tactical invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EntrySignal,
    EntrySignalFamily,
    MarketBar,
    PatternDirection,
)


@dataclass(frozen=True, slots=True)
class EntryRecoveryPolicy:
    """Independently versioned policy for paper-entry recovery."""

    version: str = "1.0.0"
    intraday_max_age: timedelta = timedelta(minutes=15)
    swing_max_age: timedelta = timedelta(days=3)
    minimum_reward_risk: Decimal = Decimal("1.5")
    require_strong_confirmation: bool = True
    require_five_minute_higher_low: bool = True


class EntryRecoveryEngine:
    """Emit an analytical recovery signal; never place or size an order."""

    engine_id = "entry-recovery"
    engine_version = "1.0.0"

    def __init__(self, policy: EntryRecoveryPolicy | None = None) -> None:
        self._policy = policy or EntryRecoveryPolicy()
        self._opportunities: dict[str, EntryOpportunityEvent] = {}
        self._analyses: dict[str, dict[AnalysisHorizon, AnalysisResult]] = {}
        self._emitted: set[UUID] = set()

    def ingest_opportunity(self, event: EntryOpportunityEvent) -> None:
        opportunity = event.opportunity
        if opportunity.status is EntryOpportunityStatus.CLOSED:
            self._opportunities.pop(opportunity.symbol, None)
            return
        if not any(leg.status is EntryLegStatus.INVALIDATED for leg in opportunity.legs):
            return
        if not any(leg.status is EntryLegStatus.OPEN for leg in opportunity.legs):
            return
        existing = self._opportunities.get(opportunity.symbol)
        if existing is None or event.occurred_at >= existing.occurred_at:
            self._opportunities[opportunity.symbol] = event

    def ingest_analysis(self, result: AnalysisResult) -> None:
        if result.horizon not in {AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY}:
            return
        current = self._analyses.setdefault(result.symbol, {}).get(result.horizon)
        if current is None or result.as_of >= current.as_of:
            self._analyses[result.symbol][result.horizon] = result

    def ingest_bar(self, bar: MarketBar) -> EntrySignal | None:
        if bar.timeframe is not BarTimeframe.MINUTE_1 or not bar.is_final:
            return None
        event = self._opportunities.get(bar.symbol)
        if event is None or event.opportunity.opportunity_id in self._emitted:
            return None
        analyses = self._analyses.get(bar.symbol, {})
        swing = analyses.get(AnalysisHorizon.SWING)
        intraday = analyses.get(AnalysisHorizon.INTRADAY)
        if not self._analysis_is_valid(swing, bar=bar, max_age=self._policy.swing_max_age):
            return None
        if not self._analysis_is_valid(
            intraday,
            bar=bar,
            max_age=self._policy.intraday_max_age,
        ):
            return None
        assert swing is not None and intraday is not None
        metrics = {item.name: item.value for item in intraday.metrics}
        if (
            self._policy.require_strong_confirmation
            and metrics.get("confirmation_quality") != "strong"
        ):
            return None
        if (
            self._policy.require_five_minute_higher_low
            and metrics.get("five_minute_higher_low") is not True
        ):
            return None

        invalidated = [
            leg
            for leg in event.opportunity.legs
            if leg.status is EntryLegStatus.INVALIDATED and leg.entry_price is not None
        ]
        continuing = [
            leg
            for leg in event.opportunity.legs
            if leg.status is EntryLegStatus.OPEN and leg.target is not None
        ]
        if not invalidated or not continuing:
            return None
        reclaim_price = max(leg.entry_price for leg in invalidated if leg.entry_price is not None)
        if bar.close < reclaim_price:
            return None
        plan = max(continuing, key=lambda leg: leg.target or Decimal())
        target = plan.target
        if target is None or target <= bar.close or plan.invalidation >= bar.close:
            return None
        reward_risk = (target - bar.close) / (bar.close - plan.invalidation)
        if reward_risk < self._policy.minimum_reward_risk:
            return None

        self._emitted.add(event.opportunity.opportunity_id)
        return EntrySignal(
            family=EntrySignalFamily.CORE_RECOVERY,
            maturity=EntryMaturityLevel.L4,
            symbol=bar.symbol,
            created_at=bar.timestamp,
            setup_id=f"recovery:{event.opportunity.opportunity_id}",
            entry_price=bar.close,
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            zone_low=reclaim_price,
            zone_high=max(reclaim_price, bar.close),
            invalidation=plan.invalidation,
            targets=(target,),
            policy_id="core-recovery",
            policy_version=self._policy.version,
            reasons=(
                "tactical_invalidation_recovered",
                "higher_horizon_thesis_remains_open",
                "fresh_intraday_higher_low",
                "minimum_reward_risk_passed",
            ),
            source_event_ids=(event.event_id, swing.analysis_id, intraday.analysis_id),
        )

    @staticmethod
    def _analysis_is_valid(
        result: AnalysisResult | None,
        *,
        bar: MarketBar,
        max_age: timedelta,
    ) -> bool:
        return bool(
            result is not None
            and result.as_of <= bar.timestamp
            and bar.timestamp - result.as_of <= max_age
            and result.direction is PatternDirection.BULLISH
            and result.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
            and _metric(result, "setup")
            in {
                "bullish_breakout",
                "bullish_vwap_reclaim",
                "bullish_entry_confirmation",
            }
        )


def _metric(result: AnalysisResult, name: str) -> object:
    return next((item.value for item in result.metrics if item.name == name), None)
