"""Concrete composition for the synthetic Foundation + Rule Platform milestone."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final, cast
from uuid import UUID

from app.audit_engine import AuditLog, AuditStream
from app.common.clock import Clock
from app.contracts import (
    DecisionTrace,
    EvaluationContext,
    EventEnvelope,
    StrategyMode,
    new_uuid7,
)
from app.reference_engine import (
    EngineEvaluation,
    ExecutionOutcome,
    PreparedStrategy,
    ReferenceEngine,
)
from app.rule_registry import Registry, RuntimeEnvironment, discover_providers
from app.rulepacks.synthetic_core import get_provider
from app.strategy_runtime import (
    CompiledPlan,
    StrategyCompiler,
    StrategyRuntime,
    SubprocessRuleRunner,
    load_strategy_yaml,
)
from app.strategy_runtime.ports import ProviderMap, RegistrySnapshotPort

_CONFIG_ROOT: Final = Path(__file__).parents[2] / "configs" / "strategies" / "synthetic"


class NdjsonEvaluationSink:
    """Durably audit traces and decisions while preserving idempotent identities."""

    def __init__(self, runtime_root: Path, run_id: str) -> None:
        self._log = AuditLog(runtime_root)
        self._run_id = run_id
        self._trace_events: dict[UUID, UUID] = {}
        self._decision_events: dict[UUID, UUID] = {}
        self._evaluations: dict[UUID, EngineEvaluation] = {}

    @property
    def evaluations(self) -> tuple[EngineEvaluation, ...]:
        return tuple(self._evaluations.values())

    def confirm(self, trace: DecisionTrace) -> bool:
        event_id = self._trace_events.setdefault(trace.trace_id, new_uuid7())
        envelope = EventEnvelope(
            event_id=event_id,
            event_type="audit.rule_trace",
            occurred_at=trace.completed_at,
            source="foundation_integration",
            trace_id=trace.trace_id,
            correlation_id=trace.correlation_id,
            subject=trace.symbol,
            payload={
                "audit": {"run_id": self._run_id, "stream": AuditStream.RULE_TRACES.value},
                "trace": trace.model_dump(mode="json"),
            },
        )
        receipt = self._log.append(self._run_id, AuditStream.RULE_TRACES, envelope)
        return receipt.persisted or receipt.duplicate

    async def emit(self, evaluation: EngineEvaluation) -> None:
        self.confirm(evaluation.trace)
        event_id = self._decision_events.setdefault(evaluation.decision_id, new_uuid7())
        envelope = EventEnvelope(
            event_id=event_id,
            event_type="audit.decision",
            occurred_at=evaluation.trace.completed_at,
            source="foundation_integration",
            trace_id=evaluation.trace.trace_id,
            correlation_id=evaluation.trace.correlation_id,
            subject=evaluation.trace.symbol,
            payload={
                "audit": {"run_id": self._run_id, "stream": AuditStream.DECISIONS.value},
                "decision": {
                    "decision_id": str(evaluation.decision_id),
                    "event_id": str(evaluation.event_id),
                    "execution_id": str(evaluation.execution_id),
                    "strategy_id": evaluation.strategy_id,
                    "strategy_version": evaluation.strategy_version,
                    "mode": evaluation.mode.value,
                    "context_hash": evaluation.context_hash,
                    "strategy_definition_hash": evaluation.strategy_definition_hash,
                    "compiled_plan_hash": evaluation.compiled_plan_hash,
                    "registry_snapshot_hash": evaluation.registry_snapshot_hash,
                    "audit_confirmed": evaluation.audit_confirmed,
                    "eligible": evaluation.eligible,
                    "trace": evaluation.trace.model_dump(mode="json"),
                },
            },
        )
        self._log.append(self._run_id, AuditStream.DECISIONS, envelope)
        self._evaluations.setdefault(evaluation.decision_id, evaluation)

    def replay(self, stream: AuditStream) -> tuple[EventEnvelope, ...]:
        return tuple(self._log.replay(self._run_id, stream))

    def close(self) -> None:
        self._log.close()


def prepare_foundation_engine(
    runtime_root: Path,
    clock: Clock,
    *,
    rule_timeout_seconds: float = 2.0,
) -> tuple[ReferenceEngine, NdjsonEvaluationSink, tuple[CompiledPlan, ...]]:
    """Compose trusted plugins, registry, runtime, audit, and the reference engine."""

    specs = tuple(
        load_strategy_yaml(path.read_bytes())
        for path in (
            _CONFIG_ROOT / "primary_a.yaml",
            _CONFIG_ROOT / "shadow_b.yaml",
        )
    )
    run_ids = {spec.run_id for spec in specs}
    if len(run_ids) != 1:
        raise ValueError("synthetic strategies must share one run_id")
    run_id = next(iter(run_ids))

    descriptors = discover_providers()
    if len(descriptors) != 1:
        raise ValueError("foundation expects exactly one trusted synthetic provider")
    registry = Registry()
    for descriptor in descriptors:
        registry.register(descriptor)

    references = _unique(
        f"{step.rule_id}@{step.rule_version}" for spec in specs for step in spec.pipeline
    )
    snapshot = registry.snapshot(
        run_id,
        references,
        StrategyMode.PRIMARY,
        RuntimeEnvironment.PAPER,
    )
    provider = get_provider()
    provider_id = descriptors[0].provider_id
    compiler = StrategyCompiler(clock.now)
    plans = tuple(
        compiler.compile(
            spec,
            cast("RegistrySnapshotPort", snapshot),
            cast("ProviderMap", {provider_id: provider}),
        )
        for spec in specs
    )
    audit = NdjsonEvaluationSink(runtime_root, run_id)
    runtime = StrategyRuntime(
        SubprocessRuleRunner(timeout_seconds=rule_timeout_seconds),
        clock,
    )
    prepared: list[PreparedStrategy] = []
    for plan in plans:

        def evaluate(
            context: EvaluationContext, selected: CompiledPlan = plan
        ) -> ExecutionOutcome:
            result = runtime.execute(selected, context, audit)
            return ExecutionOutcome(
                execution_id=result.execution_id,
                trace=result.trace,
                audit_confirmed=result.audit_confirmed,
                eligible=result.eligible,
            )

        prepared.append(
            PreparedStrategy(
                spec=plan.contract.spec,
                strategy_definition_hash=plan.strategy_definition_hash,
                compiled_plan_hash=plan.compiled_plan_hash,
                registry_snapshot_hash=plan.registry_snapshot_hash,
                evaluate=evaluate,
            )
        )
    return ReferenceEngine(tuple(prepared), audit, clock), audit, plans


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
