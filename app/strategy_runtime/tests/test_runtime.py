from decimal import Decimal
from uuid import uuid4

from app.common.clock import FrozenClock
from app.contracts import (
    ContextValue,
    DecisionOutcome,
    DependencyPolicy,
    EvaluationContext,
    MarketSession,
    NamedValue,
    PipelineStep,
    RuleBinding,
    RuleStatus,
    ScoringPolicy,
    ScoringWeight,
    StrategyMode,
    StrategyPolicies,
    StrategySpec,
)
from app.rule_registry import Registry, RegistryProvider, RuntimeEnvironment
from app.rulepacks.synthetic_core import get_provider
from app.strategy_runtime import StrategyCompiler, StrategyRuntime, SubprocessRuleRunner
from app.strategy_runtime.tests.test_loader_compiler import NOW, build


class Audit:
    def __init__(self, confirmed: bool) -> None:
        self.confirmed = confirmed
        self.traces = []

    def confirm(self, trace: object) -> bool:
        self.traces.append(trace)
        return self.confirmed


def evaluation_context() -> EvaluationContext:
    return EvaluationContext(
        symbol="TEST",
        timeframe="1m",
        as_of=NOW,
        market_session=MarketSession.CONTINUOUS,
        trace_id=uuid4(),
    )


def synthetic_plan() -> object:
    provider = get_provider()
    registry = Registry()
    registry.register(RegistryProvider("synthetic.v1", "1", provider.manifest))
    snapshot = registry.snapshot(
        "run-chain",
        (
            "synthetic.read_number@1.0.0",
            "synthetic.multiply@1.0.0",
            "synthetic.threshold@1.0.0",
        ),
        StrategyMode.SHADOW,
        RuntimeEnvironment.LIVE,
    )
    spec = StrategySpec(
        strategy_id="dynamic-chain",
        version="1.0.0",
        family="synthetic",
        engine="reference_engine",
        run_id="run-chain",
        mode=StrategyMode.SHADOW,
        rule_pack_hash=provider.manifest.manifest_hash,
        pipeline=(
            PipelineStep(step_id="read", rule_id="synthetic.read_number", rule_version="1.0.0"),
            PipelineStep(
                step_id="multiply",
                rule_id="synthetic.multiply",
                rule_version="1.0.0",
                depends_on=("read",),
                dependency_policy=DependencyPolicy.REQUIRE_COMPLETION,
            ),
            PipelineStep(
                step_id="threshold",
                rule_id="synthetic.threshold",
                rule_version="1.0.0",
                depends_on=("multiply",),
            ),
        ),
        bindings=(
            RuleBinding(
                rule_id="synthetic.read_number",
                parameters=(NamedValue(name="source", value="seed"),),
            ),
            RuleBinding(
                rule_id="synthetic.multiply",
                parameters=(
                    NamedValue(name="value", value="${steps.read.outputs.number}"),
                    NamedValue(name="factor", value=Decimal("2")),
                ),
            ),
            RuleBinding(
                rule_id="synthetic.threshold",
                parameters=(
                    NamedValue(name="value", value="${steps.multiply.outputs.product}"),
                    NamedValue(name="minimum", value=Decimal("5")),
                ),
            ),
        ),
        policies=StrategyPolicies(stop_on_error=False, minimum_passing_rules=3),
        scoring=ScoringPolicy(
            pass_threshold=Decimal("1"),
            weights=(ScoringWeight(rule_id="synthetic.threshold", weight=Decimal("1")),),
        ),
    )
    return StrategyCompiler(clock=lambda: NOW).compile(spec, snapshot, {"synthetic.v1": provider})


