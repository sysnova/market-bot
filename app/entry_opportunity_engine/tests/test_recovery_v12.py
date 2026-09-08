from datetime import timedelta
from decimal import Decimal

from app.contracts import EntryLegStatus, GeriCountertrendMaturity
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_geri_countertrend_v5 import NOW, countertrend_signal
from app.entry_opportunity_engine.v12 import EntryOpportunityEngineV12


async def test_ct1_tracks_without_a_paper_position_or_pnl_checkpoint() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV12(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT1))
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.legs[0].status is EntryLegStatus.WATCHING
    assert active.legs[0].entry_price is None
    assert active.checkpoints == ()
    assert active.signal_references[0].current_ct is GeriCountertrendMaturity.CT1
    signal = countertrend_signal(GeriCountertrendMaturity.CT2, at=NOW + timedelta(minutes=15))
    signal = signal.model_copy(update={"invalidation": Decimal("79")})
    await engine.ingest_signal(signal)
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.legs[0].status is EntryLegStatus.OPEN
    assert active.legs[0].entry_price == Decimal("85")
    assert len(active.checkpoints) == 1
    assert active.checkpoints[0].countertrend_maturity is GeriCountertrendMaturity.CT2


async def test_ct2_cannot_open_with_bad_geometry_or_insufficient_rr() -> None:
    for target in ("70", "86", "85"):
        store = InMemoryEntryOpportunityStore()
        engine = EntryOpportunityEngineV12(store=store)
        signal = countertrend_signal(GeriCountertrendMaturity.CT2).model_copy(
            update={"targets": (Decimal(target),)}
        )
        assert await engine.ingest_signal(signal) == ()
        assert await store.load_active("AAPL") is None
