from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
)
from app.integration.entry_opportunity_corporate_actions import (
    APH_2026_SPLIT,
    apply_stock_split_adjustment,
)
from app.opportunity_dashboard import checkpoint_pnl_percent

NOW = datetime(2026, 9, 4, 15, tzinfo=UTC)


@pytest.mark.unit
def test_known_aph_split_rebases_pre_split_entry_levels_not_current_mark() -> None:
    opportunity = _opportunity(symbol="APH")

    adjusted = apply_stock_split_adjustment(opportunity, APH_2026_SPLIT)

    checkpoint = adjusted.checkpoints[0]
    leg = adjusted.legs[0]
    assert adjusted.original_price == Decimal("84.59")
    assert adjusted.zone_low == Decimal("80")
    assert adjusted.zone_high == Decimal("84.59")
    assert adjusted.invalidation == Decimal("75")
    assert adjusted.current_price == Decimal("85.12")
    assert checkpoint.entry_price == Decimal("84.59")
    assert checkpoint.current_price == Decimal("85.12")
    assert checkpoint.highest_price == Decimal("85")
    assert checkpoint.lowest_price == Decimal("84.50")
    assert leg.entry_price == Decimal("84.59")
    assert leg.current_price == Decimal("85.12")
    assert checkpoint_pnl_percent(checkpoint).quantize(Decimal("0.0001")) == Decimal("0.6266")


@pytest.mark.unit
def test_non_aph_fifty_percent_drop_remains_a_real_mark_to_market_loss() -> None:
    opportunity = _opportunity(symbol="TEST")

    adjusted = apply_stock_split_adjustment(opportunity, APH_2026_SPLIT)

    assert adjusted == opportunity
    assert checkpoint_pnl_percent(adjusted.checkpoints[0]).quantize(
        Decimal("0.0001")
    ) == Decimal("-49.6867")


def _opportunity(*, symbol: str) -> EntryOpportunity:
    reached_at = datetime(2026, 8, 8, 14, tzinfo=UTC)
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7000-8000-000000000001"),
        level=EntryMaturityLevel.L1,
        reached_at=reached_at,
        entry_price=Decimal("169.18"),
        current_price=Decimal("85.12"),
        highest_price=Decimal("170"),
        lowest_price=Decimal("84.50"),
        invalidation=Decimal("150"),
        target=Decimal("190"),
        zone_low=Decimal("160"),
        zone_high=Decimal("169.18"),
        status=EntryCheckpointStatus.OPEN,
    )
    leg = EntryHorizonLeg(
        leg_id=UUID("01987e76-3c00-7000-8000-000000000002"),
        horizon=AnalysisHorizon.SWING,
        status=EntryLegStatus.OPEN,
        opened_at=reached_at,
        expires_at=reached_at + timedelta(days=30),
        entry_price=Decimal("169.18"),
        current_price=Decimal("85.12"),
        invalidation=Decimal("150"),
        target=Decimal("190"),
        highest_price=Decimal("170"),
        lowest_price=Decimal("84.50"),
    )
    return EntryOpportunity(
        opportunity_id=UUID("01987e76-3c00-7001-8000-000000000001"),
        symbol=symbol,
        status=EntryOpportunityStatus.OPEN,
        current_maturity=EntryMaturityLevel.L1,
        peak_maturity=EntryMaturityLevel.L1,
        progress_percent=Decimal("60"),
        armed_at=reached_at,
        updated_at=NOW,
        last_market_bar_at=NOW,
        expires_at=reached_at + timedelta(days=60),
        zone_low=Decimal("160"),
        zone_high=Decimal("169.18"),
        invalidation=Decimal("150"),
        original_price=Decimal("169.18"),
        current_price=Decimal("85.12"),
        source_analysis_ids=(UUID("01987e76-3c00-7002-8000-000000000001"),),
        legs=(leg,),
        checkpoints=(checkpoint,),
    )
