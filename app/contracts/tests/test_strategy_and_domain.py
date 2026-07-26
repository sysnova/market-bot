from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts import (
    AlertDecision,
    CompiledStrategy,
    DecisionOutcome,
    DecisionTrace,
    DependencyHealth,
    MarketSession,
    PatternCandidate,
    PatternDirection,
    PipelineStep,
    PriceLevel,
    RuleBinding,
    RuleLifecycleStatus,
    RuleMetadata,
    RulePackManifest,
    RuleTraceStatus,
    RuleTraceStep,
    RuleType,
    ScoringPolicy,
    ScoringWeight,
    ServiceHealth,
    ServiceStatus,
    StrategyMode,
    StrategyPolicies,
    StrategySpec,
    TradePlan,
    TradeSide,
    validate_primary_uniqueness,
)

NOW = datetime(2026, 7, 25, 18, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def metadata(version: str = "1.0.0") -> RuleMetadata:
    return RuleMetadata(
        rule_id="trend.confirm",
        name="Trend",
        version=version,
        rule_type=RuleType.CONFIRMATION,
        lifecycle_status=RuleLifecycleStatus.APPROVED,
        description="Confirms trend.",
        implementation_hash=HASH,
        created_at=NOW,
    )


def spec(
    mode: StrategyMode = StrategyMode.PRIMARY, rule_version: str = "1.0.0"
) -> StrategySpec:
    return StrategySpec(
        strategy_id="trend-breakout",
        version="2.0.0",
        family="equities",
        engine="technical-v1",
        run_id="daily-us",
        mode=mode,
        rule_pack_hash=HASH,
        pipeline=(
            PipelineStep(
                step_id="confirm", rule_id="trend.confirm", rule_version=rule_version
            ),
        ),
        bindings=(RuleBinding(rule_id="trend.confirm"),),
        policies=StrategyPolicies(max_candidate_age=timedelta(minutes=15)),
        scoring=ScoringPolicy(
            pass_threshold=Decimal("0.7"),
            weights=(ScoringWeight(rule_id="trend.confirm", weight=Decimal("1")),),
        ),
    )


def test_primary_uniqueness_is_scoped_by_family_engine_and_run() -> None:
    validate_primary_uniqueness((spec(), spec(StrategyMode.SHADOW)))
    with pytest.raises(ValueError, match="PRIMARY"):
        validate_primary_uniqueness((spec(), spec()))


def test_pipeline_step_requires_an_exact_rule_version() -> None:
    with pytest.raises(ValidationError, match="rule_version"):
        PipelineStep(step_id="confirm", rule_id="trend.confirm")


def test_pipeline_rejects_duplicate_rule_ids_even_with_distinct_versions() -> None:
    with pytest.raises(ValidationError, match="pipeline rule ids must be unique"):
        StrategySpec(
            strategy_id="duplicate-rule",
            version="1.0.0",
            family="equities",
            engine="technical-v1",
            run_id="daily-us",
            mode=StrategyMode.RESEARCH,
            rule_pack_hash=HASH,
            pipeline=(
                PipelineStep(
                    step_id="confirm-v1",
                    rule_id="trend.confirm",
                    rule_version="1.0.0",
                ),
                PipelineStep(
                    step_id="confirm-v2",
                    rule_id="trend.confirm",
                    rule_version="2.0.0",
                ),
            ),
            bindings=(RuleBinding(rule_id="trend.confirm"),),
            policies=StrategyPolicies(),
            scoring=ScoringPolicy(
                pass_threshold=Decimal("0.5"),
                weights=(ScoringWeight(rule_id="trend.confirm", weight=Decimal("1")),),
            ),
        )


def test_pipeline_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown pipeline dependencies"):
        StrategySpec(
            strategy_id="bad",
            version="1.0.0",
            family="equities",
            engine="technical-v1",
            run_id="daily-us",
            mode=StrategyMode.RESEARCH,
            rule_pack_hash=HASH,
            pipeline=(
                PipelineStep(
                    step_id="confirm",
                    rule_id="trend.confirm",
                    rule_version="1.0.0",
                    depends_on=("missing",),
                ),
            ),
            bindings=(RuleBinding(rule_id="trend.confirm"),),
            policies=StrategyPolicies(),
            scoring=ScoringPolicy(
                pass_threshold=Decimal("0.5"),
                weights=(ScoringWeight(rule_id="trend.confirm", weight=Decimal("1")),),
            ),
        )


def test_manifest_allows_same_rule_id_at_distinct_versions() -> None:
    manifest = RulePackManifest(
        pack_id="ab-rules",
        version="1.0.0",
        family="equities",
        engine="technical-v1",
        manifest_hash=HASH,
        rules=(metadata("1.0.0"), metadata("2.0.0")),
        created_at=NOW,
    )
    assert tuple(rule.version for rule in manifest.rules) == ("1.0.0", "2.0.0")


def test_manifest_rejects_duplicate_rule_coordinate() -> None:
    with pytest.raises(ValidationError, match="rule coordinates in a pack must be unique"):
        RulePackManifest(
            pack_id="duplicate",
            version="1.0.0",
            family="equities",
            engine="technical-v1",
            manifest_hash=HASH,
            rules=(metadata(), metadata()),
            created_at=NOW,
        )


def test_compilation_checks_manifest_and_order() -> None:
    manifest = RulePackManifest(
        pack_id="core",
        version="1.0.0",
        family="equities",
        engine="technical-v1",
        manifest_hash=HASH,
        rules=(metadata(),),
        created_at=NOW,
    )
    compiled = CompiledStrategy(
        spec=spec(),
        rule_pack=manifest,
        compiled_hash=HASH,
        compiled_at=NOW,
        execution_order=("confirm",),
    )
    assert compiled.execution_order == ("confirm",)


def test_compilation_rejects_rule_version_absent_from_manifest() -> None:
    manifest = RulePackManifest(
        pack_id="core",
        version="1.0.0",
        family="equities",
        engine="technical-v1",
        manifest_hash=HASH,
        rules=(metadata(),),
        created_at=NOW,
    )
    with pytest.raises(ValidationError, match="exact rule coordinates"):
        CompiledStrategy(
            spec=spec(rule_version="2.0.0"),
            rule_pack=manifest,
            compiled_hash=HASH,
            compiled_at=NOW,
            execution_order=("confirm",),
        )


def test_trace_expresses_skipped_dependency() -> None:
    trace = DecisionTrace(
        trace_id=uuid4(),
        strategy_id="trend-breakout",
        strategy_version="2.0.0",
        symbol="AAPL",
        started_at=NOW,
        completed_at=NOW,
        outcome=DecisionOutcome.REJECTED,
        steps=(
            RuleTraceStep(
                step_id="confirm",
                rule_id="trend.confirm",
                status=RuleTraceStatus.SKIPPED_DEPENDENCY,
                started_at=NOW,
                completed_at=NOW,
                skipped_dependencies=("volume",),
            ),
        ),
    )
    assert trace.steps[0].status is RuleTraceStatus.SKIPPED_DEPENDENCY


def test_pattern_trade_alert_and_health_models() -> None:
    candidate = PatternCandidate(
        candidate_id=uuid4(),
        pattern="bull-flag",
        detector="patterns-v2",
        symbol="AAPL",
        timeframe="1h",
        direction=PatternDirection.BULLISH,
        market_session=MarketSession.REGULAR,
        detected_at=NOW,
        window_start=NOW - timedelta(hours=8),
        window_end=NOW,
        confidence=Decimal("0.86"),
        levels=(PriceLevel(label="breakout", price=Decimal("215.25")),),
    )
    plan = TradePlan(
        plan_id=uuid4(),
        candidate_id=candidate.candidate_id,
        symbol="AAPL",
        side=TradeSide.LONG,
        created_at=NOW,
        valid_until=NOW + timedelta(hours=2),
        entry=Decimal("215.25"),
        stop_loss=Decimal("211"),
        take_profits=(Decimal("223.75"),),
        quantity=Decimal("10"),
        risk_amount=Decimal("42.50"),
        reward_risk_ratio=Decimal("2"),
    )
    alert = AlertDecision(
        decision_id=uuid4(),
        policy_id="desk",
        candidate_id=candidate.candidate_id,
        trade_plan_id=plan.plan_id,
        should_alert=True,
        decided_at=NOW,
        channels=("telegram",),
        reasons=("confidence threshold met",),
    )
    health = ServiceHealth(
        service="scanner",
        status=ServiceStatus.DEGRADED,
        observed_at=NOW,
        dependencies=(DependencyHealth(name="market-data", status=ServiceStatus.UNHEALTHY),),
    )
    assert alert.should_alert and health.status is ServiceStatus.DEGRADED

    with pytest.raises(ValidationError, match="stop_loss"):
        TradePlan(
            plan_id=uuid4(),
            symbol="AAPL",
            side=TradeSide.LONG,
            created_at=NOW,
            valid_until=NOW + timedelta(hours=1),
            entry=Decimal("215"),
            stop_loss=Decimal("220"),
            take_profits=(Decimal("225"),),
            quantity=Decimal("1"),
            risk_amount=Decimal("5"),
            reward_risk_ratio=Decimal("2"),
        )
