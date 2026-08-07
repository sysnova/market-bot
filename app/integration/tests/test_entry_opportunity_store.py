from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.contracts import (
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
)
from app.integration.entry_opportunity_store import (
    PostgresEntryOpportunityStore,
    _event_record,
    _to_domain,
    _to_record,
)

NOW = datetime(2026, 8, 6, 18, tzinfo=UTC)
OPPORTUNITY_ID = UUID("01987e76-3c00-7000-8000-000000000001")
CHECKPOINT_ID = UUID("01987e76-3c00-7000-8000-000000000002")
ANALYSIS_ID = UUID("01987e76-3c00-7000-8000-000000000003")
EVENT_ID = UUID("01987e76-3c00-7000-8000-000000000004")


def opportunity() -> EntryOpportunity:
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=CHECKPOINT_ID,
        level=EntryMaturityLevel.ARMED,
        reached_at=NOW,
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
        invalidation=Decimal("92"),
    )
    return EntryOpportunity(
        opportunity_id=OPPORTUNITY_ID,
        symbol="AAPL",
        status=EntryOpportunityStatus.ARMED,
        current_maturity=EntryMaturityLevel.ARMED,
        peak_maturity=EntryMaturityLevel.ARMED,
        progress_percent=Decimal("20"),
        armed_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("100"),
        current_price=Decimal("100"),
        source_analysis_ids=(ANALYSIS_ID,),
        checkpoints=(checkpoint,),
    )


@pytest.mark.unit
def test_opportunity_record_round_trip_preserves_full_audit_snapshot() -> None:
    original = opportunity()

    restored = _to_domain(_to_record(original))

    assert restored == original


@pytest.mark.unit
def test_event_record_uses_event_id_for_idempotency() -> None:
    original = EntryOpportunityEvent(
        event_id=EVENT_ID,
        occurred_at=NOW,
        opportunity=opportunity(),
        reasons=("opportunity_created",),
    )

    record = _event_record(original)

    assert record.id == EVENT_ID
    assert record.opportunity_id == OPPORTUNITY_ID
    assert record.payload["reasons"] == ["opportunity_created"]


@pytest.mark.unit
async def test_readiness_requires_snapshot_and_event_tables() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["market_bot.entry_opportunities", None]
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = MagicMock(return_value=context)
    store = PostgresEntryOpportunityStore(factory)  # type: ignore[arg-type]

    assert await store.is_ready() is False
    assert session.scalar.await_count == 2
