from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.contracts import AnalysisHorizon, EntryWatchStatus, EntryWatchTransition
from app.entry_watcher import EntryWatch
from app.integration.entry_watch_store import (
    PostgresEntryWatchStore,
    _to_domain,
    _to_record,
    _transition_record,
)

NOW = datetime(2026, 7, 26, 15, tzinfo=UTC)
WATCH_ID = UUID("0195f3a5-9000-7000-8000-000000000001")
ANALYSIS_ID = UUID("0195f3a5-9000-7000-8000-000000000002")


def watch() -> EntryWatch:
    return EntryWatch(
        watch_id=WATCH_ID,
        symbol="AAPL",
        status=EntryWatchStatus.ARMED,
        armed_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        original_price=Decimal("120"),
        current_price=Decimal("120"),
        correction_target_percent=Decimal("12.5000"),
        source_analysis_id=ANALYSIS_ID,
        source_context_hash="sha256:" + "a" * 64,
        anchor_snapshot={"classification": "extended"},
    )


@pytest.mark.unit
def test_postgres_record_round_trip_preserves_frozen_thesis() -> None:
    original = watch()

    restored = _to_domain(_to_record(original))

    assert restored == original


@pytest.mark.unit
def test_transition_record_is_json_serializable() -> None:
    transition = EntryWatchTransition(
        watch_id=WATCH_ID,
        symbol="AAPL",
        status=EntryWatchStatus.ARMED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("120"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("long_entry_thesis_armed",),
        horizons=(AnalysisHorizon.LONG_TERM,),
        source_analysis_ids=(ANALYSIS_ID,),
    )

    record = _transition_record(transition)

    assert record.status == "ARMED"
    assert record.horizons == ["LONG_TERM"]
    assert record.source_analysis_ids == [str(ANALYSIS_ID)]


@pytest.mark.unit
async def test_readiness_requires_both_versioned_tables() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["market_bot.entry_watches", None]
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = MagicMock(return_value=context)
    store = PostgresEntryWatchStore(factory)  # type: ignore[arg-type]

    assert await store.is_ready() is False
    assert session.scalar.await_count == 2
