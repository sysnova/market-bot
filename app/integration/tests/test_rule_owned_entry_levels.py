# pyright: reportPrivateUsage=false
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import AnalysisHorizon, EntryMaturityLevel, EntryWatchStatus
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_watcher.tests.test_v57 import armed
from app.entry_watcher.tests.test_v58 import recorded, replace_metric
from app.integration.engine_assembly import MarketBotAssembly


@pytest.mark.unit
@pytest.mark.parametrize("armed_first", [False, True])
async def test_amzn_transition_persists_same_intraday_pair_in_opportunity(
    armed_first: bool,
) -> None:
    assembly = MarketBotAssembly.from_path(Path("configs/marketbot/7.62.0.yaml"))
    _, watch_store, seed = await armed()
    now, _, analyses = recorded()
    watch = seed.model_copy(
        update={
            "symbol": "AMZN",
            "armed_at": now - timedelta(days=3),
            "updated_at": now - timedelta(days=3),
            "expires_at": now + timedelta(days=3),
            "status": EntryWatchStatus.ARMED,
            "zone_low": Decimal("250"),
            "zone_high": Decimal("257.4792"),
            "anchor_snapshot": {},
        }
    )
    watch_store.watches[watch.watch_id] = watch
    watcher = assembly.build_entry_watcher(store=watch_store)
    watcher._latest["AMZN"] = analyses
    transition = await watcher.ingest(analyses[AnalysisHorizon.INTRADAY], now=now)
    assert transition is not None and transition.status is EntryWatchStatus.EARLY_ENTRY
    store = InMemoryEntryOpportunityStore()
    consumer = assembly.build_entry_opportunity(store=store)
    if armed_first:
        from app.contracts import new_uuid7

        await consumer.ingest_transition(
            transition.model_copy(
                update={
                    "transition_id": new_uuid7(),
                    "status": EntryWatchStatus.ARMED,
                    "occurred_at": now - timedelta(minutes=5),
                    "entry_invalidation": None,
                    "entry_target": None,
                }
            )
        )
    events = await consumer.ingest_transition(transition)
    assert events
    opened = await store.load_active("AMZN")
    assert opened is not None
    entry = next(cp for cp in opened.checkpoints if cp.level is EntryMaturityLevel.L1)
    assert (entry.entry_price, entry.invalidation, entry.target) == (
        Decimal("263.219"),
        Decimal("262.5610"),
        Decimal("264.2061"),
    )
    later = replace_metric(analyses[AnalysisHorizon.SWING], "target_2r", "999").model_copy(
        update={"as_of": now + timedelta(minutes=1)}
    )
    await consumer.ingest_analysis(later, now=now + timedelta(minutes=1))
    after = await store.load_latest("AMZN")
    assert after is not None
    same = next(cp for cp in after.checkpoints if cp.checkpoint_id == entry.checkpoint_id)
    assert (same.invalidation, same.target) == (entry.invalidation, entry.target)
