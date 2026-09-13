# pyright: reportPrivateUsage=false
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    NamedValue,
)
from app.entry_watcher import EntryWatcherV57, InMemoryEntryWatchStore
from app.entry_watcher.models import EntryWatch
from app.entry_watcher.tests.test_engine import NOW, WATCH_ID, analysis, long_watch
from app.entry_watcher.v56 import EntryWatcherV56


def context(
    *, price: str, minute: int, approved: bool = True, recovery: bool = False
) -> dict[AnalysisHorizon, AnalysisResult]:
    at = NOW + timedelta(minutes=minute)
    long = long_watch(price=price, as_of=at)
    swing = analysis(
        AnalysisHorizon.SWING,
        classification="recovery" if recovery else "setup",
        verdict=AnalysisVerdict.FAVORABLE if approved else AnalysisVerdict.WATCH,
        direction=long.direction,
        price=price,
        as_of=at,
        extra_metrics=(
            NamedValue(name="swing_entry_gate_passed", value=approved and not recovery),
            NamedValue(name="recovery_entry_gate_passed", value=approved and recovery),
            NamedValue(
                name="entry_lane", value="STRUCTURE_RECOVERY" if recovery else "CONTINUATION"
            ),
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="liquidity_high", value="500"),
            NamedValue(name="target_2r", value="130"),
        ),
    )
    intraday = analysis(
        AnalysisHorizon.INTRADAY,
        classification="reclaim",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=long.direction,
        price=price,
        as_of=at,
        extra_metrics=tuple(
            NamedValue(name=k, value=v)
            for k, v in {
                "confirmation_gate_passed": True,
                "entry_efficiency_gate_passed": True,
                "five_minute_higher_low": True,
                "entry_trigger_level": "111.5",
                "atr14": "1",
            }.items()
        ),
    )
    return {a.horizon: a for a in (long, swing, intraday)}


async def armed() -> tuple[EntryWatcherV57, InMemoryEntryWatchStore, EntryWatch]:
    store = InMemoryEntryWatchStore()
    engine = EntryWatcherV57(store=store, id_factory=lambda: WATCH_ID)
    seed = analysis(
        AnalysisHorizon.LONG_TERM,
        classification="watch_pullback",
        verdict=AnalysisVerdict.WATCH,
        direction=long_watch().direction,
        price="108",
        extra_metrics=(NamedValue(name="distance_to_buy_zone_atr", value="0.5"),),
    )
    await engine.ingest(seed, now=NOW)
    watch = await store.load_active("AAPL")
    assert watch is not None
    return engine, store, watch


@pytest.mark.unit
async def test_impulse_uses_observed_peak_instead_of_structural_liquidity_high() -> None:
    engine, _, watch = await armed()
    state = engine._new_impulse_state(watch, Decimal("115"), context(price="115", minute=1))
    assert state["peak"] == "115"
    assert state["peak_at"] == (NOW + timedelta(minutes=1)).isoformat()


@pytest.mark.unit
@pytest.mark.parametrize("recovery", [False, True])
async def test_causal_pullback_survives_restart_and_requires_approved_swing_lane(
    recovery: bool,
) -> None:
    engine, store, watch = await armed()
    state = engine._new_impulse_state(watch, Decimal("115"), context(price="115", minute=1))
    for minute, price in ((2, "111"), (3, "112")):
        watch = watch.model_copy(update={"anchor_snapshot": {"impulse_pullback_state": state}})
        engine = EntryWatcherV57(store=store)
        engine._latest["AAPL"] = context(price=price, minute=minute, recovery=recovery)
        state = engine._updated_impulse_state(watch, Decimal(price))
    args = {"price": Decimal("112"), "now": NOW + timedelta(minutes=3)}
    assert (
        engine._pullback_entry_levels(
            state, analyses=context(price="112", minute=3, recovery=recovery), **args
        )
        is not None
    )
    assert (
        engine._pullback_entry_levels(
            state, analyses=context(price="112", minute=3, approved=False), **args
        )
        is None
    )


