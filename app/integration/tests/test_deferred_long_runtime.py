from datetime import timedelta
from decimal import Decimal

import fakeredis
import pytest

from app.common.clock import FrozenClock
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    ORDER_FLOW_STATE_EVENT,
    EntrySignal,
    EntrySignalFamily,
    OrderFlowStateKind,
)
from app.entry_opportunity_engine import EntryOpportunityEngineV2
from app.entry_opportunity_engine.memory import InMemoryEntryOpportunityStore
from app.integration.deferred_long_runtime import DeferredLongRuntime, RedisLongStateStore
from app.integration.redis_ticker_cache import RedisTickerCache
from app.integration.tests.test_deferred_short_runtime import Publisher, envelope
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.tests.test_v12 import long_signal
from app.leveraged_thesis_engine.v12 import LeveragedThesisEngineV12


@pytest.mark.asyncio
async def test_astx_evidence_survives_restart_both_triggers_create_one_opportunity() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    store = RedisLongStateStore(cache.redis, cache.namespace)
    publisher = Publisher()
    clock = FrozenClock(NOW - timedelta(minutes=20))
    engine = LeveragedThesisEngineV12()
    runtime = DeferredLongRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(
        envelope(
            _flow(
                "ASTX",
                OrderFlowStateKind.BUY_PRESSURE,
                bid=Decimal("5"),
                ask=Decimal("5.01"),
                occurred_at=clock.now(),
            ),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    assert not publisher.events
    cache.reset_for_startup()
    clock.advance(timedelta(minutes=20))
    runtime = DeferredLongRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(
        envelope(
            _flow("ASTX", OrderFlowStateKind.NEUTRAL, bid=Decimal("6"), ask=Decimal("6.01")),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    await runtime.handle(
        envelope(_flow("ASTS", OrderFlowStateKind.NEUTRAL), ORDER_FLOW_STATE_EVENT)
    )
    source = long_signal()
    await runtime.handle(envelope(source, ENTRY_SIGNAL_EVENT))
    await runtime.handle(envelope(long_signal(EntrySignalFamily.CORE_ENTRY), ENTRY_SIGNAL_EVENT))
    await DeferredLongRuntime(engine, publisher, store, clock).restore()
    entries = [event for event in publisher.events if event.event_type == ENTRY_SIGNAL_EVENT]
    assert len(entries) == 1
    signal = entries[0].payload
    assert isinstance(signal, EntrySignal)
    assert signal.symbol == "ASTX" and signal.entry_price == Decimal("6.01")
    assert source.signal_id in signal.source_event_ids
    assert "exposure:LONG_2X" in signal.reasons
    opportunities = EntryOpportunityEngineV2(store=InMemoryEntryOpportunityStore())
    assert await opportunities.ingest_signal(signal)
    clock.advance(timedelta(seconds=1))
    await runtime.handle(
        envelope(
            _flow("ASTS", OrderFlowStateKind.NEUTRAL, price=Decimal("60"), occurred_at=clock.now()),
            ORDER_FLOW_STATE_EVENT,
        )
    )
    state = await store.get("ASTS")
    assert state.assessment is not None and state.assessment.state.value == "CANCELLED"
    assert await opportunities.ingest_leveraged_cancellation(state.assessment)


def test_operational_assembly_subscribes_to_native_asts_confirmations() -> None:
    from pathlib import Path

    from app.contracts import entry_signal_subject
    from app.integration.engine_assembly import MarketBotAssembly
    from app.integration.leveraged_thesis_composition import leveraged_thesis_source_subjects

    root = Path(__file__).resolve().parents[3]
    engine = MarketBotAssembly.from_path(
        root / "configs/marketbot/7.74.0.yaml"
    ).build_leveraged_thesis()
    assert isinstance(engine, LeveragedThesisEngineV12)
    subjects = leveraged_thesis_source_subjects(engine)
    for family in (
        EntrySignalFamily.CORE_ENTRY,
        EntrySignalFamily.CORE_RECOVERY,
        EntrySignalFamily.SWING_TRADE,
    ):
        assert entry_signal_subject(family, "ASTS") in subjects
    assert entry_signal_subject(EntrySignalFamily.LEVERAGED_THESIS, "ASTX") not in subjects


@pytest.mark.asyncio
async def test_long_publication_retry_reuses_identity_after_restart() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    store = RedisLongStateStore(cache.redis, cache.namespace)
    publisher = Publisher()
    clock = FrozenClock(NOW)
    engine = LeveragedThesisEngineV12()
    runtime = DeferredLongRuntime(engine, publisher, store, clock)
    await runtime.restore()
    await runtime.handle(envelope(long_signal(), ENTRY_SIGNAL_EVENT))
    await runtime.handle(
        envelope(_flow("ASTS", OrderFlowStateKind.NEUTRAL), ORDER_FLOW_STATE_EVENT)
    )
    publisher.fail_entry = True
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await runtime.handle(
            envelope(
                _flow(
                    "ASTX", OrderFlowStateKind.BUY_PRESSURE, bid=Decimal("5"), ask=Decimal("5.01")
                ),
                ORDER_FLOW_STATE_EVENT,
            )
        )
    await DeferredLongRuntime(engine, publisher, store, clock).restore()
    entries = [event for event in publisher.events if event.event_type == ENTRY_SIGNAL_EVENT]
    assert len(entries) == 2
    assert entries[0].event_id == entries[1].event_id
    assert entries[0].payload == entries[1].payload


@pytest.mark.asyncio
async def test_live_composition_routes_entry_subjects_to_long_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.contracts import entry_signal_subject
    from app.integration import leveraged_thesis_composition as composition

    monkeypatch.delenv("MARKETBOT_DEFINITION_PATH", raising=False)
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    bus = SimpleNamespace(
        get_last=AsyncMock(return_value=None),
        publish=AsyncMock(),
        subscribe=AsyncMock(return_value=SimpleNamespace(unsubscribe=AsyncMock())),
        close=AsyncMock(),
    )
    monkeypatch.setattr(composition.NatsJetStreamEventBus, "connect", AsyncMock(return_value=bus))
    monkeypatch.setattr(composition, "shared_cache_client", lambda: cache)
    monkeypatch.setattr(composition, "context_store", lambda *args, **kwargs: {})
    monkeypatch.setattr(composition.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await composition.run_leveraged_thesis_process()
    for family in (
        EntrySignalFamily.CORE_ENTRY,
        EntrySignalFamily.CORE_RECOVERY,
        EntrySignalFamily.SWING_TRADE,
    ):
        subject = entry_signal_subject(family, "ASTS")
        handlers = [
            call.args[1] for call in bus.subscribe.call_args_list if call.args[0] == subject
        ]
        assert len(handlers) == 1
        assert isinstance(handlers[0].__self__, DeferredLongRuntime)
