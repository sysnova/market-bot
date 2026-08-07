from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
)
from app.integration.entry_opportunity_report import build_entry_opportunity_report

NOW = datetime(2026, 8, 6, 18, tzinfo=UTC)


def opportunity(*, closed: bool, suffix: int) -> EntryOpportunity:
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID(f"01987e76-3c00-7000-8000-{suffix:012d}"),
        level=EntryMaturityLevel.L1 if closed else EntryMaturityLevel.L3,
        reached_at=NOW - timedelta(hours=1),
        entry_price=Decimal("100"),
        current_price=Decimal("105") if closed else Decimal("102"),
        highest_price=Decimal("108"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("92"),
        status=EntryCheckpointStatus.CLOSED if closed else EntryCheckpointStatus.OPEN,
        closed_at=NOW if closed else None,
        exit_price=Decimal("105") if closed else None,
        outcome=EntryLegStatus.TIME_EXIT if closed else None,
        gain_loss_percent=Decimal("5") if closed else None,
        mfe_percent=Decimal("8"),
        mae_percent=Decimal("-2"),
    )
    return EntryOpportunity(
        opportunity_id=UUID(f"01987e76-3c00-7001-8000-{suffix:012d}"),
        symbol="MSFT" if closed else "AAPL",
        status=EntryOpportunityStatus.CLOSED if closed else EntryOpportunityStatus.CONFIRMING,
        current_maturity=checkpoint.level,
        peak_maturity=checkpoint.level,
        progress_percent=Decimal("60") if closed else Decimal("90"),
        armed_at=NOW - timedelta(hours=2),
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        closed_at=NOW if closed else None,
        close_reason=EntryCloseReason.ALL_HORIZONS_CLOSED if closed else None,
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("105"),
        current_price=checkpoint.current_price,
        source_analysis_ids=(UUID("01987e76-3c00-7002-8000-000000000001"),),
        checkpoints=(checkpoint,),
    )


@pytest.mark.unit
def test_report_lists_open_progress_and_l1_success_rate() -> None:
    report = build_entry_opportunity_report(
        (opportunity(closed=False, suffix=1), opportunity(closed=True, suffix=2))
    )

    assert report["summary"] == {"opportunities": 2, "open": 1, "closed": 1}
    assert report["open_trades"][0]["progress_bar"] == "[#########-]"
    assert report["maturity_outcomes"]["L1"]["wins"] == 1
    assert report["maturity_outcomes"]["L1"]["success_rate_percent"] == "100.00"
