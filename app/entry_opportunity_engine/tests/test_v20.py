import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    BarTimeframe,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunity,
    EntrySignal,
    EntrySignalFamily,
    MarketBar,
    NamedValue,
    PatternDirection,
)
from app.contracts.entry_opportunity import RecoveryExitState
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_engine import analysis
from app.entry_opportunity_engine.v20 import EntryOpportunityEngineV20

AT = datetime(2026, 9, 3, 15, 30, tzinfo=UTC)


def signal() -> EntrySignal:
    evidence = analysis(
        AnalysisHorizon.SWING,
        price="100",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        as_of=AT - timedelta(minutes=15),
        extra_metrics=(
            NamedValue(name="classification", value="recovery"),
            NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
            NamedValue(name="swing_entry_gate_passed", value=True),
            NamedValue(name="recovery_entry_gate_passed", value=True),
            NamedValue(name="recovery_setup_id", value="recovery:test"),
            NamedValue(name="recovery_avwap", value=Decimal("98")),
            NamedValue(name="recovery_breakout_level", value=Decimal("99")),
            NamedValue(name="recovery_intraday_rebound_low", value=Decimal("96")),
            NamedValue(name="recovery_reaction_low", value=Decimal("92")),
            NamedValue(name="recovery_pivot_at", value=AT - timedelta(days=2)),
        ),
    ).model_copy(update={"engine_id": "swing"})
    return EntrySignal(
        family=EntrySignalFamily.CORE_RECOVERY,
        maturity=EntryMaturityLevel.L2,
        symbol="AAPL",
        created_at=AT,
        setup_id="recovery:test",
        entry_price=Decimal("100"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("98"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        targets=(Decimal("120"),),
        policy_id="core-recovery",
        policy_version="3.5.0",
        reasons=("swing_recovery_l2_confirmed",),
        entry_analyses=(evidence,),
    )


def minute(index: int, close: str, open_: str = "99") -> MarketBar:
    p, o = Decimal(close), Decimal(open_)
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=AT + timedelta(minutes=index),
        open=o,
        high=max(o, p) + Decimal(".1"),
        low=min(o, p) - Decimal(".1"),
        close=p,
        volume=Decimal("1000"),
        source="fixture",
        feed="sip",
        is_final=True,
    )


async def test_two_complete_lower_closes_below_entry_levels_exit_next_open_after_restart() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    # First entry bucket is not complete after entry, so begin at the next quarter.
    for i in range(15, 45):
        await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    active = await store.load_active("AAPL")
    assert active is not None
    cp = active.checkpoints[0]
    assert cp.status is EntryCheckpointStatus.OPEN
    assert cp.recovery_exit is not None and cp.recovery_exit.pending_exit_at == AT + timedelta(
        minutes=45
    )
    assert cp.entry_analyses == signal().entry_analyses
    await store.save(type(active).model_validate_json(active.model_dump_json()), None)
    engine = EntryOpportunityEngineV20(store=store)
    events = await engine.ingest_bar(minute(45, "95", "96.8"))
    after = await store.load_latest("AAPL")
    assert after is not None
    assert after.checkpoints[0].exit_price == Decimal("96.8")
    assert after.checkpoints[0].outcome is EntryLegStatus.RECOVERY_FAILED
    assert after.legs[0].exit_price == Decimal("96.8")
    assert any("core_recovery_l2_recovery_failed" in e.reasons for e in events)


async def test_one_bad_close_then_reclaim_does_not_exit() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 60):
        await engine.ingest_bar(minute(i, "97" if i < 30 or i >= 45 else "99.5"))
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit.pending_exit_at is None


async def test_missing_minute_prevents_false_consecutive_confirmation() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 45):
        if i != 20:
            await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit.pending_exit_at is None


async def test_legacy_entry_without_evidence_is_not_reconstructed_from_future_data() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal().model_copy(update={"entry_analyses": ()}))
    for i in range(15, 45):
        await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit is None


@pytest.mark.parametrize("first,second", [("97", "97.5"), ("98.6", "98.5")])
async def test_higher_second_close_or_holding_original_avwap_does_not_exit(
    first: str, second: str
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 45):
        await engine.ingest_bar(minute(i, first if i < 30 else second))
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit.pending_exit_at is None


async def test_partial_bucket_and_first_warning_survive_restart_without_duplicate_closes() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 40):
        await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    active = await store.load_active("AAPL")
    assert active is not None
    await store.save(type(active).model_validate_json(active.model_dump_json()), None)
    engine = EntryOpportunityEngineV20(store=store)
    assert await engine.ingest_bar(minute(39, "96")) == ()
    for i in range(40, 45):
        await engine.ingest_bar(minute(i, "97"))
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit.pending_exit_at is not None


