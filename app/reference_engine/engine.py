"""Reference event consumer coordinating prepared PRIMARY and CANDIDATE strategies."""

from __future__ import annotations

import asyncio
from uuid import NAMESPACE_URL, UUID, uuid5

from app.common.canonical import sha256_digest
from app.common.clock import Clock
from app.contracts import (
    DecisionOutcome,
    DecisionTrace,
    EventEnvelope,
    StrategyMode,
    validate_primary_uniqueness,
)

from .context import context_from_event
from .models import EngineEvaluation, ExecutionOutcome, PreparedStrategy
from .ports import EvaluationSink, EventBusPort, SubscriptionPort


class ReferenceEngine:
    """Evaluate two exact strategy versions against the same immutable context."""

    def __init__(
        self,
        strategies: tuple[PreparedStrategy, ...],
        sink: EvaluationSink,
        clock: Clock,
    ) -> None:
        if not strategies:
            raise ValueError("reference engine requires at least one prepared strategy")
        unsupported = tuple(
            item.spec.mode
            for item in strategies
            if item.spec.mode not in {StrategyMode.PRIMARY, StrategyMode.CANDIDATE}
        )
        if unsupported:
            raise ValueError("reference engine only supports PRIMARY and CANDIDATE strategies")
        validate_primary_uniqueness(item.spec for item in strategies)
        snapshot_hashes = {item.registry_snapshot_hash for item in strategies}
        if len(snapshot_hashes) != 1:
            raise ValueError("all strategies must share one registry snapshot")
        self._strategies = strategies
        self._sink = sink
        self._clock = clock
        self._completed: dict[UUID, tuple[EngineEvaluation, ...]] = {}
        self._process_lock = asyncio.Lock()

    async def start(self, bus: EventBusPort, subject: str) -> SubscriptionPort:
        """Subscribe without depending on a concrete transport implementation."""

        async def handle(envelope: EventEnvelope) -> None:
            await self.process(envelope)

        return await bus.subscribe(subject, handle)

    async def process(self, envelope: EventEnvelope) -> tuple[EngineEvaluation, ...]:
        """Process an envelope once and return the cached outcome on redelivery."""

        async with self._process_lock:
            completed = self._completed.get(envelope.event_id)
            if completed is not None:
                return completed
            context = context_from_event(envelope)
            context_hash = f"sha256:{sha256_digest(context.model_dump(mode='python'))}"
            evaluations: list[EngineEvaluation] = []
            for prepared in self._strategies:
                try:
                    outcome = prepared.evaluate(context)
                except Exception as error:
                    outcome = self._failure_outcome(envelope, prepared, error)
                evaluation = self._evaluation(envelope, prepared, context_hash, outcome)
                await self._sink.emit(evaluation)
                evaluations.append(evaluation)
            result = tuple(evaluations)
            self._completed[envelope.event_id] = result
            return result

    def _failure_outcome(
        self,
        envelope: EventEnvelope,
        prepared: PreparedStrategy,
        error: Exception,
    ) -> ExecutionOutcome:
        now = self._clock.now()
        identity = _stable_id(
            "adapter-failure",
            envelope.event_id,
            prepared.spec.strategy_id,
            prepared.spec.version,
            prepared.compiled_plan_hash,
        )
        trace = DecisionTrace(
            trace_id=identity,
            correlation_id=envelope.correlation_id,
            strategy_id=prepared.spec.strategy_id,
            strategy_version=prepared.spec.version,
            compiled_strategy_hash=prepared.compiled_plan_hash,
            symbol=envelope.subject or "UNKNOWN",
            started_at=now,
            completed_at=now,
            outcome=DecisionOutcome.ERROR,
            reasons=(f"strategy adapter failed: {type(error).__name__}",),
        )
        return ExecutionOutcome(
            execution_id=identity,
            trace=trace,
            audit_confirmed=False,
            eligible=False,
        )

    @staticmethod
    def _evaluation(
        envelope: EventEnvelope,
        prepared: PreparedStrategy,
        context_hash: str,
        outcome: ExecutionOutcome,
    ) -> EngineEvaluation:
        mode = prepared.spec.mode
        eligible = (
            mode is StrategyMode.PRIMARY
            and outcome.audit_confirmed
            and outcome.eligible
            and outcome.trace.outcome is DecisionOutcome.ACCEPTED
        )
        decision_id = _stable_id(
            "decision",
            envelope.event_id,
            prepared.spec.strategy_id,
            prepared.spec.version,
            prepared.compiled_plan_hash,
        )
        return EngineEvaluation(
            decision_id=decision_id,
            event_id=envelope.event_id,
            execution_id=outcome.execution_id,
            strategy_id=prepared.spec.strategy_id,
            strategy_version=prepared.spec.version,
            mode=mode,
            context_hash=context_hash,
            strategy_definition_hash=prepared.strategy_definition_hash,
            compiled_plan_hash=prepared.compiled_plan_hash,
            registry_snapshot_hash=prepared.registry_snapshot_hash,
            trace=outcome.trace,
            audit_confirmed=outcome.audit_confirmed,
            eligible=eligible,
        )


def _stable_id(kind: str, *parts: object) -> UUID:
    serialized = ":".join(str(part) for part in parts)
    return uuid5(NAMESPACE_URL, f"marketbot:{kind}:{serialized}")