@pytest.mark.unit
async def test_duplicate_observation_cannot_turn_the_trough_into_a_reclaim() -> None:
    engine, _, watch = await armed()
    state = engine._new_impulse_state(watch, Decimal("115"), context(price="115", minute=1))
    engine._latest["AAPL"] = context(price="112", minute=2)
    watch = watch.model_copy(update={"anchor_snapshot": {"impulse_pullback_state": state}})
    state = engine._updated_impulse_state(watch, Decimal("112"))
    watch = watch.model_copy(update={"anchor_snapshot": {"impulse_pullback_state": state}})
    repeated = engine._updated_impulse_state(watch, Decimal("112"))
    assert repeated == state
    assert (
        engine._pullback_entry_levels(
            state,
            price=Decimal("112"),
            analyses=engine._latest["AAPL"],
            now=NOW + timedelta(minutes=2),
        )
        is None
    )


@pytest.mark.unit
async def test_legacy_unentered_impulse_restarts_and_cannot_bypass_through_second_leg() -> None:
    engine, _, watch = await armed()
    watch = watch.model_copy(
        update={
            "status": EntryWatchStatus.IMPULSE_EXTENDED,
            "anchor_snapshot": {
                "impulse_pullback_state": {
                    "schema_version": "1.0.0",
                    "start": "91.56",
                    "peak": "108.21",
                    "pullback_low": "96.2949",
                },
            },
        }
    )
    analyses = context(price="98.815", minute=3)
    engine._latest["AAPL"] = analyses
    state = engine._updated_impulse_state(watch, Decimal("98.815"))
    assert state["start"] == state["peak"] == "98.815"
    assert (
        engine._early_entry_levels(
            watch,
            price=Decimal("112"),
            analyses=context(price="112", minute=3),
            now=NOW + timedelta(minutes=3),
        )
        is None
    )


@pytest.mark.unit
def test_recorded_hood_entry_reproduces_old_levels_but_is_rejected_by_v57() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/hood_l1_20260819.json").read_text())
    analyses = {
        a.horizon: a
        for a in (AnalysisResult.model_validate(a, strict=False) for a in fixture["analyses"])
    }
    args = {
        "price": Decimal(fixture["entry_price"]),
        "analyses": analyses,
        "now": datetime.fromisoformat(fixture["entry_at"]),
    }
    old = EntryWatcherV56(store=InMemoryEntryWatchStore())
    new = EntryWatcherV57(store=InMemoryEntryWatchStore())
    assert old._pullback_entry_levels(fixture["state"], **args) == tuple(
        map(Decimal, ("96.2382", "108.2100", "3.65", "97.9203", "101.8497"))
    )
    assert new._pullback_entry_levels(fixture["state"], **args) is None


@pytest.mark.unit
async def test_full_ingest_confirms_observed_pullback_and_freezes_original_thesis() -> None:
    engine, store, original = await armed()
    transitions = []
    for minute, price in ((1, "115"), (2, "111"), (3, "112")):
        analyses = context(price=price, minute=minute)
        if minute < 3:
            analyses[AnalysisHorizon.INTRADAY] = analyses[AnalysisHorizon.INTRADAY].model_copy(
                update={"verdict": AnalysisVerdict.WATCH}
            )
        engine._latest["AAPL"] = analyses
        transitions.append(
            await engine.ingest(
                analyses[AnalysisHorizon.INTRADAY], now=NOW + timedelta(minutes=minute)
            )
        )
        # Recreate the engine between observations: chronology is durable in the store.
        engine = EntryWatcherV57(store=store, id_factory=lambda: WATCH_ID)
    assert transitions[0] is not None and transitions[0].status is EntryWatchStatus.IMPULSE_EXTENDED
    assert transitions[1] is None
    final = transitions[2]
    assert final is not None and final.status is EntryWatchStatus.EARLY_ENTRY
    assert final.entry_target == Decimal("115")
    current = await store.load_active("AAPL")
    assert current is not None
    assert (current.zone_low, current.zone_high, current.invalidation) == (
        original.zone_low,
        original.zone_high,
        original.invalidation,
    )
    saved = current.anchor_snapshot["impulse_pullback_state"]
    assert saved["schema_version"] == "2.0.0"
    # Already confirmed state must not be reset when the new engine receives another observation.
    next_analyses = context(price="113", minute=4)
    engine._latest["AAPL"] = next_analyses
    await engine.ingest(next_analyses[AnalysisHorizon.INTRADAY], now=NOW + timedelta(minutes=4))
    current = await store.load_active("AAPL")
    assert current is not None and current.anchor_snapshot["impulse_pullback_state"] == saved
