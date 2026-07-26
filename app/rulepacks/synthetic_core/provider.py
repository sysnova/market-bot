"""Explicit discovery surface for the trusted synthetic rule pack."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.common.canonical import canonical_json, sha256_digest
from app.contracts import (
    EvaluationContext,
    RuleInputDeclaration,
    RuleLifecycleStatus,
    RuleMetadata,
    RuleOutputDeclaration,
    RulePackManifest,
    RuleResult,
    RuleType,
    StrictFrozenModel,
)

from .parameters import (
    ExceptionParameters,
    MultiplyParameters,
    ReadNumberParameters,
    ThresholdV1Parameters,
    ThresholdV2Parameters,
    TimeoutParameters,
)
from .rules import multiply, never_returns, raise_exception, read_number, threshold_v1, threshold_v2

ENTRY_POINT_GROUP = "marketbot.rulepacks.v1"
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)

RuleCallable = Callable[[EvaluationContext, Any], RuleResult]


@dataclass(frozen=True, slots=True)
class RuleRegistration:
    metadata: RuleMetadata
    parameter_model: type[StrictFrozenModel]
    callable: RuleCallable

    def execute(
        self,
        context: EvaluationContext,
        parameters: StrictFrozenModel | Mapping[str, object],
    ) -> RuleResult:
        validated = (
            parameters
            if isinstance(parameters, self.parameter_model)
            else self.parameter_model.model_validate(parameters)
        )
        return self.callable(context, validated)


@dataclass(frozen=True, slots=True)
class RulePackProvider:
    manifest: RulePackManifest
    rules: tuple[RuleRegistration, ...]

    def resolve(self, rule_id: str, version: str) -> RuleRegistration:
        for registration in self.rules:
            if (
                registration.metadata.rule_id == rule_id
                and registration.metadata.version == version
            ):
                return registration
        raise KeyError(f"exact rule version not found: {rule_id}@{version}")

    def inventory_json(self) -> bytes:
        inventory = {
            "entry_point_group": ENTRY_POINT_GROUP,
            "provider": {
                "pack_id": self.manifest.pack_id,
                "version": self.manifest.version,
                "manifest_hash": self.manifest.manifest_hash,
                "rules": [
                    {
                        "rule_id": registration.metadata.rule_id,
                        "version": registration.metadata.version,
                        "implementation_hash": registration.metadata.implementation_hash,
                        "parameter_model": registration.parameter_model.__name__,
                    }
                    for registration in self.rules
                ],
            },
        }
        return canonical_json(inventory)


def _hash(value: object) -> str:
    return f"sha256:{sha256_digest(value)}"


def _metadata(
    *,
    rule_id: str,
    version: str,
    name: str,
    description: str,
    algorithm: str,
    inputs: tuple[RuleInputDeclaration, ...],
    outputs: tuple[RuleOutputDeclaration, ...],
) -> RuleMetadata:
    return RuleMetadata(
        rule_id=rule_id,
        name=name,
        version=version,
        rule_type=RuleType.FILTER,
        lifecycle_status=RuleLifecycleStatus.PAPER,
        description=description,
        implementation_hash=_hash(
            {"rule_id": rule_id, "version": version, "algorithm": algorithm}
        ),
        inputs=inputs,
        outputs=outputs,
        author="MarketBot",
        tags=("synthetic",),
        created_at=CREATED_AT,
        validated_at=CREATED_AT,
    )


def _registrations() -> tuple[RuleRegistration, ...]:
    number_output = (RuleOutputDeclaration(name="number", data_type="decimal"),)
    matched_output = (RuleOutputDeclaration(name="matched", data_type="boolean"),)
    return (
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.read_number",
                version="1.0.0",
                name="Read number",
                description="Reads an exact number from evaluation context.",
                algorithm="context[source] -> Decimal|int; bool and inexact types rejected",
                inputs=(RuleInputDeclaration(name="source", data_type="identifier"),),
                outputs=number_output,
            ),
            parameter_model=ReadNumberParameters,
            callable=cast("RuleCallable", read_number),
        ),
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.multiply",
                version="1.0.0",
                name="Multiply",
                description="Multiplies two exact decimal values.",
                algorithm="Decimal(value) * Decimal(factor)",
                inputs=(
                    RuleInputDeclaration(name="value", data_type="decimal"),
                    RuleInputDeclaration(name="factor", data_type="decimal"),
                ),
                outputs=(RuleOutputDeclaration(name="product", data_type="decimal"),),
            ),
            parameter_model=MultiplyParameters,
            callable=cast("RuleCallable", multiply),
        ),
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.threshold",
                version="1.0.0",
                name="Minimum threshold",
                description="Checks an inclusive lower bound.",
                algorithm="value >= minimum",
                inputs=(
                    RuleInputDeclaration(name="value", data_type="decimal"),
                    RuleInputDeclaration(name="minimum", data_type="decimal"),
                ),
                outputs=matched_output,
            ),
            parameter_model=ThresholdV1Parameters,
            callable=cast("RuleCallable", threshold_v1),
        ),
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.threshold",
                version="2.0.0",
                name="Range threshold",
                description="Checks an inclusive lower and upper bound.",
                algorithm="lower <= value <= upper",
                inputs=(
                    RuleInputDeclaration(name="value", data_type="decimal"),
                    RuleInputDeclaration(name="lower", data_type="decimal"),
                    RuleInputDeclaration(name="upper", data_type="decimal"),
                ),
                outputs=matched_output,
            ),
            parameter_model=ThresholdV2Parameters,
            callable=cast("RuleCallable", threshold_v2),
        ),
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.exception",
                version="1.0.0",
                name="Intentional exception",
                description="Raises a deterministic exception for failure isolation tests.",
                algorithm="raise RuntimeError(message)",
                inputs=(RuleInputDeclaration(name="message", data_type="string", required=False),),
                outputs=(),
            ),
            parameter_model=ExceptionParameters,
            callable=cast("RuleCallable", raise_exception),
        ),
        RuleRegistration(
            metadata=_metadata(
                rule_id="synthetic.timeout",
                version="1.0.0",
                name="Intentional timeout",
                description="Never returns and must be terminated by the runtime.",
                algorithm="while True: pass",
                inputs=(),
                outputs=(),
            ),
            parameter_model=TimeoutParameters,
            callable=cast("RuleCallable", never_returns),
        ),
    )


def get_rules() -> tuple[RuleRegistration, ...]:
    """Return the exact executable catalog without performing discovery."""

    return _registrations()


def _manifest(version: str, registrations: tuple[RuleRegistration, ...]) -> RulePackManifest:
    rules = tuple(registration.metadata for registration in registrations)
    description = f"Trusted synthetic rule pack generation {version}."
    data: dict[str, object] = {
        "pack_id": "synthetic_core",
        "version": version,
        "family": "synthetic",
        "engine": "reference_engine",
        "rules": rules,
        "created_at": CREATED_AT,
        "description": description,
    }
    return RulePackManifest(
        pack_id="synthetic_core",
        version=version,
        family="synthetic",
        engine="reference_engine",
        manifest_hash=_hash(data),
        rules=rules,
        created_at=CREATED_AT,
        description=description,
    )


def get_providers() -> tuple[RulePackProvider, ...]:
    """Return the single provider exported through plugin discovery."""

    return (get_provider(),)


def get_provider() -> RulePackProvider:
    """Return one manifest containing every exact executable coordinate."""

    rules = get_rules()
    return RulePackProvider(manifest=_manifest("1.0.0", rules), rules=rules)
