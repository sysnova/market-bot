from datetime import UTC, datetime
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
    MarketBar,
    PatternDirection,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_engine import analysis
from app.entry_opportunity_engine.v18 import EntryOpportunityEngineV18

AT = datetime(2026, 9, 9, 13, 30, tzinfo=UTC)


def jpm() -> EntryOpportunity:
    return EntryOpportunity.model_validate_json(
        (Path(__file__).parent / "fixtures/jpm_before_exit_20260909.json").read_text(),
        strict=False,
    )


def opening(open_price: str, high: str, low: str, close: str) -> MarketBar:
    return MarketBar(
        symbol="JPM",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=AT,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="fixture",
        feed="sip",
        is_final=True,
    )


@pytest.mark.parametrize(
    "prices,exit_price,outcome",
    [
        (("352", "352.3527", "349.5", "350"), "352", EntryLegStatus.INVALIDATED),
        (("370", "372", "360", "361"), "370", EntryLegStatus.TARGET_HIT),
        (("357", "367", "351", "355"), "353.0933", EntryLegStatus.INVALIDATED),
    ],
)
async def test_gap_and_ambiguous_bar_use_same_causal_exit_for_checkpoint_and_open_legs(
    prices: tuple[str, str, str, str],
    exit_price: str,
    outcome: EntryLegStatus,
) -> None:
    store = InMemoryEntryOpportunityStore()
    before = jpm()
    await store.save(before, None)
    engine = EntryOpportunityEngineV18(store=store)
    candle = opening(*prices)
    await engine.ingest_bar(candle)
    after = await store.load_latest("JPM")
    assert after is not None
    cp = next(cp for cp in after.checkpoints if cp.level is EntryMaturityLevel.L1)
    assert cp.exit_price == Decimal(exit_price)
    assert cp.outcome is outcome
    assert cp.lowest_price == min(
        before.checkpoints[1].lowest_price, Decimal(prices[0]), Decimal(exit_price)
    )
    for leg in after.legs:
        previous = next(item for item in before.legs if item.leg_id == leg.leg_id)
        if previous.status is EntryLegStatus.OPEN:
            assert (leg.exit_price, leg.closed_at) == (cp.exit_price, cp.closed_at)
        else:
            assert leg == previous
    assert await engine.ingest_bar(candle) == ()


async def test_analytical_exit_synchronizes_only_open_legs_of_that_entry() -> None:
    store = InMemoryEntryOpportunityStore()
    before = jpm()
    await store.save(before, None)
    engine = EntryOpportunityEngineV18(store=store)
    result = analysis(
        AnalysisHorizon.SWING,
        price="356",
        verdict=AnalysisVerdict.CAUTION,
        direction=PatternDirection.BEARISH,
        as_of=AT,
    ).model_copy(update={"symbol": "JPM", "engine_id": "swing"})
    await engine.ingest_analysis(result, now=AT)
    after = await store.load_latest("JPM")
    assert after is not None
    cp = next(cp for cp in after.checkpoints if cp.level is EntryMaturityLevel.L1)
    assert cp.exit_price == Decimal("356")
    for leg in after.legs:
        previous = next(item for item in before.legs if item.leg_id == leg.leg_id)
        if previous.status is EntryLegStatus.OPEN:
            assert leg.exit_price == cp.exit_price and leg.closed_at == cp.closed_at
        else:
            assert leg == previous
    assert any(
        cp.status is EntryCheckpointStatus.OPEN and cp.swing_trade_maturity is not None
        for cp in after.checkpoints
    )
