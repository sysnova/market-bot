from datetime import timedelta
from decimal import Decimal

import fakeredis
import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    LOCAL_ALERT_EVENT,
    ORDER_FLOW_STATE_EVENT,
    EntrySignal,
    EventEnvelope,
    OrderFlowStateKind,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.integration.deferred_short_runtime import DeferredShortRuntime, RedisShortStateStore
from app.integration.redis_ticker_cache import RedisTickerCache
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.tests.test_v11 import alert
from app.leveraged_thesis_engine.v11 import LeveragedThesisEngineV11


class Publisher:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []
        self.fail_entry = False

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append(envelope)
        if self.fail_entry and envelope.event_type == ENTRY_SIGNAL_EVENT:
            self.fail_entry = False
            raise RuntimeError("publication acknowledgement lost")


def envelope(payload: object, event_type: str) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type, occurred_at=NOW, source="test", subject="ASTS", payload=payload
    )


@pytest.mark.asyncio
async def test_pending_survives_startup_cleanup_and_records_one_delayed_astn_purchase() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    store = RedisShortStateStore(cache.redis, cache.namespace)
    publisher = Publisher()
    clock = FrozenClock(NOW)
    engine = LeveragedThesisEngineV11()
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(envelope(alert(), LOCAL_ALERT_EVENT))
    assert not [e for e in publisher.events if e.event_type == ENTRY_SIGNAL_EVENT]
    cache.reset_for_startup()
    clock.advance(timedelta(minutes=12))
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(
        envelope(
            _flow("ASTS", OrderFlowStateKind.NEUTRAL, occurred_at=clock.now()),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    await runtime.handle(
        envelope(
            _flow(
                "ASTN",
                OrderFlowStateKind.BUY_PRESSURE,
                bid=Decimal("5"),
                ask=Decimal("5.01"),
                occurred_at=clock.now(),
            ),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    await runtime.tick()
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    entries = [e for e in publisher.events if e.event_type == ENTRY_SIGNAL_EVENT]
    assert len(entries) == 1
    assert len({e.event_id for e in publisher.events}) == len(publisher.events)
    signal = entries[0].payload
    assert isinstance(signal, EntrySignal)
    assert signal.symbol == "ASTN"
    opportunities = EntryOpportunityEngineV2(store=InMemoryEntryOpportunityStore())
    events = await opportunities.ingest_signal(signal)
    assert events


@pytest.mark.asyncio
async def test_ack_failure_retries_same_purchase_identity_after_restart() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    store = RedisShortStateStore(cache.redis, cache.namespace)
    publisher = Publisher()
    clock = FrozenClock(NOW)
    engine = LeveragedThesisEngineV11()
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(envelope(alert(), LOCAL_ALERT_EVENT))
    await runtime.handle(
        envelope(_flow("ASTS", OrderFlowStateKind.NEUTRAL), ORDER_FLOW_STATE_EVENT)
    )
    publisher.fail_entry = True
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await runtime.handle(
            envelope(
                _flow(
                    "ASTN", OrderFlowStateKind.BUY_PRESSURE, bid=Decimal("5"), ask=Decimal("5.01")
                ),
                ORDER_FLOW_STATE_EVENT,
            )
        )
    await DeferredShortRuntime(engine, publisher, store, clock).restore()
    entries = [e for e in publisher.events if e.event_type == ENTRY_SIGNAL_EVENT]
    assert len(entries) == 2
    assert entries[0].event_id == entries[1].event_id
    assert entries[0].payload == entries[1].payload


@pytest.mark.asyncio
async def test_instrument_first_evidence_survives_restart_and_buys_at_current_ask() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    store = RedisShortStateStore(cache.redis, cache.namespace)
    publisher = Publisher()
    clock = FrozenClock(NOW - timedelta(minutes=20))
    engine = LeveragedThesisEngineV11()
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(
        envelope(
            _flow(
                "ASTN",
                OrderFlowStateKind.BUY_PRESSURE,
                bid=Decimal("5"),
                ask=Decimal("5.01"),
                occurred_at=clock.now(),
            ),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    saved = await store.get("ASTS")
    assert saved.entry_evidence is not None and saved.intent is None
    assert not publisher.events
    cache.reset_for_startup()
    clock.advance(timedelta(minutes=20))
    runtime = DeferredShortRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(
        envelope(
            _flow("ASTN", OrderFlowStateKind.NEUTRAL, bid=Decimal("6"), ask=Decimal("6.01")),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    await runtime.handle(
        envelope(_flow("ASTS", OrderFlowStateKind.NEUTRAL), ORDER_FLOW_STATE_EVENT)
    )
    await runtime.handle(envelope(alert(), LOCAL_ALERT_EVENT))
    await runtime.tick()
    await DeferredShortRuntime(engine, publisher, store, clock).restore()
    entries = [event for event in publisher.events if event.event_type == ENTRY_SIGNAL_EVENT]
    assert len(entries) == 1
    saved = await store.get("ASTS")
    assert saved.assessment is not None
    assert saved.assessment.instrument_ask == Decimal("6.01")
    assert "instrument_prior_entry_confirmed" in saved.assessment.reasons
    assert any(reason.startswith("instrument_entry_at:") for reason in saved.assessment.reasons)
    signal = entries[0].payload
    assert isinstance(signal, EntrySignal)
    opportunities = EntryOpportunityEngineV2(store=InMemoryEntryOpportunityStore())
    assert await opportunities.ingest_signal(signal)