@pytest.mark.parametrize(
    "gap,outcome", [("89", EntryLegStatus.INVALIDATED), ("125", EntryLegStatus.TARGET_HIT)]
)
async def test_pending_exit_respects_opening_gap_and_synchronizes_legs(
    gap: str, outcome: EntryLegStatus
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 45):
        await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    await engine.ingest_bar(minute(45, "95", gap))
    after = await store.load_latest("AAPL")
    assert after is not None
    assert after.checkpoints[0].outcome is outcome
    assert after.checkpoints[0].exit_price == after.legs[0].exit_price == Decimal(gap)
    assert after.checkpoints[0].highest_price == after.legs[0].highest_price
    assert after.checkpoints[0].lowest_price == after.legs[0].lowest_price


@pytest.mark.parametrize("delta", [timedelta(minutes=1), timedelta(hours=-2)])
async def test_future_or_stale_evidence_cannot_arm_recovery_exit(delta: timedelta) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    sig = signal()
    sig = sig.model_copy(
        update={"entry_analyses": (sig.entry_analyses[0].model_copy(update={"as_of": AT + delta}),)}
    )
    await engine.ingest_signal(sig)
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit is None


@pytest.mark.parametrize(
    "reconstructed,price,at",
    [
        (False, "110.3523", "2026-09-08T13:42:00+00:00"),
        (True, "112.35", "2026-09-04T19:45:00+00:00"),
    ],
)
async def test_se_conditional_replay_and_legacy_snapshot_behavior(
    reconstructed: bool, price: str, at: str
) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/se_recovery_conditional_20260903.json").read_text()
    )
    initial = EntryOpportunity.model_validate(fixture["entry"], strict=False)
    if reconstructed:
        state = RecoveryExitState.model_validate(fixture["reconstructed_evidence"], strict=False)
        assert all(
            datetime.fromisoformat(b["t"]) < initial.armed_at for b in fixture["daily_source"]
        )
        initial = initial.model_copy(
            update={
                "checkpoints": (initial.checkpoints[0].model_copy(update={"recovery_exit": state}),)
            }
        )
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await store.save(initial, None)
    reasons = []
    for b in fixture["bars"]:
        events = await engine.ingest_bar(
            MarketBar(
                symbol="SE",
                timeframe=BarTimeframe.MINUTE_1,
                timestamp=datetime.fromisoformat(b["t"]),
                open=Decimal(str(b["o"])),
                high=Decimal(str(b["h"])),
                low=Decimal(str(b["l"])),
                close=Decimal(str(b["c"])),
                volume=Decimal(str(b["v"])),
                source="alpaca",
                feed="sip",
                is_final=True,
            )
        )
        reasons.extend(r for e in events for r in e.reasons)
    after = await store.load_latest("SE")
    assert after is not None
    cp = after.checkpoints[0]
    assert cp.exit_price == Decimal(price) and cp.closed_at == datetime.fromisoformat(at)
    if reconstructed:
        assert reasons.count("recovery_failure_warning") == 2
        assert "recovery_failure_confirmation_reset" in reasons
        assert cp.outcome is EntryLegStatus.RECOVERY_FAILED
    else:
        assert cp.recovery_exit is None


async def test_two_closes_cannot_bridge_sessions() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 30):
        await engine.ingest_bar(minute(i, "97.5"))
    for i in range(15, 30):
        bar = minute(i, "97").model_copy(update={"timestamp": AT + timedelta(days=1, minutes=i)})
        await engine.ingest_bar(bar)
    active = await store.load_active("AAPL")
    assert active is not None and active.checkpoints[0].recovery_exit.pending_exit_at is None


async def test_original_stop_preempts_second_failure_close() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    for i in range(15, 44):
        await engine.ingest_bar(minute(i, "97.5" if i < 30 else "97"))
    await engine.ingest_bar(minute(44, "97").model_copy(update={"low": Decimal("89")}))
    after = await store.load_latest("AAPL")
    assert after is not None and after.checkpoints[0].outcome is EntryLegStatus.INVALIDATED
    assert after.checkpoints[0].exit_price == Decimal("90")


async def test_nonfinal_and_nonminute_bars_do_not_advance_failure_state() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV20(store=store)
    await engine.ingest_signal(signal())
    before = await store.load_active("AAPL")
    for update in ({"is_final": False}, {"timeframe": BarTimeframe.MINUTE_15}):
        assert await engine.ingest_bar(minute(15, "97").model_copy(update=update)) == ()
    assert await store.load_active("AAPL") == before