def test_primary_is_eligible_only_after_audit_confirmation() -> None:
    spec, snapshot, providers, _ = build()
    plan = (
        __import__("app.strategy_runtime", fromlist=["StrategyCompiler"])
        .StrategyCompiler(clock=lambda: NOW)
        .compile(spec, snapshot, providers)
    )
    runtime = StrategyRuntime(
        runner=SubprocessRuleRunner(timeout_seconds=1), clock=FrozenClock(NOW)
    )

    rejected_audit = runtime.execute(plan, evaluation_context(), Audit(False))
    confirmed = runtime.execute(plan, evaluation_context(), Audit(True))

    assert rejected_audit.trace.outcome is DecisionOutcome.ACCEPTED
    assert not rejected_audit.eligible
    assert confirmed.eligible and confirmed.audit_confirmed
    assert tuple(step.step_id for step in confirmed.trace.steps) == ("first", "second")


def test_shadow_executes_but_is_never_action_eligible_and_disabled_does_not_execute() -> None:
    spec, snapshot, providers, _ = build()
    compiler = __import__("app.strategy_runtime", fromlist=["StrategyCompiler"]).StrategyCompiler(
        clock=lambda: NOW
    )
    shadow_plan = compiler.compile(
        spec.model_copy(update={"mode": StrategyMode.SHADOW}), snapshot, providers
    )
    disabled_plan = compiler.compile(
        spec.model_copy(update={"mode": StrategyMode.DISABLED}), snapshot, providers
    )
    runtime = StrategyRuntime(SubprocessRuleRunner(timeout_seconds=1), FrozenClock(NOW))

    shadow = runtime.execute(shadow_plan, evaluation_context(), Audit(True))
    disabled = runtime.execute(disabled_plan, evaluation_context(), Audit(True))

    assert shadow.trace.steps and not shadow.eligible
    assert disabled.trace.outcome is DecisionOutcome.NO_DECISION
    assert not disabled.trace.steps and not disabled.eligible


def test_trace_identifier_is_idempotent_for_same_definition_and_context() -> None:
    spec, snapshot, providers, _ = build()
    compiler = __import__("app.strategy_runtime", fromlist=["StrategyCompiler"]).StrategyCompiler(
        clock=lambda: NOW
    )
    plan = compiler.compile(spec, snapshot, providers)
    context = evaluation_context()
    runtime = StrategyRuntime(SubprocessRuleRunner(timeout_seconds=1), FrozenClock(NOW))

    first = runtime.execute(plan, context, Audit(True))
    second = runtime.execute(plan, context, Audit(True))
    assert first.execution_id == second.execution_id
    assert first.trace.trace_id == second.trace.trace_id


def test_dynamic_decimal_bindings_execute_read_multiply_threshold_chain() -> None:
    plan = synthetic_plan()
    context = EvaluationContext(
        symbol="TEST",
        timeframe="1m",
        as_of=NOW,
        market_session=MarketSession.CONTINUOUS,
        values=(ContextValue(name="seed", value=Decimal("3")),),
    )
    result = StrategyRuntime(SubprocessRuleRunner(timeout_seconds=1), FrozenClock(NOW)).execute(
        plan, context, Audit(True)
    )  # type: ignore[arg-type]

    assert result.trace.outcome is DecisionOutcome.ACCEPTED
    assert [step.result.status for step in result.trace.steps if step.result] == [
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
    ]
    assert result.trace.steps[1].result is not None
    assert result.trace.steps[1].result.outputs[0].value == Decimal("6")


def test_missing_dynamic_output_becomes_rule_input_error_without_crashing_engine() -> None:
    plan = synthetic_plan()
    context = EvaluationContext(
        symbol="TEST",
        timeframe="1m",
        as_of=NOW,
        market_session=MarketSession.CONTINUOUS,
    )
    runtime = StrategyRuntime(SubprocessRuleRunner(timeout_seconds=1), FrozenClock(NOW))

    failed = runtime.execute(plan, context, Audit(True))  # type: ignore[arg-type]

    error_codes = [step.result.error_code for step in failed.trace.steps if step.result is not None]
    assert "RULE_INPUT_MISSING" in error_codes
    assert failed.trace.outcome is DecisionOutcome.NO_DECISION
