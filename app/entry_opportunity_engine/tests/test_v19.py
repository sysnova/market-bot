# pyright: reportPrivateUsage=false
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.contracts import (
    BarTimeframe,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntrySignalFamily,
    MarketBar,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_v18 import AT, jpm, opening
from app.entry_opportunity_engine.v18 import EntryOpportunityEngineV18
from app.entry_opportunity_engine.v19 import EntryOpportunityEngineV19


def checkpoint() -> EntryMaturityCheckpoint:
    return EntryMaturityCheckpoint(
        level=EntryMaturityLevel.L1,
        reached_at=AT - timedelta(minutes=1),
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
        invalidation=Decimal("90"),
        target=Decimal("150"),
    )


@pytest.mark.parametrize(
    "engine_type,exit_price,closed_at",
    [
        (EntryOpportunityEngineV18, "60.7116", "2026-09-02T16:58:00+00:00"),
        (EntryOpportunityEngineV19, "64.8342", "2026-09-02T14:17:00+00:00"),
    ],
)
async def test_tem_same_entry_replay_against_real_minute_bars(
    engine_type: type[EntryOpportunityEngineV18], exit_price: str, closed_at: str
) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/tem_protection_20260831.json").read_text()
    )
    initial = EntryOpportunity.model_validate(fixture["entry"], strict=False)
    store = InMemoryEntryOpportunityStore()
    await store.save(initial, None)
    engine = engine_type(store=store)
    for row in fixture["bars"]:
        await engine.ingest_bar(
            MarketBar(
                symbol="TEM",
                timeframe=BarTimeframe.MINUTE_1,
                timestamp=datetime.fromisoformat(row["t"]),
                open=Decimal(str(row["o"])),
                high=Decimal(str(row["h"])),
                low=Decimal(str(row["l"])),
                close=Decimal(str(row["c"])),
                volume=Decimal(str(row["v"])),
                source="alpaca",
                feed="sip",
                is_final=True,
            )
        )
    after = await store.load_latest("TEM")
    assert after is not None
    cp = next(c for c in after.checkpoints if c.level is EntryMaturityLevel.L1)
    assert cp.exit_price == Decimal(exit_price)
    assert cp.closed_at == datetime.fromisoformat(closed_at)
    assert cp.invalidation == Decimal("60.7116") and cp.target == Decimal("72.93")
    assert cp.highest_price == Decimal("67.0999")


@pytest.mark.parametrize(
    "prices,expected,outcome",
    [
        (("100", "125", "89", "120"), "90", EntryLegStatus.INVALIDATED),
        (("100", "155", "95", "120"), "150", EntryLegStatus.TARGET_HIT),
    ],
)
def test_original_stop_or_target_wins_before_new_protection(
    prices: tuple[str, str, str, str], expected: str, outcome: EntryLegStatus
) -> None:
    cp = EntryOpportunityEngineV19._mark_checkpoint(checkpoint(), opening(*prices))
    assert cp.exit_price == Decimal(expected) and cp.outcome is outcome
    assert cp.protection_stop is None


@pytest.mark.parametrize(
    "prices,expected,outcome",
    [
        (("110", "151", "99", "120"), "100", EntryLegStatus.PROTECTION_EXIT),
        (("155", "160", "99", "120"), "155", EntryLegStatus.TARGET_HIT),
    ],
)
def test_preexisting_protection_and_target_use_gap_and_stop_first_order(
    prices: tuple[str, str, str, str], expected: str, outcome: EntryLegStatus
) -> None:
    cp = checkpoint().model_copy(
        update={
            "protection_stop": Decimal("100"),
            "protection_updated_at": AT - timedelta(minutes=1),
            "protection_rule_version": "1.0.0",
        }
    )
    closed = EntryOpportunityEngineV19._mark_checkpoint(cp, opening(*prices))
    assert closed.exit_price == Decimal(expected) and closed.outcome is outcome
    assert (
        EntryOpportunityEngineV19._mark_checkpoint(closed, opening("100", "120", "90", "100"))
        == closed
    )


async def test_only_final_regular_minute_bars_can_arm_protection() -> None:
    store = InMemoryEntryOpportunityStore()
    before = jpm()
    await store.save(before, None)
    engine = EntryOpportunityEngineV19(store=store)
    for overrides in (
        {"is_final": False},
        {"timeframe": BarTimeframe.MINUTE_15},
        {"timestamp": AT.replace(hour=12)},
    ):
        assert (
            await engine.ingest_bar(
                opening("358", "363", "357", "362").model_copy(update=overrides)
            )
            == ()
        )
    assert await store.load_latest("JPM") == before


