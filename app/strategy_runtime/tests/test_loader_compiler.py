from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    EvaluationContext,
    NamedValue,
    PipelineStep,
    RuleBinding,
    RuleLifecycleStatus,
    RuleMetadata,
    RulePackManifest,
    RuleResult,
    RuleStatus,
    RuleType,
    ScoringPolicy,
    ScoringWeight,
    StrategyMode,
    StrategyPolicies,
    StrategySpec,
    StrictFrozenModel,
)
from app.rule_registry import (
    Registry,
    RegistryProvider,
    RuntimeEnvironment,
    calculate_manifest_hash,
)
from app.rulepacks.synthetic_core import get_provider
from app.strategy_runtime import CompileError, StrategyCompiler, load_strategy_yaml
from app.strategy_runtime.models import DynamicOutputBinding

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class NumberParameters(StrictFrozenModel):
    value: Decimal


class Registration:
    parameter_model = NumberParameters

    def __init__(self, metadata: RuleMetadata) -> None:
        self.metadata = metadata

    def execute(self, context: EvaluationContext, parameters: NumberParameters) -> RuleResult:
        return RuleResult(
            rule_id=self.metadata.rule_id,
            rule_version=self.metadata.version,
            status=RuleStatus.PASS,
            evaluated_at=context.as_of,
            score=Decimal("1"),
            reason=f"value={parameters.value}",
        )


class Provider:
    def __init__(self, manifest: RulePackManifest) -> None:
        self.manifest = manifest
        self.registrations = {
            (metadata.rule_id, metadata.version): Registration(metadata)
            for metadata in manifest.rules
        }

    def resolve(self, rule_id: str, version: str) -> Registration:
        return self.registrations[(rule_id, version)]


def build() -> tuple[StrategySpec, object, dict[str, Provider], RulePackManifest]:
    metadata = RuleMetadata(
        rule_id="number",
        name="Number",
        version="1.0.0",
        rule_type=RuleType.FILTER,
        lifecycle_status=RuleLifecycleStatus.APPROVED,
        description="Number rule",
        implementation_hash="sha256:" + "a" * 64,
        created_at=NOW,
    )
    other_metadata = metadata.model_copy(
        update={
            "rule_id": "other",
            "name": "Other",
            "implementation_hash": "sha256:" + "c" * 64,
        }
    )
    values = {
        "pack_id": "tests",
        "version": "1.0.0",
        "family": "synthetic",
        "engine": "reference",
        "rules": (metadata, other_metadata),
        "created_at": NOW,
        "description": None,
    }
    manifest = RulePackManifest(manifest_hash=calculate_manifest_hash(values), **values)
    registry = Registry()
    registry.register(RegistryProvider("tests.provider", "1.0.0", manifest))
    spec = StrategySpec(
        strategy_id="test",
        version="1.0.0",
        family="synthetic",
        engine="reference",
        run_id="run-1",
        mode=StrategyMode.PRIMARY,
        rule_pack_hash=manifest.manifest_hash,
        pipeline=(
            PipelineStep(
                step_id="second",
                rule_id="other",
                rule_version="1.0.0",
                depends_on=("first",),
            ),
            PipelineStep(step_id="first", rule_id="number", rule_version="1.0.0"),
        ),
        bindings=(
            RuleBinding(
                rule_id="number", parameters=(NamedValue(name="value", value=Decimal("2")),)
            ),
            RuleBinding(
                rule_id="other", parameters=(NamedValue(name="value", value=Decimal("3")),)
            ),
        ),
        policies=StrategyPolicies(minimum_passing_rules=1),
        scoring=ScoringPolicy(
            pass_threshold=Decimal("0.5"),
            weights=(ScoringWeight(rule_id="number", weight=Decimal("1")),),
        ),
    )
    snapshot = registry.snapshot(
        "run-1",
        ("number@1.0.0", "other@1.0.0"),
        StrategyMode.PRIMARY,
        RuntimeEnvironment.LIVE,
    )
    return spec, snapshot, {"tests.provider": Provider(manifest)}, manifest


def test_safe_yaml_loader_builds_strict_frozen_spec() -> None:
    spec, _, _, _ = build()
    loaded = load_strategy_yaml(spec.model_dump_json())
    assert loaded.strategy_id == spec.strategy_id
    assert loaded.mode is StrategyMode.PRIMARY
    with pytest.raises(ValidationError):
        loaded.strategy_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        load_strategy_yaml("!!python/object:os.system ['echo unsafe']")


def test_compile_resolves_exact_snapshot_validates_parameters_and_sorts_dag() -> None:
    spec, snapshot, providers, manifest = build()
    plan = StrategyCompiler(clock=lambda: NOW).compile(spec, snapshot, providers)

    assert plan.contract.rule_pack == manifest
    assert plan.contract.execution_order == ("first", "second")
    assert plan.nodes[0].reference == "number@1.0.0"
    assert plan.nodes[0].parameters == {"value": Decimal("2")}


