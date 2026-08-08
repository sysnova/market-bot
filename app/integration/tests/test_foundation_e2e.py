"""End-to-end proof for the synthetic Foundation + Rule Platform milestone."""

from datetime import UTC, datetime
from pathlib import Path

from app.audit_engine import AuditStream
from app.common.clock import FrozenClock
from app.contracts import EventEnvelope, MarketSession, StrategyMode
from app.event_bus import InMemoryEventBus
from app.integration.foundation import prepare_foundation_engine


async def test_primary_and_candidate_share_context_and_audit_idempotently(
    tmp_path: Path,
) -> None:
    clock = FrozenClock(datetime(2026, 7, 25, 14, 30, tzinfo=UTC))
    engine, audit, plans = prepare_foundation_engine(tmp_path, clock)
    bus = InMemoryEventBus()
    subscription = await engine.start(bus, "synthetic.input")
    event = EventEnvelope(
        event_type="synthetic.input",
        occurred_at=clock.now(),
        source="foundation_e2e",
        market_session=MarketSession.REGULAR,
        subject="AAPL",
        payload={
            "symbol": "AAPL",
            "timeframe": "1m",
            "run_id": "synthetic-demo",
            "values": {"price": 12},
        },
    )

    try:
        await bus.publish("synthetic.input", event)
        await bus.join()
        evaluations = audit.evaluations
        assert len(evaluations) == 2
        primary = next(item for item in evaluations if item.mode is StrategyMode.PRIMARY)
        candidate = next(item for item in evaluations if item.mode is StrategyMode.CANDIDATE)

        assert primary.context_hash == candidate.context_hash
        assert primary.registry_snapshot_hash == candidate.registry_snapshot_hash
        assert primary.compiled_plan_hash != candidate.compiled_plan_hash
        assert primary.trace.steps[-1].result is not None
        assert candidate.trace.steps[-1].result is not None
        assert primary.trace.steps[-1].result.rule_version == "1.0.0"
        assert candidate.trace.steps[-1].result.rule_version == "2.0.0"
        assert primary.audit_confirmed is True
        assert primary.eligible is True
        assert candidate.audit_confirmed is False
        assert candidate.eligible is False

        cached = await engine.process(event)
        await bus.publish("synthetic.input", event)
        await bus.join()
        assert cached == evaluations
        assert audit.evaluations == evaluations
        assert len(audit.replay(AuditStream.RULE_TRACES)) == 2
        assert len(audit.replay(AuditStream.DECISIONS)) == 2
        assert plans[0].registry_snapshot_hash == plans[1].registry_snapshot_hash
    finally:
        await subscription.unsubscribe()
        await bus.close()
        audit.close()
