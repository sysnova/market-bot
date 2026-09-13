from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    EntryMaturityLevel,
    EntrySignalFamily,
    EntryWatchStatus,
    NamedValue,
    PatternDirection,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_engine import (
    NOW,
    analysis,
    bar,
    entry_signal,
    watch_transition,
)
from app.entry_opportunity_engine.v17 import EntryOpportunityEngineV17


@pytest.mark.parametrize("missing", [None, "objective_level", "invalidation_level"])
async def test_l2_reclaim_uses_its_own_pair_without_swing_or_anchor_fallback(
    missing: str | None,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV17(store=store)
    await engine.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED, watch_id="0195f3a5-9000-7000-8000-000000000021", price="98"
        )
    )
    signal = entry_signal(
        EntrySignalFamily.CORE_ENTRY,
        signal_id="0195f3a5-9000-7000-8000-000000000091",
        setup_id="aapl-l2",
        created_at=NOW + timedelta(minutes=10),
        maturity=EntryMaturityLevel.L2,
    ).model_copy(
        update={
            "entry_price": Decimal("106"),
            "zone_low": Decimal("104"),
            "zone_high": Decimal("106"),
            "invalidation": Decimal("102"),
        }
    )
    await engine.ingest_signal(signal)
    active = await store.load_active("AAPL")
    assert active is not None
    swing = analysis(
        AnalysisHorizon.SWING,
        price="106",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        as_of=NOW + timedelta(minutes=11),
        extra_metrics=(NamedValue(name="target_2r", value=Decimal("999")),),
    )
    await engine.ingest_analysis(swing, now=swing.as_of)
    await engine.ingest_bar(
        bar(timestamp=NOW + timedelta(minutes=15), close="106", low="103", high="107")
    )
    touched = await store.load_active("AAPL")
    assert touched is not None
    l2 = next(cp for cp in touched.checkpoints if cp.level is EntryMaturityLevel.L2)
    assert l2.retested_at == NOW + timedelta(minutes=15)
    values = {
        "confirmation_gate_passed": True,
        "mature_confirmation_gate_passed": True,
        "entry_efficiency_gate_passed": True,
        "five_minute_higher_low": True,
        "entry_trigger_level": Decimal("106.5"),
        "invalidation_level": Decimal("103"),
        "objective_level": Decimal("115"),
    }
    reclaim = analysis(
        AnalysisHorizon.INTRADAY,
        price="107",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        as_of=NOW + timedelta(minutes=20),
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000093"),
        extra_metrics=tuple(NamedValue(name=k, value=v) for k, v in values.items() if k != missing),
    )
    await engine.ingest_analysis(reclaim, now=reclaim.as_of)
    latest = await store.load_latest("AAPL")
    assert latest is not None
    l4 = [cp for cp in latest.checkpoints if cp.level is EntryMaturityLevel.L4]
    if missing is not None:
        assert l4 == []
    else:
        assert len(l4) == 1
        assert (l4[0].invalidation, l4[0].target) == (Decimal("103"), Decimal("115"))
