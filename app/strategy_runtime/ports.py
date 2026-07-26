"""Structural ports keep the runtime independent from concrete engines and rule packs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from app.contracts import (
    DecisionTrace,
    EvaluationContext,
    RuleMetadata,
    RulePackManifest,
    RuleResult,
)


class ParameterModel(Protocol):
    @classmethod
    def model_validate(cls, value: object) -> Any: ...  # noqa: ANN401


class RuleRegistrationPort(Protocol):
    metadata: RuleMetadata
    parameter_model: type[ParameterModel]

    def execute(self, context: EvaluationContext, parameters: Any) -> RuleResult: ...  # noqa: ANN401


class RuleProviderPort(Protocol):
    manifest: RulePackManifest

    def resolve(self, rule_id: str, version: str) -> RuleRegistrationPort: ...


class RuleReferencePort(Protocol):
    rule_id: str
    version: str

    def __str__(self) -> str: ...


class ResolvedRulePort(Protocol):
    reference: RuleReferencePort
    metadata: RuleMetadata
    provider_id: str
    manifest_hash: str


class RegistrySnapshotPort(Protocol):
    run_id: str
    rules: tuple[ResolvedRulePort, ...]
    snapshot_hash: str


class AuditSink(Protocol):
    """Durably confirm a trace before a PRIMARY result can become actionable."""

    def confirm(self, trace: DecisionTrace) -> bool: ...


RuleFunction = Callable[[EvaluationContext, Any], RuleResult]
ProviderMap = Mapping[str, RuleProviderPort]
