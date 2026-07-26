"""Immutable values owned by the reference engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.contracts import DecisionTrace, EvaluationContext, StrategyMode, StrategySpec


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Minimal runtime result consumed through the engine's adapter boundary."""

    execution_id: UUID
    trace: DecisionTrace
    audit_confirmed: bool
    eligible: bool


@dataclass(frozen=True, slots=True)
class PreparedStrategy:
    """A compiled strategy plus its opaque evaluation callback."""

    spec: StrategySpec
    strategy_definition_hash: str
    compiled_plan_hash: str
    registry_snapshot_hash: str
    evaluate: Callable[[EvaluationContext], ExecutionOutcome]


@dataclass(frozen=True, slots=True)
class EngineEvaluation:
    """Idempotent result emitted by the reference engine."""

    decision_id: UUID
    event_id: UUID
    execution_id: UUID
    strategy_id: str
    strategy_version: str
    mode: StrategyMode
    context_hash: str
    strategy_definition_hash: str
    compiled_plan_hash: str
    registry_snapshot_hash: str
    trace: DecisionTrace
    audit_confirmed: bool
    eligible: bool
