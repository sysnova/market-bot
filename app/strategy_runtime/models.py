"""Immutable internal values for compiled and executed strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.contracts import CompiledStrategy, DecisionTrace

from .ports import RuleRegistrationPort


@dataclass(frozen=True, slots=True)
class DynamicOutputBinding:
    step_id: str
    output_name: str


@dataclass(frozen=True, slots=True)
class CompiledNode:
    step_id: str
    rule_id: str
    reference: str
    version: str
    depends_on: tuple[str, ...]
    dependency_policy: str
    enabled: bool
    provider_id: str
    parameters: dict[str, Any]
    registration: RuleRegistrationPort


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    contract: CompiledStrategy
    strategy_definition_hash: str
    compiled_plan_hash: str
    registry_snapshot_hash: str
    nodes: tuple[CompiledNode, ...]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: UUID
    trace: DecisionTrace
    audit_confirmed: bool
    eligible: bool
