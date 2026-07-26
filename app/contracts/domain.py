"""Decision, market candidate, trade, alert and health contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    NonNegativeDecimal,
    PositiveDecimal,
    SemVer,
    Sha256,
    StrictFrozenModel,
    UnitInterval,
)
from .enums import (
    AlertSeverity,
    DecisionOutcome,
    MarketSession,
    PatternDirection,
    RuleTraceStatus,
    ServiceStatus,
    TradeSide,
)
from .rules import NamedValue, RuleResult


class RuleTraceStep(StrictFrozenModel):
    step_id: Identifier
    rule_id: Identifier
    status: RuleTraceStatus
    started_at: datetime
    completed_at: datetime
    result: RuleResult | None = None
    skipped_dependencies: tuple[Identifier, ...] = ()
    message: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_trace_step(self) -> RuleTraceStep:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        is_skipped = self.status is RuleTraceStatus.SKIPPED_DEPENDENCY
        if is_skipped and not self.skipped_dependencies:
            raise ValueError("SKIPPED_DEPENDENCY requires skipped_dependencies")
        if not is_skipped and self.skipped_dependencies:
            raise ValueError("skipped_dependencies is only valid for SKIPPED_DEPENDENCY")
        if self.result is not None and self.result.rule_id != self.rule_id:
            raise ValueError("trace rule_id must match result rule_id")
        return self


class DecisionTrace(StrictFrozenModel):
    trace_id: UUID
    correlation_id: UUID | None = None
    strategy_id: Identifier
    strategy_version: SemVer
    compiled_strategy_hash: Sha256 | None = None
    symbol: Identifier
    started_at: datetime
    completed_at: datetime
    outcome: DecisionOutcome
    score: UnitInterval | None = None
    steps: tuple[RuleTraceStep, ...] = ()
    reasons: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_trace(self) -> DecisionTrace:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("decision trace step ids must be unique")
        outside_trace = any(
            step.started_at < self.started_at or step.completed_at > self.completed_at
            for step in self.steps
        )
        if outside_trace:
            raise ValueError("rule trace steps must fall within the decision trace interval")
        return self


class PriceLevel(StrictFrozenModel):
    label: Identifier
    price: PositiveDecimal
    strength: UnitInterval | None = None
    touched_at: datetime | None = None


class PatternEvidence(StrictFrozenModel):
    kind: Identifier
    description: NonEmptyStr
    score: UnitInterval | None = None
    observed_at: datetime | None = None


class PatternCandidate(StrictFrozenModel):
    candidate_id: UUID
    pattern: Identifier
    detector: Identifier
    detector_version: SemVer | None = None
    detector_hash: Sha256 | None = None
    symbol: Identifier
    venue: Identifier | None = None
    timeframe: Identifier
    direction: PatternDirection
    market_session: MarketSession | None = None
    detected_at: datetime
    window_start: datetime
    window_end: datetime
    confidence: UnitInterval
    quality_score: UnitInterval | None = None
    reference_price: PositiveDecimal | None = None
    levels: tuple[PriceLevel, ...] = ()
    evidence: tuple[PatternEvidence, ...] = ()
    features: tuple[NamedValue, ...] = ()
    invalidation_price: PositiveDecimal | None = None
    expires_at: datetime | None = None
    tags: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_candidate(self) -> PatternCandidate:
        if self.window_end < self.window_start:
            raise ValueError("window_end cannot precede window_start")
        if not self.window_start <= self.detected_at:
            raise ValueError("detected_at cannot precede window_start")
        if self.expires_at is not None and self.expires_at <= self.detected_at:
            raise ValueError("expires_at must be later than detected_at")
        labels = [level.label for level in self.levels]
        if len(labels) != len(set(labels)):
            raise ValueError("price level labels must be unique")
        feature_names = [feature.name for feature in self.features]
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("pattern feature names must be unique")
        return self


class TradePlan(StrictFrozenModel):
    plan_id: UUID
    candidate_id: UUID | None = None
    symbol: Identifier
    venue: Identifier | None = None
    side: TradeSide
    created_at: datetime
    valid_until: datetime
    entry: PositiveDecimal
    stop_loss: PositiveDecimal
    take_profits: tuple[PositiveDecimal, ...]
    quantity: PositiveDecimal
    risk_amount: PositiveDecimal
    reward_risk_ratio: PositiveDecimal
    max_slippage_bps: NonNegativeDecimal = Decimal("0")
    rationale: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_plan(self) -> TradePlan:
        if self.valid_until <= self.created_at:
            raise ValueError("valid_until must be later than created_at")
        if not self.take_profits:
            raise ValueError("at least one take profit is required")
        if self.side is TradeSide.LONG:
            if self.stop_loss >= self.entry:
                raise ValueError("LONG stop_loss must be below entry")
            if any(target <= self.entry for target in self.take_profits):
                raise ValueError("LONG take profits must be above entry")
            if tuple(sorted(self.take_profits)) != self.take_profits:
                raise ValueError("LONG take profits must be ordered ascending")
        else:
            if self.stop_loss <= self.entry:
                raise ValueError("SHORT stop_loss must be above entry")
            if any(target >= self.entry for target in self.take_profits):
                raise ValueError("SHORT take profits must be below entry")
            if tuple(sorted(self.take_profits, reverse=True)) != self.take_profits:
                raise ValueError("SHORT take profits must be ordered descending")
        return self


class AlertPolicy(StrictFrozenModel):
    policy_id: Identifier
    enabled: bool = True
    min_confidence: UnitInterval
    min_reward_risk_ratio: PositiveDecimal | None = None
    allowed_sessions: tuple[MarketSession, ...] = ()
    allowed_directions: tuple[PatternDirection, ...] = ()
    cooldown: timedelta = Field(default=timedelta(0), ge=timedelta(0))
    channels: tuple[Identifier, ...] = ()
    severity: AlertSeverity = AlertSeverity.WATCH
    deduplication_window: timedelta = Field(default=timedelta(minutes=5), ge=timedelta(0))

    @model_validator(mode="after")
    def validate_policy(self) -> AlertPolicy:
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("alert channels must be unique")
        if len(self.allowed_sessions) != len(set(self.allowed_sessions)):
            raise ValueError("allowed sessions must be unique")
        return self


class AlertDecision(StrictFrozenModel):
    decision_id: UUID
    policy_id: Identifier
    candidate_id: UUID | None = None
    trade_plan_id: UUID | None = None
    should_alert: bool
    decided_at: datetime
    severity: AlertSeverity = AlertSeverity.WATCH
    channels: tuple[Identifier, ...] = ()
    reasons: tuple[NonEmptyStr, ...]
    deduplication_key: NonEmptyStr | None = None
    suppressed_until: datetime | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> AlertDecision:
        if not self.reasons:
            raise ValueError("an alert decision requires at least one reason")
        if self.should_alert and not self.channels:
            raise ValueError("an alerting decision requires at least one channel")
        if not self.should_alert and self.channels:
            raise ValueError("a suppressed decision cannot have delivery channels")
        if self.suppressed_until is not None and self.suppressed_until < self.decided_at:
            raise ValueError("suppressed_until cannot precede decided_at")
        return self


class DependencyHealth(StrictFrozenModel):
    name: Identifier
    status: ServiceStatus
    latency_ms: NonNegativeDecimal | None = None
    checked_at: datetime | None = None
    message: NonEmptyStr | None = None


class ServiceHealth(StrictFrozenModel):
    service: Identifier
    status: ServiceStatus
    observed_at: datetime
    version: SemVer | None = None
    uptime_seconds: NonNegativeDecimal | None = None
    dependencies: tuple[DependencyHealth, ...] = ()
    details: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def validate_health(self) -> ServiceHealth:
        dependency_names = [item.name for item in self.dependencies]
        if len(dependency_names) != len(set(dependency_names)):
            raise ValueError("dependency names must be unique")
        detail_names = [item.name for item in self.details]
        if len(detail_names) != len(set(detail_names)):
            raise ValueError("health detail names must be unique")
        return self