def test_compile_selects_declared_version_when_snapshot_contains_v1_and_v2() -> None:
    base_spec, _, _, manifest = build()
    version_one = manifest.rules[0]
    version_two = version_one.model_copy(
        update={"version": "2.0.0", "implementation_hash": "sha256:" + "b" * 64}
    )
    v1_values = {
        "pack_id": "tests_v1",
        "version": "1.0.0",
        "family": manifest.family,
        "engine": manifest.engine,
        "rules": (version_one,),
        "created_at": NOW,
        "description": None,
    }
    v2_values = {
        "pack_id": manifest.pack_id,
        "version": "2.0.0",
        "family": manifest.family,
        "engine": manifest.engine,
        "rules": (version_two,),
        "created_at": NOW,
        "description": None,
    }
    v1_manifest = RulePackManifest(manifest_hash=calculate_manifest_hash(v1_values), **v1_values)
    v2_manifest = RulePackManifest(manifest_hash=calculate_manifest_hash(v2_values), **v2_values)
    registry = Registry()
    registry.register(RegistryProvider("tests.v1", "1.0.0", v1_manifest))
    registry.register(RegistryProvider("tests.v2", "1.0.0", v2_manifest))
    snapshot = registry.snapshot(
        "run-1",
        ("number@1.0.0", "number@2.0.0"),
        StrategyMode.PRIMARY,
        RuntimeEnvironment.LIVE,
    )
    version_two_spec = base_spec.model_copy(
        update={
            "rule_pack_hash": v2_manifest.manifest_hash,
            "pipeline": (PipelineStep(step_id="only", rule_id="number", rule_version="2.0.0"),),
            "bindings": (base_spec.bindings[0],),
            "policies": StrategyPolicies(minimum_passing_rules=1),
        }
    )

    plan = StrategyCompiler(clock=lambda: NOW).compile(
        version_two_spec,
        snapshot,
        {"tests.v1": Provider(v1_manifest), "tests.v2": Provider(v2_manifest)},
    )

    assert next(node.reference for node in plan.nodes if node.rule_id == "number") == (
        "number@2.0.0"
    )


def test_definition_and_plan_hashes_ignore_run_and_compile_time() -> None:
    spec, snapshot, providers, _ = build()
    compiler = StrategyCompiler(clock=lambda: NOW)
    first = compiler.compile(spec, snapshot, providers)
    second = compiler.compile(
        spec.model_copy(update={"run_id": "run-2"}),
        type(snapshot)(run_id="run-2", rules=snapshot.rules),
        providers,
    )
    assert first.strategy_definition_hash == second.strategy_definition_hash
    assert first.compiled_plan_hash == second.compiled_plan_hash


def test_compile_accepts_yaml_decimal_string_and_rejects_non_exact_numbers() -> None:
    spec, snapshot, providers, _ = build()
    from_yaml = spec.model_copy(
        update={
            "bindings": (
                RuleBinding(rule_id="number", parameters=(NamedValue(name="value", value="2"),)),
                spec.bindings[1],
            )
        }
    )
    plan = StrategyCompiler(clock=lambda: NOW).compile(from_yaml, snapshot, providers)

    assert plan.nodes[0].parameters == {"value": Decimal("2")}

    for invalid in (2.0, float("nan"), float("inf"), "NaN", "Infinity", "-Infinity"):
        bad = spec.model_copy(
            update={
                "bindings": (
                    RuleBinding(
                        rule_id="number",
                        parameters=(NamedValue(name="value", value=invalid),),
                    ),
                    spec.bindings[1],
                )
            }
        )
        with pytest.raises(CompileError, match="parameters"):
            StrategyCompiler(clock=lambda: NOW).compile(bad, snapshot, providers)


def test_compile_rejects_dynamic_reference_not_declared_as_dependency() -> None:
    spec, snapshot, providers, _ = build()
    bad = spec.model_copy(
        update={
            "bindings": (
                spec.bindings[0],
                RuleBinding(
                    rule_id="other",
                    parameters=(NamedValue(name="value", value="${steps.unknown.outputs.number}"),),
                ),
            )
        }
    )
    with pytest.raises(CompileError, match="declared dependency"):
        StrategyCompiler(clock=lambda: NOW).compile(bad, snapshot, providers)


def test_compile_preserves_exact_declared_dynamic_output_binding() -> None:
    provider = get_provider()
    registry = Registry()
    registry.register(RegistryProvider("synthetic.v1", "1", provider.manifest))
    snapshot = registry.snapshot(
        "run-chain",
        ("synthetic.read_number@1.0.0", "synthetic.multiply@1.0.0"),
        StrategyMode.CANDIDATE,
        RuntimeEnvironment.LIVE,
    )
    spec = StrategySpec(
        strategy_id="dynamic-chain",
        version="1.0.0",
        family="synthetic",
        engine="reference_engine",
        run_id="run-chain",
        mode=StrategyMode.CANDIDATE,
        rule_pack_hash=provider.manifest.manifest_hash,
        pipeline=(
            PipelineStep(
                step_id="read",
                rule_id="synthetic.read_number",
                rule_version="1.0.0",
            ),
            PipelineStep(
                step_id="multiply",
                rule_id="synthetic.multiply",
                rule_version="1.0.0",
                depends_on=("read",),
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
                    NamedValue(name="factor", value="2"),
                ),
            ),
        ),
        policies=StrategyPolicies(minimum_passing_rules=1),
        scoring=ScoringPolicy(
            pass_threshold=Decimal("1"),
            weights=(
                ScoringWeight(rule_id="synthetic.multiply", weight=Decimal("1")),
            ),
        ),
    )

    plan = StrategyCompiler(clock=lambda: NOW).compile(
        spec, snapshot, {"synthetic.v1": provider}
    )

    assert plan.nodes[1].parameters == {
        "value": DynamicOutputBinding(step_id="read", output_name="number"),
        "factor": Decimal("2"),
    }

    undeclared_output = spec.model_copy(
        update={
            "bindings": (
                spec.bindings[0],
                RuleBinding(
                    rule_id="synthetic.multiply",
                    parameters=(
                        NamedValue(
                            name="value",
                            value="${steps.read.outputs.not_declared}",
                        ),
                        NamedValue(name="factor", value=Decimal("2")),
                    ),
                ),
            )
        }
    )
    with pytest.raises(CompileError, match=r"output read\.not_declared is not declared"):
        StrategyCompiler(clock=lambda: NOW).compile(
            undeclared_output, snapshot, {"synthetic.v1": provider}
        )