async def test_protection_exit_does_not_sweep_another_core_entry_closed() -> None:
    before = jpm()
    l1 = next(c for c in before.checkpoints if c.level is EntryMaturityLevel.L1)
    independent = l1.model_copy(
        update={
            "checkpoint_id": UUID("0199a100-0000-7000-8000-000000000777"),
            "level": EntryMaturityLevel.L4,
            "entry_price": Decimal("350"),
            "invalidation": Decimal("330"),
            "target": Decimal("400"),
            "zone_low": None,
            "zone_high": None,
        }
    )
    before = before.model_copy(update={"checkpoints": (l1, independent)})
    store = InMemoryEntryOpportunityStore()
    await store.save(before, None)
    engine = EntryOpportunityEngineV19(store=store)
    await engine.ingest_bar(opening("358", "363", "357", "362"))
    await engine.ingest_bar(
        opening("360", "361", "356", "357").model_copy(
            update={"timestamp": AT + timedelta(minutes=1)}
        )
    )
    after = await store.load_active("JPM")
    assert after is not None
    assert after.checkpoints[0].outcome is EntryLegStatus.PROTECTION_EXIT
    assert after.checkpoints[1].status is EntryCheckpointStatus.OPEN


def test_protection_state_requires_complete_persisted_evidence() -> None:
    from pydantic import ValidationError

    raw = checkpoint().model_dump(mode="json")
    raw["protection_stop"] = "100"
    with pytest.raises(ValidationError, match="protection requires"):
        EntryMaturityCheckpoint.model_validate(raw, strict=False)


def test_close_arms_protection_only_for_next_bar_and_preserves_rule_levels() -> None:
    cp = EntryOpportunityEngineV19._mark_checkpoint(
        checkpoint(), opening("100", "113", "95", "110")
    )
    assert cp.status is EntryCheckpointStatus.OPEN
    assert cp.protection_stop == Decimal("100")
    assert cp.invalidation == Decimal("90") and cp.target == Decimal("150")
    cp = EntryMaturityCheckpoint.model_validate_json(cp.model_dump_json())
    following = opening("105", "107", "98", "102").model_copy(
        update={"timestamp": AT + timedelta(minutes=1)}
    )
    closed = EntryOpportunityEngineV19._mark_checkpoint(cp, following)
    assert closed.exit_price == Decimal("100")
    assert closed.outcome is EntryLegStatus.PROTECTION_EXIT


def test_wick_does_not_arm_and_old_mfe_does_not_retroactively_arm() -> None:
    cp = checkpoint().model_copy(update={"highest_price": Decimal("140")})
    marked = EntryOpportunityEngineV19._mark_checkpoint(cp, opening("103", "125", "101", "109"))
    assert marked.protection_stop is None


def test_trailing_uses_initial_risk_never_lowers_and_handles_gap() -> None:
    cp = EntryOpportunityEngineV19._mark_checkpoint(
        checkpoint(), opening("105", "126", "101", "125")
    )
    assert cp.protection_stop == Decimal("115")
    candle = opening("120", "124", "116", "118").model_copy(
        update={"timestamp": AT + timedelta(minutes=1)}
    )
    cp = EntryOpportunityEngineV19._mark_checkpoint(cp, candle)
    assert cp.protection_stop == Decimal("115")
    gap = opening("97", "121", "95", "120").model_copy(
        update={"timestamp": AT + timedelta(minutes=2)}
    )
    closed = EntryOpportunityEngineV19._mark_checkpoint(cp, gap)
    assert closed.exit_price == Decimal("97")
    assert closed.outcome is EntryLegStatus.PROTECTION_EXIT
    assert closed.highest_price == Decimal("126")


@pytest.mark.parametrize("level", [EntryMaturityLevel.ARMED, EntryMaturityLevel.IN_ZONE])
def test_reference_checkpoints_are_not_protected(level: EntryMaturityLevel) -> None:
    cp = checkpoint().model_copy(update={"level": level})
    assert (
        EntryOpportunityEngineV19._mark_checkpoint(
            cp, opening("105", "125", "101", "120")
        ).protection_stop
        is None
    )


async def test_protection_emits_event_and_closes_matching_legs_after_restart() -> None:
    before = jpm()
    store = InMemoryEntryOpportunityStore()
    await store.save(before, None)
    engine = EntryOpportunityEngineV19(store=store)
    events = await engine.ingest_bar(opening("358", "363", "357", "362"))
    assert any("profit_protection_updated" in e.reasons for e in events)
    marked = await store.load_latest("JPM")
    assert marked is not None
    restored = EntryOpportunity.model_validate_json(marked.model_dump_json())
    await store.save(restored, None)
    engine = EntryOpportunityEngineV19(store=store)
    assert await engine.ingest_bar(opening("358", "363", "357", "362")) == ()
    following = opening("360", "361", "356", "357").model_copy(
        update={"timestamp": AT + timedelta(minutes=1)}
    )
    events = await engine.ingest_bar(following)
    assert any("core_entry_l1_protection_exit" in e.reasons for e in events)
    after = await store.load_latest("JPM")
    assert after is not None
    cp = next(c for c in after.checkpoints if c.level is EntryMaturityLevel.L1)
    assert cp.exit_price == Decimal("357.6633")
    assert cp.outcome is EntryLegStatus.PROTECTION_EXIT
    for leg in after.legs:
        old = next(x for x in before.legs if x.leg_id == leg.leg_id)
        if old.status is EntryLegStatus.OPEN:
            assert leg.exit_price == cp.exit_price
            assert leg.status is EntryLegStatus.PROTECTION_EXIT
        else:
            assert leg == old
    assert any(
        c.signal_family is EntrySignalFamily.SWING_TRADE and c.protection_stop is None
        for c in after.checkpoints
    )
