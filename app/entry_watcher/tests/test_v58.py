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
from app.entry_watcher.tests.test_engine import NOW, WATCH_ID
from app.entry_watcher.tests.test_v57 import armed, context
from app.entry_watcher.v58 import EntryWatcherV58


def recorded() -> tuple[datetime, Decimal, dict[AnalysisHorizon, AnalysisResult]]:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/amzn_l1_20260824.json").read_text(encoding="utf-8-sig")
    )
    analyses = {
        a.horizon: a
        for a in (AnalysisResult.model_validate(a, strict=False) for a in fixture["analyses"])
    }
    return datetime.fromisoformat(fixture["entry_at"]), Decimal(fixture["entry_price"]), analyses


def replace_metric(result: AnalysisResult, name: str, value: str | None) -> AnalysisResult:
    metrics = tuple(m for m in result.metrics if m.name != name)
    if value is not None:
        metrics += (NamedValue(name=name, value=value),)
    return result.model_copy(update={"metrics": metrics})


@pytest.mark.unit
async def test_amzn_uses_intraday_pair_instead_of_hybrid_22r() -> None:
    _, _, watch = await armed()
    now, price, analyses = recorded()
    old = EntryWatcherV57(store=InMemoryEntryWatchStore())
    new = EntryWatcherV58(store=InMemoryEntryWatchStore())
    assert old._early_entry_levels(watch, price=price, analyses=analyses, now=now) == (
        Decimal("262.5610"),
        Decimal("277.7360"),
        Decimal("22.06"),
    )
    assert new._early_entry_levels(watch, price=price, analyses=analyses, now=now) == (
        Decimal("262.5610"),
        Decimal("264.2061"),
        Decimal("1.50"),
    )
    assert new._continuation_reward_risk(watch, current_price=price, analyses=analyses) < 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("objective_level", None),
        ("invalidation_level", None),
        ("objective_level", "263"),
        ("invalidation_level", "264"),
        ("invalidation_level", "0"),
        ("objective_level", "NaN"),
        ("objective_level", "Infinity"),
        ("invalidation_level", "-Infinity"),
    ],
)
async def test_missing_or_invalid_intraday_pair_cannot_borrow_swing_levels(
    name: str,
    value: str | None,
) -> None:
    _, _, watch = await armed()
    now, price, analyses = recorded()
    analyses[AnalysisHorizon.INTRADAY] = replace_metric(
        analyses[AnalysisHorizon.INTRADAY], name, value
    )
    engine = EntryWatcherV58(store=InMemoryEntryWatchStore())
    assert engine._early_entry_levels(watch, price=price, analyses=analyses, now=now) is None
    assert engine._continuation_reward_risk(watch, current_price=price, analyses=analyses) is None
    assert not engine._confirmed(analyses, now=now)


@pytest.mark.unit
async def test_live_rr_below_threshold_is_not_rescued_by_swing_or_rounding() -> None:
    _, _, watch = await armed()
    now, price, analyses = recorded()
    # R/R 1.49985 rounds to 1.50, but must fail the unrounded 1.5 gate.
    analyses[AnalysisHorizon.INTRADAY] = replace_metric(
        analyses[AnalysisHorizon.INTRADAY], "objective_level", "264.2059"
    )
    engine = EntryWatcherV58(store=InMemoryEntryWatchStore())
    assert engine._early_entry_levels(watch, price=price, analyses=analyses, now=now) is None


@pytest.mark.unit
@pytest.mark.parametrize("in_zone", [False, True])
async def test_confirmed_transition_and_restart_retain_rule_levels(in_zone: bool) -> None:
    _, store, seed = await armed()
    now, _, analyses = recorded()
    # Old zone touch rules out the continuation path; the two cases exercise L1 and L4.
    watch: EntryWatch = seed.model_copy(
        update={
            "symbol": "AMZN",
            "armed_at": now - timedelta(days=3),
            "updated_at": now - timedelta(days=3),
            "expires_at": now + timedelta(days=3),
            "status": EntryWatchStatus.IN_ZONE if in_zone else EntryWatchStatus.ARMED,
            "zone_low": Decimal("250"),
            "zone_high": Decimal("264" if in_zone else "257.4792"),
            "anchor_snapshot": {},
        }
    )
    # A store port lookup can return a persisted watch without replaying synthetic arming.
    store.watches[watch.watch_id] = watch
    engine = EntryWatcherV58(store=store)
    engine._latest["AMZN"] = analyses
    transition = await engine.ingest(analyses[AnalysisHorizon.INTRADAY], now=now)
    assert transition is not None
    assert transition.status is (
        EntryWatchStatus.TRIGGERED if in_zone else EntryWatchStatus.EARLY_ENTRY
    )
    assert transition.entry_invalidation == Decimal("262.5610")
    assert transition.entry_target == Decimal("264.2061")
    assert "entry_levels_source:INTRADAY" in transition.reasons
    saved = await store.load_latest("AMZN")
    assert saved is not None and saved.invalidation == seed.invalidation
    assert saved.anchor_snapshot["entry_rule_levels"]["analysis_id"] == str(
        analyses[AnalysisHorizon.INTRADAY].analysis_id
    )
    restarted = EntryWatcherV58(store=store)
    later = analyses[AnalysisHorizon.INTRADAY].model_copy(
        update={"as_of": now + timedelta(minutes=1), "verdict": AnalysisVerdict.WATCH}
    )
    await restarted.ingest(later, now=now + timedelta(minutes=1))
    after = await store.load_latest("AMZN")
    assert after is not None
    assert after.anchor_snapshot["entry_rule_levels"] == saved.anchor_snapshot["entry_rule_levels"]


@pytest.mark.unit
async def test_observed_pullback_keeps_own_peak_and_stop_across_restart() -> None:
    _, store, _ = await armed()
    engine = EntryWatcherV58(store=store, id_factory=lambda: WATCH_ID)
    final = None
    for minute, price in ((1, "115"), (2, "111"), (3, "112")):
        analyses = context(price=price, minute=minute)
        if minute < 3:
            analyses[AnalysisHorizon.INTRADAY] = analyses[AnalysisHorizon.INTRADAY].model_copy(
                update={"verdict": AnalysisVerdict.WATCH}
            )
        engine._latest["AAPL"] = analyses
        final = await engine.ingest(
            analyses[AnalysisHorizon.INTRADAY], now=NOW + timedelta(minutes=minute)
        )
        engine = EntryWatcherV58(store=store, id_factory=lambda: WATCH_ID)
    assert final is not None and final.status is EntryWatchStatus.EARLY_ENTRY
    assert final.entry_target == Decimal("115")
    assert final.entry_invalidation == Decimal("110.7500")
    assert "entry_levels_source:OBSERVED_IMPULSE_PULLBACK" in final.reasons
