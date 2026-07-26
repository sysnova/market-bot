"""Policy-aware, fail-closed execution of deterministic compiled plans."""

from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from app.common.canonical import sha256_digest
from app.common.clock import Clock
from app.contracts import (
    DecisionOutcome,
    DecisionTrace,
    DependencyPolicy,
    EvaluationContext,
    RuleResult,
    RuleStatus,
    RuleTraceStatus,
    RuleTraceStep,
    StrategyMode,
)

from .models import CompiledNode, CompiledPlan, DynamicOutputBinding, ExecutionResult
from .ports import AuditSink
from .subprocess_runner import SubprocessRuleRunner


def _idempotent_id(kind: str, plan: CompiledPlan, context: EvaluationContext) -> UUID:
    context_hash = sha256_digest(context.model_dump(mode="python"))
    return uuid5(NAMESPACE_URL, f"marketbot:{kind}:{plan.compiled_plan_hash}:{context_hash}")


class StrategyRuntime:
    def __init__(self, runner: SubprocessRuleRunner, clock: Clock) -> None:
        self._runner = runner
        self._clock = clock

    def execute(
        self, plan: CompiledPlan, context: EvaluationContext, audit_sink: AuditSink
    ) -> ExecutionResult:
        execution_id = _idempotent_id("execution", plan, context)
        trace_id = _idempotent_id("trace", plan, context)
        started_at = self._clock.now()
        spec = plan.contract.spec
        if spec.mode is StrategyMode.DISABLED:
            trace = DecisionTrace(
                trace_id=trace_id,
                correlation_id=context.correlation_id,
                strategy_id=spec.strategy_id,
                strategy_version=spec.version,
                compiled_strategy_hash=plan.compiled_plan_hash,
                symbol=context.symbol,
                started_at=started_at,
                completed_at=self._clock.now(),
                outcome=DecisionOutcome.NO_DECISION,
                reasons=("strategy mode is DISABLED",),
            )
            return ExecutionResult(execution_id, trace, False, False)

        results: dict[str, RuleResult] = {}
        traces: list[RuleTraceStep] = []
        stopped_on_error = False
        for node in plan.nodes:
            node_started = self._clock.now()
            skipped = self._skipped_dependencies(node.depends_on, node.dependency_policy, results)
            if stopped_on_error:
                skipped = skipped or node.depends_on or ("prior_error",)
            if skipped:
                traces.append(
                    RuleTraceStep(
                        step_id=node.step_id,
                        rule_id=node.rule_id,
                        status=RuleTraceStatus.SKIPPED_DEPENDENCY,
                        started_at=node_started,
                        completed_at=self._clock.now(),
                        skipped_dependencies=tuple(skipped),
                        message="dependency policy prevented execution",
                    )
                )
                continue
            if not node.enabled:
                result = RuleResult(
                    rule_id=node.rule_id,
                    rule_version=node.version,
                    status=RuleStatus.NOT_APPLICABLE,
                    evaluated_at=context.as_of,
                    reason="pipeline step is disabled",
                )
            else:
                resolved_parameters, missing = self._resolve_parameters(node, results)
                if missing is not None:
                    result = self._input_error(node, context, "RULE_INPUT_MISSING", missing)
                else:
                    try:
                        parameters = node.registration.parameter_model.model_validate(
                            resolved_parameters
                        )
                    except (ValidationError, TypeError, ValueError) as error:
                        result = self._input_error(
                            node,
                            context,
                            "RULE_INPUT_INVALID",
                            f"resolved rule parameters are invalid: {error}",
                        )
                    else:
                        result = self._runner.run(
                            node.registration.execute,
                            context,
                            parameters,
                            rule_id=node.rule_id,
                            rule_version=node.version,
                        )
            results[node.step_id] = result
            traces.append(
                RuleTraceStep(
                    step_id=node.step_id,
                    rule_id=node.rule_id,
                    status=RuleTraceStatus(result.status.value),
                    started_at=node_started,
                    completed_at=self._clock.now(),
                    result=result,
                )
            )
            if result.status is RuleStatus.ERROR and spec.policies.stop_on_error:
                stopped_on_error = True

        outcome, score, reasons = self._decide(plan, results)
        trace = DecisionTrace(
            trace_id=trace_id,
            correlation_id=context.correlation_id,
            strategy_id=spec.strategy_id,
            strategy_version=spec.version,
            compiled_strategy_hash=plan.compiled_plan_hash,
            symbol=context.symbol,
            started_at=started_at,
            completed_at=self._clock.now(),
            outcome=outcome,
            score=score,
            steps=tuple(traces),
            reasons=tuple(reasons),
        )
        audit_confirmed = False
        if spec.mode is StrategyMode.PRIMARY:
            try:
                audit_confirmed = audit_sink.confirm(trace)
            except Exception:
                audit_confirmed = False
        eligible = (
            spec.mode is StrategyMode.PRIMARY
            and outcome is DecisionOutcome.ACCEPTED
            and audit_confirmed
        )
        return ExecutionResult(execution_id, trace, audit_confirmed, eligible)

    @staticmethod
    def _resolve_parameters(
        node: CompiledNode, results: dict[str, RuleResult]
    ) -> tuple[dict[str, object], str | None]:
        resolved: dict[str, object] = {}
        for name, value in node.parameters.items():
            if not isinstance(value, DynamicOutputBinding):
                resolved[name] = value
                continue
            source = results.get(value.step_id)
            if source is None:
                return {}, f"source step {value.step_id!r} has no result"
            outputs = {item.name: item.value for item in source.outputs}
            if value.output_name not in outputs:
                return (
                    {},
                    f"source output {value.step_id}.{value.output_name} is missing",
                )
            resolved[name] = outputs[value.output_name]
        return resolved, None

    @staticmethod
    def _input_error(
        node: CompiledNode,
        context: EvaluationContext,
        code: str,
        message: str,
    ) -> RuleResult:
        return RuleResult(
            rule_id=node.rule_id,
            rule_version=node.version,
            status=RuleStatus.ERROR,
            evaluated_at=context.as_of,
            reason=message,
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _skipped_dependencies(
        dependency_ids: tuple[str, ...], policy: str, results: dict[str, RuleResult]
    ) -> tuple[str, ...]:
        if policy == DependencyPolicy.REQUIRE_PASS.value:
            return tuple(
                dependency
                for dependency in dependency_ids
                if dependency not in results or results[dependency].status is not RuleStatus.PASS
            )
        return tuple(dependency for dependency in dependency_ids if dependency not in results)

    @staticmethod
    def _decide(
        plan: CompiledPlan, results: dict[str, RuleResult]
    ) -> tuple[DecisionOutcome, Decimal | None, list[str]]:
        spec = plan.contract.spec
        values = tuple(results.values())
        errors = [result for result in values if result.status is RuleStatus.ERROR]
        if errors:
            outcome = (
                DecisionOutcome.ERROR
                if spec.mode is StrategyMode.PRIMARY and spec.policies.fail_closed
                else DecisionOutcome.NO_DECISION
            )
            return outcome, None, ["one or more rules ended in ERROR"]
        if any(result.status is RuleStatus.FAIL for result in values):
            return DecisionOutcome.REJECTED, Decimal("0"), ["one or more rules failed"]
        if not spec.policies.allow_not_applicable and any(
            result.status is RuleStatus.NOT_APPLICABLE for result in values
        ):
            return DecisionOutcome.REJECTED, None, ["NOT_APPLICABLE is forbidden by policy"]
        passing = sum(result.status is RuleStatus.PASS for result in values)
        if passing < spec.policies.minimum_passing_rules:
            return DecisionOutcome.REJECTED, None, ["minimum passing rule count was not met"]

        latest_by_rule: dict[str, RuleResult] = {}
        for node in plan.nodes:
            if node.step_id in results:
                latest_by_rule[node.rule_id] = results[node.step_id]
        weighted_score = Decimal("0")
        for scoring_weight in spec.scoring.weights:
            result = latest_by_rule.get(scoring_weight.rule_id)
            if result is None or result.score is None:
                if spec.scoring.require_complete_scoring:
                    return DecisionOutcome.NO_DECISION, None, ["scoring result is incomplete"]
                continue
            weighted_score += result.score * scoring_weight.weight
        if weighted_score < spec.scoring.pass_threshold:
            return DecisionOutcome.REJECTED, weighted_score, ["weighted score is below threshold"]
        return DecisionOutcome.ACCEPTED, weighted_score, ["all strategy policies passed"]
