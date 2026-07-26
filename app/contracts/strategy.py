"""Declarative and compiled strategy contracts; no runtime behavior lives here."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, SemVer, Sha256, StrictFrozenModel, UnitInterval
from .enums import DependencyPolicy, StrategyMode
from .rules import NamedValue, RulePackManifest


class PipelineStep(StrictFrozenModel):
    step_id: Identifier
    rule_id: Identifier
    rule_version: SemVer
    depends_on: tuple[Identifier, ...] = ()
    dependency_policy: DependencyPolicy = DependencyPolicy.REQUIRE_PASS
    enabled: bool = True

    @model_validator(mode="after")
    def validate_dependencies(self) -> PipelineStep:
        if self.step_id in self.depends_on:
            raise ValueError("a pipeline step cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("pipeline dependencies must be unique")
        return self


class RuleBinding(StrictFrozenModel):
    rule_id: Identifier
    alias: Identifier | None = None
    parameters: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def validate_parameters(self) -> RuleBinding:
        names = [item.name for item in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("binding parameter names must be unique")
        return self


class StrategyPolicies(StrictFrozenModel):
    fail_closed: bool = True
    stop_on_error: bool = True
    max_candidate_age: timedelta | None = Field(default=None, gt=timedelta(0))
    minimum_passing_rules: int = Field(default=1, ge=0)
    allow_not_applicable: bool = True


class ScoringWeight(StrictFrozenModel):
    rule_id: Identifier
    weight: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))


class ScoringPolicy(StrictFrozenModel):
    pass_threshold: UnitInterval
    weights: tuple[ScoringWeight, ...]
    require_complete_scoring: bool = True

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringPolicy:
        ids = [item.rule_id for item in self.weights]
        if len(ids) != len(set(ids)):
            raise ValueError("scoring rule ids must be unique")
        total_weight = sum((item.weight for item in self.weights), Decimal("0"))
        if self.weights and total_weight != Decimal("1"):
            raise ValueError("scoring weights must sum exactly to 1")
        return self


class StrategySpec(StrictFrozenModel):
    strategy_id: Identifier
    version: SemVer
    family: Identifier
    engine: Identifier
    run_id: Identifier
    mode: StrategyMode
    rule_pack_hash: Sha256
    pipeline: tuple[PipelineStep, ...]
    bindings: tuple[RuleBinding, ...]
    policies: StrategyPolicies
    scoring: ScoringPolicy
    description: NonEmptyStr | None = None
    tags: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_graph_and_bindings(self) -> StrategySpec:
        step_ids = [step.step_id for step in self.pipeline]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("pipeline step ids must be unique")
        rule_ids = [step.rule_id for step in self.pipeline]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("pipeline rule ids must be unique")
        known_steps = set(step_ids)
        for step in self.pipeline:
            unknown = set(step.depends_on) - known_steps
            if unknown:
                raise ValueError(f"unknown pipeline dependencies: {sorted(unknown)}")

        state: dict[str, int] = {}
        dependencies = {step.step_id: step.depends_on for step in self.pipeline}

        def visit(step_id: str) -> None:
            if state.get(step_id) == 1:
                raise ValueError("pipeline dependency graph contains a cycle")
            if state.get(step_id) == 2:
                return
            state[step_id] = 1
            for dependency in dependencies[step_id]:
                visit(dependency)
            state[step_id] = 2

        for step_id in step_ids:
            visit(step_id)

        binding_ids = [binding.rule_id for binding in self.bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("rule bindings must be unique")
        pipeline_rules = {step.rule_id for step in self.pipeline}
        if set(binding_ids) != pipeline_rules:
            raise ValueError("bindings must match the rules referenced by the pipeline")
        scoring_ids = {weight.rule_id for weight in self.scoring.weights}
        if not scoring_ids.issubset(pipeline_rules):
            raise ValueError("scoring may only reference pipeline rules")
        if self.policies.minimum_passing_rules > len(self.pipeline):
            raise ValueError("minimum_passing_rules exceeds pipeline length")
        return self


def validate_primary_uniqueness(strategies: Iterable[StrategySpec]) -> None:
    """Ensure one PRIMARY at most per (family, engine, run_id) deployment scope."""

    primary_by_scope: dict[tuple[str, str, str], str] = {}
    for strategy in strategies:
        if strategy.mode is not StrategyMode.PRIMARY:
            continue
        scope = (strategy.family, strategy.engine, strategy.run_id)
        existing = primary_by_scope.get(scope)
        if existing is not None:
            raise ValueError(
                "multiple PRIMARY strategies for "
                f"family={scope[0]!r}, engine={scope[1]!r}, run_id={scope[2]!r}: "
                f"{existing!r} and {strategy.strategy_id!r}"
            )
        primary_by_scope[scope] = strategy.strategy_id


class CompiledStrategy(StrictFrozenModel):
    spec: StrategySpec
    rule_pack: RulePackManifest
    compiled_hash: Sha256
    compiled_at: datetime
    execution_order: tuple[Identifier, ...]

    @model_validator(mode="after")
    def validate_compilation(self) -> CompiledStrategy:
        if self.spec.family != self.rule_pack.family or self.spec.engine != self.rule_pack.engine:
            raise ValueError("strategy and rule pack family/engine must match")
        if self.spec.rule_pack_hash != self.rule_pack.manifest_hash:
            raise ValueError("strategy rule_pack_hash does not match manifest_hash")
        step_by_id = {step.step_id: step for step in self.spec.pipeline}
        order_is_complete = len(self.execution_order) == len(step_by_id) and set(
            self.execution_order
        ) == set(step_by_id)
        if not order_is_complete:
            raise ValueError("execution_order must contain every pipeline step exactly once")
        positions = {step_id: index for index, step_id in enumerate(self.execution_order)}
        for step in self.spec.pipeline:
            invalid_order = any(
                positions[dependency] >= positions[step.step_id] for dependency in step.depends_on
            )
            if invalid_order:
                raise ValueError("execution_order violates pipeline dependencies")
        manifest_rules = {(rule.rule_id, rule.version) for rule in self.rule_pack.rules}
        required_rules = {(step.rule_id, step.rule_version) for step in self.spec.pipeline}
        missing = required_rules - manifest_rules
        if missing:
            raise ValueError(
                "compiled strategy references exact rule coordinates absent from manifest: "
                f"{sorted(missing)}"
            )
        return self
