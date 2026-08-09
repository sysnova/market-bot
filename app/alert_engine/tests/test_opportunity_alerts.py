from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.alert_engine import AlertEngine
from app.contracts import (
    AlertKind,
    AlertSeverity,
    EntryCloseReason,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EntrySignalFamily,
)

NOW = datetime(2026, 8, 6, 18, tzinfo=UTC)


def event(*, closed: bool = False) -> EntryOpportunityEvent:
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7000-8000-000000000002"),
        level=EntryMaturityLevel.L3,
        reached_at=NOW,
        entry_price=Decimal("100"),
        current_price=Decimal("98") if closed else Decimal("102"),
        highest_price=Decimal("104"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("92"),
    )
    opportunity = EntryOpportunity(
        opportunity_id=UUID("01987e76-3c00-7000-8000-000000000001"),
        symbol="AAPL",
        status=EntryOpportunityStatus.CLOSED if closed else EntryOpportunityStatus.CONFIRMING,
        current_maturity=EntryMaturityLevel.L3,
        peak_maturity=EntryMaturityLevel.L3,
        progress_percent=Decimal("90"),
        armed_at=NOW - timedelta(hours=1),
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        closed_at=NOW if closed else None,
        close_reason=EntryCloseReason.ORIGINAL_THESIS_INVALIDATED if closed else None,
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("105"),
        current_price=Decimal("92") if closed else Decimal("102"),
        source_analysis_ids=(UUID("01987e76-3c00-7000-8000-000000000003"),),
        checkpoints=(checkpoint,),
    )
    return EntryOpportunityEvent(
        event_id=UUID("01987e76-3c00-7000-8000-000000000004"),
        occurred_at=NOW,
        opportunity=opportunity,
        reasons=("fixture",),
    )


@pytest.mark.unit
def test_opportunity_progress_alert_contains_maturity_and_progress_bar() -> None:
    alert = AlertEngine().ingest_entry_opportunity(event(), now=NOW)

    assert alert.kind is AlertKind.ENTRY_OPPORTUNITY_PROGRESS
    assert alert.severity is AlertSeverity.ACTION
    assert "L3" in alert.title
    assert "90%" in alert.message
    assert next(item.value for item in alert.metrics if item.name == "progress_percent") == Decimal(
        "90"
    )


@pytest.mark.unit
def test_invalidated_opportunity_emits_critical_close_alarm_alert() -> None:
    alert = AlertEngine().ingest_entry_opportunity(event(closed=True), now=NOW)

    assert alert.kind is AlertKind.ENTRY_OPPORTUNITY_CLOSED
    assert alert.severity is AlertSeverity.CRITICAL
    assert "ORIGINAL THESIS INVALIDATED" in alert.title


@pytest.mark.unit
def test_standalone_analytical_family_is_not_presented_as_core_l_maturity() -> None:
    base = event()
    analytical = base.model_copy(
        update={
            "opportunity": base.opportunity.model_copy(
                update={"primary_signal_family": EntrySignalFamily.PATREON_CAPS}
            )
        }
    )

    alert = AlertEngine().ingest_entry_opportunity(analytical, now=NOW)

    assert "PATREON CAPS" in alert.title
    assert "L3" not in alert.title
    assert next(item.value for item in alert.metrics if item.name == "signal_family") == (
        "PATREON_CAPS"
    )
