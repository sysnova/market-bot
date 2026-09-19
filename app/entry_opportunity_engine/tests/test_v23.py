from datetime import timedelta

import pytest

from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_short_v14 import NOW, alert
from app.entry_opportunity_engine.v22 import EntryOpportunityEngineV22
from app.entry_opportunity_engine.v23 import EntryOpportunityEngineV23


@pytest.mark.asyncio
async def test_redelivered_confirmed_short_is_persisted_using_original_alert_time() -> None:
    delivered_at = NOW + timedelta(minutes=20)
    previous_store = InMemoryEntryOpportunityStore()
    previous = EntryOpportunityEngineV22(
        store=previous_store,
        short_symbols=("ASTS",),
        now=lambda: delivered_at,
    )
    assert await previous.ingest_alert(alert()) == ()

    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV23(
        store=store,
        short_symbols=("ASTS",),
        now=lambda: delivered_at,
    )

    events = await engine.ingest_alert(alert())

    assert len(events) == 1
    assert events[0].opportunity.armed_at == NOW
    assert events[0].opportunity.updated_at == NOW
    assert await store.load_active("ASTS") is not None


@pytest.mark.asyncio
async def test_old_or_cross_session_short_alert_is_not_recovered() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV23(
        store=store,
        short_symbols=("ASTS",),
        now=lambda: NOW + timedelta(days=1),
    )

    assert await engine.ingest_alert(alert()) == ()
    assert await store.load_active("ASTS") is None
