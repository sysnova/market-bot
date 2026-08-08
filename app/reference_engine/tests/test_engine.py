from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    DecisionOutcome,
    DecisionTrace,
    EventEnvelope,
    MarketSession,
    NamedValue,
    PipelineStep,
    RuleBinding,
    ScoringPolicy,
    ScoringWeight,
    StrategyMode,
    StrategyPolicies,
    StrategySpec,
    new_uuid7,
)
from app.reference_engine import (
    EngineEvaluation,
    ExecutionOutcome,
    PreparedStrategy,
    ReferenceEngine,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(strategy_id: str, mode: StrategyMode, version: str) -> StrategySpec:
    rule_id = f"rule-{strategy_id}"
    return StrategySpec(
        strategy_id=strategy_id,
        version=version,
        family="synthetic",
        engine="reference_engine",
        run_id="synthetic-demo",
        mode=mode,
        rule_pack_hash="sha256:" + "a" * 64,
        pipeline=(PipelineStep(step_id="only", rule_id=rule_id, rule_version="1.0.0"),),
        bindings=(RuleBinding(rule_id=rule_id),),
        policies=StrategyPolicies(minimum_passing_rules=1),
        scoring=ScoringPolicy(
            pass_threshold=Decimal("1"),
            weights=(ScoringWeight(rule_id=rule_id, weight=Decimal("1")),),
        ),
    )


def _event() -> EventEnvelope:
    return EventEnvelope(
        event_id=new_uuid7(),
        event_type="synthetic.price",
        occurred_at=NOW,
        source="integration-test",
        market_session=MarketSession.CONTINUOUS,
        subject="TEST",
        payload={
            "symbol": "TEST",
            "timeframe": "1m",
            "values": {"price": Decimal("12")},
        },
        attributes=(NamedValue(name="run_id", value="synthetic-demo"),),
    )


class RecordingSink:
    def __init__(self) -> None:
        self.evaluations: list[EngineEvaluation] = []

    async def emit(self, evaluation: EngineEvaluation) -> None:
        self.evaluations.append(evaluation)


def _prepared(
    spec: StrategySpec,
    seen_contexts: list[int],
    *,
    eligible: bool,
    audit_confirmed: bool,
) -> PreparedStrategy:
    def evaluate(context: object) -> ExecutionOutcome:
        seen_contexts.append(id(context))
        trace = DecisionTrace(
            trace_id=new_uuid7(),
            strategy_id=spec.strategy_id,
            strategy_version=spec.version,
            compiled_strategy_hash="sha256:" + "b" * 64,
            symbol="TEST",
            started_at=NOW,
            completed_at=NOW,
            outcome=DecisionOutcome.ACCEPTED,
            score=Decimal("1"),
            reasons=("accepted",),
        )
        return ExecutionOutcome(
            execution_id=new_uuid7(),
            trace=trace,
            audit_confirmed=audit_confirmed,
            eligible=eligible,
        )

    return PreparedStrategy(
        spec=spec,
        strategy_definition_hash="sha256:" + "c" * 64,
        compiled_plan_hash="sha256:" + "b" * 64,
        registry_snapshot_hash="sha256:" + "d" * 64,
        evaluate=evaluate,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_frozen_context_drives_primary_and_candidate_with_idempotent_output() -> None:
    seen_contexts: list[int] = []
    primary = _prepared(
        _spec("primary-a", StrategyMode.PRIMARY, "1.0.0"),
        seen_contexts,
        eligible=True,
        audit_confirmed=True,
    )
    candidate = _prepared(
        _spec("candidate-b", StrategyMode.CANDIDATE, "2.0.0"),
        seen_contexts,
        eligible=False,
        audit_confirmed=False,
    )
    sink = RecordingSink()
    engine = ReferenceEngine((primary, candidate), sink, FrozenClock(NOW))
    event = _event()

    first = await engine.process(event)
    replay = await engine.process(event)

    assert replay == first
    assert len(first) == 2
    assert len(sink.evaluations) == 2
    assert len(set(seen_contexts)) == 1
    assert first[0].context_hash == first[1].context_hash
    assert first[0].decision_id != first[1].decision_id
    assert first[0].eligible and first[0].audit_confirmed
    assert not first[1].eligible
    assert all(item.event_id == event.event_id for item in first)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidate_cannot_become_eligible_even_if_adapter_claims_it_is() -> None:
    candidate = _prepared(
        _spec("candidate-b", StrategyMode.CANDIDATE, "2.0.0"),
        [],
        eligible=True,
        audit_confirmed=True,
    )
    sink = RecordingSink()
    engine = ReferenceEngine((candidate,), sink, FrozenClock(NOW))

    result = await engine.process(_event())

    assert not result[0].eligible


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unexpected_strategy_failure_is_traced_and_does_not_stop_other_strategy() -> None:
    primary_spec = _spec("primary-a", StrategyMode.PRIMARY, "1.0.0")
    primary = PreparedStrategy(
        spec=primary_spec,
        strategy_definition_hash="sha256:" + "c" * 64,
        compiled_plan_hash="sha256:" + "b" * 64,
        registry_snapshot_hash="sha256:" + "d" * 64,
        evaluate=lambda _context: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    candidate = _prepared(
        _spec("candidate-b", StrategyMode.CANDIDATE, "2.0.0"),
        [],
        eligible=False,
        audit_confirmed=False,
    )
    sink = RecordingSink()
    engine = ReferenceEngine((primary, candidate), sink, FrozenClock(NOW))

    results = await engine.process(_event())

    assert results[0].trace.outcome is DecisionOutcome.ERROR
    assert results[0].trace.reasons == ("strategy adapter failed: RuntimeError",)
    assert not results[0].eligible
    assert results[1].trace.outcome is DecisionOutcome.ACCEPTED
    assert len(sink.evaluations) == 2


class SubscribableBus:
    def __init__(self) -> None:
        self.handler: Callable[[EventEnvelope], object] | None = None

    async def subscribe(
        self, subject: str, handler: Callable[..., object], **_kwargs: object
    ) -> object:
        assert subject == "marketbot.synthetic.input"
        self.handler = handler
        return object()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_consumes_event_envelopes_through_event_bus_port() -> None:
    primary = _prepared(
        _spec("primary-a", StrategyMode.PRIMARY, "1.0.0"),
        [],
        eligible=True,
        audit_confirmed=True,
    )
    sink = RecordingSink()
    bus = SubscribableBus()
    engine = ReferenceEngine((primary,), sink, FrozenClock(NOW))

    await engine.start(bus, "marketbot.synthetic.input")
    assert bus.handler is not None
    await bus.handler(_event())  # type: ignore[misc]

    assert len(sink.evaluations) == 1
