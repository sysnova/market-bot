# pyright: reportPrivateUsage=false
from datetime import timedelta
from decimal import Decimal
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
    SwingTradeMaturity,
    TradeSide,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_swing_trade_v4 import swing_signal
from app.entry_opportunity_engine.tests.test_v18 import AT as OPEN
from app.entry_opportunity_engine.tests.test_v18 import opening as base_opening
from app.entry_opportunity_engine.tests.test_v19 import checkpoint
from app.entry_opportunity_engine.tests.test_v21 import ct
from app.entry_opportunity_engine.v21 import EntryOpportunityEngineV21
from app.entry_opportunity_engine.v22 import EntryOpportunityEngineV22

AT = OPEN + timedelta(hours=1)


def opening(open_price: str, high: str, low: str, close: str) -> MarketBar:
    return base_opening(open_price, high, low, close).model_copy(update={"timestamp": AT})


def st(stage: SwingTradeMaturity = SwingTradeMaturity.ST3) -> EntryMaturityCheckpoint:
    return checkpoint().model_copy(
        update={
            "signal_family": EntrySignalFamily.SWING_TRADE,
            "level": EntryMaturityLevel.ARMED,
            "swing_trade_maturity": stage,
        }
    )


@pytest.mark.parametrize(
    "stage",
    [SwingTradeMaturity.ST3, SwingTradeMaturity.ST4],
)
def test_confirmed_st_arms_from_close_for_next_bar_only(stage: SwingTradeMaturity) -> None:
    candle = opening("100", "125", "95", "110")
    assert EntryOpportunityEngineV21._mark_checkpoint(st(stage), candle).protection_stop is None
    cp = EntryOpportunityEngineV22._mark_checkpoint(st(stage), candle)
    assert cp.status is EntryCheckpointStatus.OPEN
    assert cp.protection_stop == Decimal("100")
    assert (cp.invalidation, cp.target) == (Decimal("90"), Decimal("150"))
    assert EntryOpportunityEngineV22._mark_checkpoint(cp, candle) == cp
    closed = EntryOpportunityEngineV22._mark_checkpoint(
        cp,
        opening("105", "107", "99", "102").model_copy(
            update={"timestamp": AT + timedelta(minutes=1)}
        ),
    )
    assert closed.exit_price == Decimal("100")
    assert closed.outcome is EntryLegStatus.PROTECTION_EXIT
    assert EntryOpportunityEngineV22._mark_checkpoint(closed, candle) == closed


def test_wicks_and_historical_mfe_do_not_arm_and_trailing_never_lowers() -> None:
    cp = st().model_copy(update={"highest_price": Decimal("140")})
    cp = EntryOpportunityEngineV22._mark_checkpoint(cp, opening("105", "125", "101", "109"))
    assert cp.protection_stop is None
    cp = EntryOpportunityEngineV22._mark_checkpoint(cp, opening("110", "126", "101", "125"))
    assert cp.protection_stop == Decimal("115")
    cp = EntryOpportunityEngineV22._mark_checkpoint(
        cp,
        opening("120", "124", "116", "118").model_copy(
            update={"timestamp": AT + timedelta(minutes=1)}
        ),
    )
    assert cp.protection_stop == Decimal("115")
    closed = EntryOpportunityEngineV22._mark_checkpoint(
        cp,
        opening("97", "140", "95", "120").model_copy(
            update={"timestamp": AT + timedelta(minutes=2)}
        ),
    )
    assert closed.exit_price == Decimal("97")
    assert closed.outcome is EntryLegStatus.PROTECTION_EXIT


@pytest.mark.parametrize(
    "prices,expected,outcome",
    [
        (("100", "155", "89", "120"), "90", EntryLegStatus.INVALIDATED),
        (("100", "155", "95", "120"), "150", EntryLegStatus.TARGET_HIT),
    ],
)
def test_original_levels_execute_before_arming(
    prices: tuple[str, str, str, str], expected: str, outcome: EntryLegStatus
) -> None:
    closed = EntryOpportunityEngineV22._mark_checkpoint(st(), opening(*prices))
    assert closed.exit_price == Decimal(expected) and closed.outcome is outcome
    assert closed.protection_stop is None


