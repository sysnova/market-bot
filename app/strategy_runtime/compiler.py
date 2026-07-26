"""Deterministic compiler from a strategy contract to executable rule bindings."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.common.canonical import sha256_digest
from app.contracts import CompiledStrategy, StrategySpec

from .errors import CompileError
from .models import CompiledNode, CompiledPlan, DynamicOutputBinding
from .ports import ProviderMap, RegistrySnapshotPort, RuleRegistrationPort

_DYNAMIC_OUTPUT = re.compile(
    r"^\$\{steps\.(?P<step>[A-Za-z0-9][A-Za-z0-9._:/-]{0,127})"
    r"\.outputs\.(?P<output>[A-Za-z0-9][A-Za-z0-9._:/-]{0,127})\}$"
)
_JSON_VALUE_ADAPTER: TypeAdapter[object] = TypeAdapter(object)


def _digest(value: object) -> str:
    return f"sha256:{sha256_digest(value)}"


def _definition_payload(spec: StrategySpec) -> dict[str, Any]:
    payload = spec.model_dump(mode="python")
    payload.pop("run_id", None)
    return payload


def _strict_json(value: object) -> bytes:
    """Serialize YAML-shaped input without accepting inexact numeric values."""
    if isinstance(value, float):
        raise ValueError("floating-point rule parameters are not allowed")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("non-finite Decimal rule parameters are not allowed")
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        for item in mapping.values():
            _strict_json(item)
    elif isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        for item in sequence:
            _strict_json(item)
    return _JSON_VALUE_ADAPTER.dump_json(cast("object", value))


def _stable_topological_order(spec: StrategySpec) -> tuple[str, ...]:
    steps = {step.step_id: step for step in spec.pipeline}
    positions = {step.step_id: index for index, step in enumerate(spec.pipeline)}
    incoming = {step.step_id: len(step.depends_on) for step in spec.pipeline}
    dependents: dict[str, list[str]] = {step_id: [] for step_id in steps}
    for step in spec.pipeline:
        for dependency in step.depends_on:
            dependents[dependency].append(step.step_id)
    ready: list[str] = sorted(
        (step_id for step_id, count in incoming.items() if count == 0),
        key=lambda step_id: positions[step_id],
    )
    ordered: list[str] = []
    while ready:
        step_id = ready.pop(0)
        ordered.append(step_id)
        for dependent in dependents[step_id]:
            incoming[dependent] -= 1
            if incoming[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda item: positions[item])
    if len(ordered) != len(steps):
        raise CompileError("pipeline dependency graph contains a cycle")
    return tuple(ordered)


def _compile_parameters(
    raw: dict[str, Any],
    registration: RuleRegistrationPort,
    step_dependencies: tuple[str, ...],
    compiled_by_step: dict[str, CompiledNode],
) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    has_dynamic = False
    for name, value in raw.items():
        match = _DYNAMIC_OUTPUT.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            prepared[name] = value
            continue
        has_dynamic = True
        source_step = match.group("step")
        output_name = match.group("output")
        if source_step not in step_dependencies:
            raise CompileError(
                f"dynamic binding source {source_step!r} must be a declared dependency"
            )
        source_node = compiled_by_step.get(source_step)
        if source_node is None:
            raise CompileError(f"dynamic binding source {source_step!r} must be compiled earlier")
        outputs = {item.name for item in source_node.registration.metadata.outputs}
        if output_name not in outputs:
            raise CompileError(
                f"dynamic binding output {source_step}.{output_name} is not declared"
            )
        prepared[name] = DynamicOutputBinding(source_step, output_name)

    model_class = cast("type[BaseModel]", registration.parameter_model)
    if not has_dynamic:
        return model_class.model_validate_json(
            _strict_json(prepared), strict=True
        ).model_dump(mode="python")

    fields = model_class.model_fields
    extra = set(prepared) - set(fields)
    missing = {
        name for name, field in fields.items() if field.is_required() and name not in prepared
    }
    if extra or missing:
        raise CompileError(f"invalid parameters: extra={sorted(extra)}, missing={sorted(missing)}")
    for name, value in tuple(prepared.items()):
        if isinstance(value, DynamicOutputBinding):
            continue
        field = fields[name]
        prepared[name] = TypeAdapter(
            field.rebuild_annotation(), config=ConfigDict(strict=True)
        ).validate_json(_strict_json(value), strict=True)
    return prepared


class StrategyCompiler:
    """Bind exact rule versions and validated parameters to a snapshot."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def compile(
        self,
        spec: StrategySpec,
        snapshot: RegistrySnapshotPort,
        providers: ProviderMap,
    ) -> CompiledPlan:
        if snapshot.run_id != spec.run_id:
            raise CompileError("strategy run_id does not match registry snapshot")
        order = _stable_topological_order(spec)
        resolved_by_coordinate: dict[tuple[str, str], Any] = {}
        for resolved in snapshot.rules:
            coordinate = (resolved.metadata.rule_id, resolved.metadata.version)
            if coordinate in resolved_by_coordinate:
                raise CompileError(f"duplicate exact rule coordinate in snapshot: {coordinate}")
            resolved_by_coordinate[coordinate] = resolved
        binding_by_rule = {binding.rule_id: binding for binding in spec.bindings}
        nodes: list[CompiledNode] = []
        compiled_by_step: dict[str, CompiledNode] = {}
        selected_manifest = None

        for step_id in order:
            step = next(item for item in spec.pipeline if item.step_id == step_id)
            coordinate = (step.rule_id, step.rule_version)
            resolved = resolved_by_coordinate.get(coordinate)
            if resolved is None:
                raise CompileError(
                    f"exact rule {step.rule_id}@{step.rule_version} is absent from snapshot"
                )
            if resolved.manifest_hash != spec.rule_pack_hash:
                raise CompileError(f"rule {step.rule_id!r} belongs to a different rule pack")
            try:
                provider = providers[resolved.provider_id]
                registration = provider.resolve(
                    resolved.reference.rule_id, resolved.reference.version
                )
            except (KeyError, LookupError) as error:
                raise CompileError(
                    f"provider cannot resolve exact rule {resolved.reference}"
                ) from error
            if provider.manifest.manifest_hash != spec.rule_pack_hash:
                raise CompileError("provider manifest does not match strategy rule_pack_hash")
            if registration.metadata != resolved.metadata:
                raise CompileError(f"provider metadata drift for {resolved.reference}")
            selected_manifest = provider.manifest
            raw_parameters = {
                item.name: item.value for item in binding_by_rule[step.rule_id].parameters
            }
            try:
                parameters = _compile_parameters(
                    raw_parameters, registration, step.depends_on, compiled_by_step
                )
            except (ValidationError, TypeError, ValueError) as error:
                raise CompileError(
                    f"invalid parameters for exact rule {resolved.reference}: {error}"
                ) from error
            node = CompiledNode(
                step_id=step.step_id,
                rule_id=step.rule_id,
                reference=str(resolved.reference),
                version=resolved.reference.version,
                depends_on=step.depends_on,
                dependency_policy=step.dependency_policy.value,
                enabled=step.enabled,
                provider_id=resolved.provider_id,
                parameters=parameters,
                registration=registration,
            )
            nodes.append(node)
            compiled_by_step[node.step_id] = node

        if selected_manifest is None:
            raise CompileError("strategy pipeline cannot be empty")
        definition_hash = _digest(_definition_payload(spec))
        plan_payload = {
            "strategy_definition_hash": definition_hash,
            "rules": [
                {
                    "step_id": node.step_id,
                    "reference": node.reference,
                    "implementation_hash": node.registration.metadata.implementation_hash,
                    "parameters": node.parameters,
                    "depends_on": node.depends_on,
                    "dependency_policy": node.dependency_policy,
                    "enabled": node.enabled,
                }
                for node in nodes
            ],
        }
        compiled_hash = _digest(plan_payload)
        contract = CompiledStrategy(
            spec=spec,
            rule_pack=selected_manifest,
            compiled_hash=compiled_hash,
            compiled_at=self._clock(),
            execution_order=order,
        )
        return CompiledPlan(
            contract=contract,
            strategy_definition_hash=definition_hash,
            compiled_plan_hash=compiled_hash,
            registry_snapshot_hash=snapshot.snapshot_hash,
            nodes=tuple(nodes),
        )
