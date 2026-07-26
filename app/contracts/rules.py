"""Event, rule and evaluation contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    SemVer,
    Sha256,
    StrictFrozenModel,
    UnitInterval,
    new_uuid7,
    utc_now,
)
from .enums import MarketSession, RuleLifecycleStatus, RuleStatus, RuleType


class NamedValue(StrictFrozenModel):
    name: Identifier
    value: Any


class EventEnvelope(StrictFrozenModel):
    event_id: UUID = Field(default_factory=new_uuid7)
    event_type: Identifier
    schema_version: SemVer = "1.0.0"
    occurred_at: datetime = Field(default_factory=utc_now)
    source: Identifier
    trace_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    market_session: MarketSession | None = None
    subject: NonEmptyStr | None = None
    payload: Any | None = None
    attributes: tuple[NamedValue, ...] = ()

    @field_validator("event_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("event_id must be a UUIDv7")
        return value


class RuleInputDeclaration(StrictFrozenModel):
    name: Identifier
    data_type: Identifier
    required: bool = True
    description: NonEmptyStr | None = None
    unit: NonEmptyStr | None = None


class RuleOutputDeclaration(StrictFrozenModel):
    name: Identifier
    data_type: Identifier
    description: NonEmptyStr | None = None
    unit: NonEmptyStr | None = None


class RuleMetadata(StrictFrozenModel):
    rule_id: Identifier
    name: NonEmptyStr
    version: SemVer
    rule_type: RuleType
    lifecycle_status: RuleLifecycleStatus
    description: NonEmptyStr
    implementation_hash: Sha256
    inputs: tuple[RuleInputDeclaration, ...] = ()
    outputs: tuple[RuleOutputDeclaration, ...] = ()
    author: NonEmptyStr | None = None
    tags: tuple[Identifier, ...] = ()
    created_at: datetime
    validated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_declarations(self) -> RuleMetadata:
        input_names = [item.name for item in self.inputs]
        output_names = [item.name for item in self.outputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("rule input names must be unique")
        if len(output_names) != len(set(output_names)):
            raise ValueError("rule output names must be unique")
        if self.validated_at is not None and self.validated_at < self.created_at:
            raise ValueError("validated_at cannot precede created_at")
        return self


class ContextValue(StrictFrozenModel):
    name: Identifier
    value: Any
    observed_at: datetime | None = None
    source: Identifier | None = None


class EvaluationContext(StrictFrozenModel):
    symbol: Identifier
    timeframe: Identifier
    as_of: datetime
    market_session: MarketSession
    run_id: Identifier | None = None
    trace_id: UUID | None = None
    correlation_id: UUID | None = None
    values: tuple[ContextValue, ...] = ()

    @model_validator(mode="after")
    def validate_values(self) -> EvaluationContext:
        names = [item.name for item in self.values]
        if len(names) != len(set(names)):
            raise ValueError("evaluation context value names must be unique")
        return self


class RuleOutputValue(StrictFrozenModel):
    name: Identifier
    value: Any


class RuleResult(StrictFrozenModel):
    rule_id: Identifier
    rule_version: SemVer | None = None
    status: RuleStatus
    evaluated_at: datetime
    score: UnitInterval | None = None
    reason: NonEmptyStr | None = None
    outputs: tuple[RuleOutputValue, ...] = ()
    error_code: Identifier | None = None
    error_message: NonEmptyStr | None = None
    duration_ms: Decimal | None = Field(default=None, ge=Decimal("0"))

    @model_validator(mode="after")
    def validate_status_details(self) -> RuleResult:
        if self.status is RuleStatus.ERROR and self.error_message is None:
            raise ValueError("ERROR results require error_message")
        if self.status is not RuleStatus.ERROR and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("error fields are only valid for ERROR results")
        names = [item.name for item in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError("rule output names must be unique")
        return self


class RulePackManifest(StrictFrozenModel):
    pack_id: Identifier
    version: SemVer
    family: Identifier
    engine: Identifier
    manifest_hash: Sha256
    rules: tuple[RuleMetadata, ...] = ()
    created_at: datetime
    description: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_rules(self) -> RulePackManifest:
        coordinates = [(rule.rule_id, rule.version) for rule in self.rules]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("rule coordinates in a pack must be unique")
        return self