@pytest.mark.parametrize("stage", [SwingTradeMaturity.ST1, SwingTradeMaturity.ST2])
def test_preparation_and_st2_are_not_promoted(stage: SwingTradeMaturity) -> None:
    assert (
        EntryOpportunityEngineV22._mark_checkpoint(
            st(stage), opening("105", "125", "101", "120")
        ).protection_stop
        is None
    )


def test_other_families_keep_previous_policy() -> None:
    candle = opening("105", "125", "101", "120")
    for cp in (
        checkpoint(),
        ct(),
        st().model_copy(update={"signal_family": EntrySignalFamily.PATREON_CAPS}),
        st().model_copy(update={"trade_side": TradeSide.SHORT}),
    ):
        assert EntryOpportunityEngineV22._mark_checkpoint(
            cp, candle
        ) == EntryOpportunityEngineV21._mark_checkpoint(cp, candle)


@pytest.mark.parametrize(
    "prices,expected,outcome",
    [
        (("110", "155", "99", "120"), "100", EntryLegStatus.PROTECTION_EXIT),
        (("155", "160", "99", "120"), "155", EntryLegStatus.TARGET_HIT),
    ],
)
def test_existing_protection_respects_target_gaps_and_ambiguous_bar_order(
    prices: tuple[str, str, str, str], expected: str, outcome: EntryLegStatus
) -> None:
    cp = st().model_copy(
        update={
            "protection_stop": Decimal("100"),
            "protection_updated_at": AT - timedelta(minutes=1),
            "protection_rule_version": "1.0.0",
        }
    )
    closed = EntryOpportunityEngineV22._mark_checkpoint(cp, opening(*prices))
    assert closed.exit_price == Decimal(expected) and closed.outcome is outcome


async def test_events_restart_matching_leg_and_independent_st4() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV22(store=store)
    signal = swing_signal(
        SwingTradeMaturity.ST3, at=AT - timedelta(minutes=1), price="100"
    ).model_copy(
        update={
            "symbol": "JPM",
            "zone_low": Decimal("95"),
            "zone_high": Decimal("105"),
            "invalidation": Decimal("90"),
            "targets": (Decimal("150"),),
        }
    )
    await engine.ingest_signal(signal)
    before = await store.load_active("JPM")
    assert before is not None
    for update in (
        {"is_final": False},
        {"timeframe": BarTimeframe.MINUTE_15},
        {"timestamp": AT.replace(hour=12)},
    ):
        assert (
            await engine.ingest_bar(opening("105", "126", "95", "125").model_copy(update=update))
            == ()
        )
    assert await store.load_active("JPM") == before
    events = await engine.ingest_bar(opening("105", "126", "95", "125"))
    assert any("profit_protection_updated" in e.reasons for e in events)
    marked = await store.load_active("JPM")
    assert marked is not None
    assert marked.checkpoints[0].protection_stop == Decimal("115")
    assert marked.legs[0].protection_stop == Decimal("115")
    await store.save(EntryOpportunity.model_validate_json(marked.model_dump_json()), None)
    engine = EntryOpportunityEngineV22(store=store)
    await engine.ingest_signal(
        signal.model_copy(
            update={
                "swing_trade_maturity": SwingTradeMaturity.ST4,
                "signal_id": UUID("0199a100-0000-7000-8000-000000000777"),
                "created_at": AT + timedelta(seconds=30),
                "entry_price": Decimal("120"),
                "targets": (Decimal("200"),),
            }
        )
    )
    assert await engine.ingest_bar(opening("105", "126", "95", "125")) == ()
    events = await engine.ingest_bar(
        opening("118", "121", "114", "116").model_copy(
            update={"timestamp": AT + timedelta(minutes=1)}
        )
    )
    assert any("swing_trade_st3_protection_exit" in e.reasons for e in events)
    after = await store.load_active("JPM")
    assert after is not None
    cp = after.checkpoints[0]
    assert cp.outcome is EntryLegStatus.PROTECTION_EXIT and cp.exit_price == Decimal("115")
    assert after.legs[0].exit_price == cp.exit_price
    assert after.legs[0].status is EntryLegStatus.PROTECTION_EXIT
    assert after.checkpoints[1].status is EntryCheckpointStatus.OPEN
    assert after.checkpoints[1].protection_stop is None
