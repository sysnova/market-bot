from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
    EntrySignalFamily,
    GeriCountertrendMaturity,
)
from app.opportunity_dashboard import build_dashboard_snapshot, checkpoint_pnl_percent

NOW = datetime(2026, 8, 31, 15, tzinfo=UTC)


def _checkpoint(
    suffix: int,
    *,
    level: EntryMaturityLevel = EntryMaturityLevel.ARMED,
    family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY,
    countertrend: GeriCountertrendMaturity | None = None,
    entry: str = "100",
    current: str = "95",
    closed: bool = False,
) -> EntryMaturityCheckpoint:
    pnl = (Decimal(current) / Decimal(entry) - Decimal("1")) * Decimal("100")
    return EntryMaturityCheckpoint(
        checkpoint_id=UUID(f"0199a100-0000-7000-8000-{suffix:012d}"),
        level=level,
        countertrend_maturity=countertrend,
        signal_family=family,
        setup_id=f"setup-{suffix}" if family is not EntrySignalFamily.CORE_ENTRY else None,
        reached_at=NOW - timedelta(minutes=suffix),
        entry_price=Decimal(entry),
        current_price=Decimal(current),
        highest_price=Decimal("103"),
        lowest_price=Decimal("94"),
        invalidation=Decimal("90"),
        status=EntryCheckpointStatus.CLOSED if closed else EntryCheckpointStatus.OPEN,
        closed_at=NOW if closed else None,
        exit_price=Decimal(current) if closed else None,
        outcome=EntryLegStatus.INVALIDATED if closed else None,
        gain_loss_percent=pnl if closed else None,
        mfe_percent=Decimal("3"),
        mae_percent=Decimal("-6"),
    )


def opportunity() -> EntryOpportunity:
    checkpoints = (
        _checkpoint(1),
        _checkpoint(2, level=EntryMaturityLevel.L1, closed=True),
        _checkpoint(
            3,
            family=EntrySignalFamily.GERI_COUNTERTREND,
            countertrend=GeriCountertrendMaturity.CT0,
        ),
        _checkpoint(
            4,
            family=EntrySignalFamily.GERI_COUNTERTREND,
            countertrend=GeriCountertrendMaturity.CT1,
        ),
    )
    return EntryOpportunity(
        opportunity_id=UUID("0199a100-0000-7001-8000-000000000001"),
        symbol="AAPL",
        status=EntryOpportunityStatus.OPEN,
        current_maturity=EntryMaturityLevel.L1,
        peak_maturity=EntryMaturityLevel.L1,
        progress_percent=Decimal("60"),
        armed_at=NOW - timedelta(hours=2),
        updated_at=NOW,
        expires_at=NOW + timedelta(days=5),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        original_price=Decimal("100"),
        current_price=Decimal("95"),
        source_analysis_ids=(UUID("0199a100-0000-7002-8000-000000000001"),),
        checkpoints=checkpoints,
    )


@pytest.mark.unit
def test_snapshot_separates_references_from_buys_and_projects_filter_dimensions() -> None:
    snapshot = build_dashboard_snapshot((opportunity(),), refreshed_at=NOW)

    rows = snapshot["rows"]
    assert {row["state"] for row in rows} == {"ARMED", "L1", "CT0", "CT1"}
    assert {row["state"] for row in rows if row["entry_kind"] == "REFERENCE"} == {
        "ARMED",
        "CT0",
    }
    assert {row["state"] for row in rows if row["entry_kind"] == "BUY"} == {"L1", "CT1"}
    assert snapshot["filters"]["statuses"] == ["OPEN"]


@pytest.mark.unit
def test_checkpoint_pnl_uses_audited_close_or_live_mark() -> None:
    live = _checkpoint(10, entry="100", current="97")
    closed = _checkpoint(11, entry="100", current="95", closed=True)

    assert checkpoint_pnl_percent(live) == Decimal("-3.00")
    assert checkpoint_pnl_percent(closed) == Decimal("-5.00")
