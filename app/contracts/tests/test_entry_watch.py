from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.contracts import AnalysisHorizon, EntryWatchStatus, EntryWatchTransition

NOW = datetime(2026, 7, 26, 15, tzinfo=UTC)
WATCH_ID = UUID("0195f3a5-9000-7000-8000-000000000001")
ANALYSIS_ID = UUID("0195f3a5-9000-7000-8000-000000000002")


def transition(**updates: object) -> EntryWatchTransition:
    values: dict[str, object] = {
        "watch_id": WATCH_ID,
        "symbol": "AAPL",
        "previous_status": EntryWatchStatus.ARMED,
        "status": EntryWatchStatus.IN_ZONE,
        "occurred_at": NOW,
        "zone_low": Decimal("90"),
        "zone_high": Decimal("95"),
        "invalidation": Decimal("84"),
        "current_price": Decimal("94"),
        "watch_expires_at": NOW + timedelta(weeks=8),
        "reasons": ("target_zone_reached",),
        "horizons": (AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING),
        "source_analysis_ids": (ANALYSIS_ID,),
    }
    values.update(updates)
    return EntryWatchTransition(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_entry_watch_transition_is_strict_and_ordered() -> None:
    value = transition()

    assert value.transition_id.version == 7
    assert value.invalidation < value.zone_low <= value.zone_high
    assert value.watch_expires_at > value.occurred_at


@pytest.mark.unit
@pytest.mark.parametrize(
    "updates",
    (
        {"zone_low": Decimal("96")},
        {"invalidation": Decimal("90")},
        {"watch_expires_at": NOW - timedelta(seconds=1)},
        {"horizons": (AnalysisHorizon.SWING, AnalysisHorizon.SWING)},
        {"source_analysis_ids": (ANALYSIS_ID, ANALYSIS_ID)},
    ),
)
def test_entry_watch_transition_rejects_invalid_boundaries(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        transition(**updates)
