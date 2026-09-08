from datetime import timedelta

from app.contracts import EntryLegStatus, GeriCountertrendMaturity
from app.entry_opportunity_engine import EntryOpportunityEngineV13, InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_geri_countertrend_v5 import NOW, countertrend_signal


async def test_unfilled_recovery_survives_pending_acceptance_at_session_close() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV13(store=store)
    watch = countertrend_signal(GeriCountertrendMaturity.CT1).model_copy(
        update={"policy_version": "1.10.0"}
    )
    await engine.ingest_signal(watch)
    pending = countertrend_signal(
        None,
        at=NOW + timedelta(hours=5),
        price="96",
        reasons=("resistance_acceptance_pending", "regular_session_close"),
    ).model_copy(update={"policy_version": "1.10.0"})
    await engine.ingest_signal(pending)
    active = await store.load_active("AAPL")
    assert active is not None and active.legs[0].status is EntryLegStatus.WATCHING
    assert active.checkpoints == ()
    await engine.ingest_signal(watch)
    assert await store.load_active("AAPL") == active


async def test_structural_invalidation_still_ends_unfilled_recovery() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV13(store=store)
    watch = countertrend_signal(GeriCountertrendMaturity.CT1).model_copy(
        update={"policy_version": "1.10.0"}
    )
    await engine.ingest_signal(watch)
    invalid = countertrend_signal(
        None,
        at=NOW + timedelta(minutes=15),
        price="77",
        reasons=("countertrend_invalidated",),
    ).model_copy(update={"policy_version": "1.10.0"})
    await engine.ingest_signal(invalid)
    assert await store.load_active("AAPL") is None
