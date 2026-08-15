from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    BarTimeframe,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
    MarketBar,
)
from app.integration.entry_opportunity_bar_recovery import (
    entry_opportunity_history_requirements,
    replay_pending_entry_opportunity_bars,
)

NOW = datetime(2026, 8, 14, 15, tzinfo=UTC)


def _opportunity(*, last_market_bar_at: datetime | None) -> EntryOpportunity:
    armed_at = NOW - timedelta(days=2)
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7000-8000-000000000002"),
        level=EntryMaturityLevel.ARMED,
        reached_at=armed_at,
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
        invalidation=Decimal("92"),
    )
    return EntryOpportunity(
        opportunity_id=UUID("01987e76-3c00-7000-8000-000000000001"),
        symbol="AAPL",
        status=EntryOpportunityStatus.ARMED,
        current_maturity=EntryMaturityLevel.ARMED,
        peak_maturity=EntryMaturityLevel.ARMED,
        progress_percent=Decimal("20"),
        armed_at=armed_at,
        updated_at=armed_at,
        last_market_bar_at=last_market_bar_at,
        expires_at=NOW + timedelta(days=54),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("100"),
        current_price=Decimal("100"),
        source_analysis_ids=(UUID("01987e76-3c00-7000-8000-000000000003"),),
        checkpoints=(checkpoint,),
    )


def _bar(timestamp: datetime) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        source="fixture",
        feed="sip",
        is_final=True,
    )


@pytest.mark.unit
def test_recovery_requirement_starts_at_the_oldest_persisted_bar_cursor() -> None:
    cursor = NOW - timedelta(hours=3)

    requirements = entry_opportunity_history_requirements(
        (_opportunity(last_market_bar_at=cursor),),
        as_of=NOW,
    )

    assert len(requirements) == 1
    assert requirements[0].timeframe is BarTimeframe.MINUTE_1
    assert requirements[0].lookback == timedelta(days=1)
    assert requirements[0].max_bars_per_symbol == 10_000


@pytest.mark.unit
async def test_replay_submits_only_bars_after_the_persisted_cursor_in_time_order() -> None:
    cursor = NOW - timedelta(minutes=3)
    submitted: list[MarketBar] = []

    class RecordingEngine:
        async def ingest_bar(self, bar: MarketBar) -> tuple[()]:
            submitted.append(bar)
            return ()

    count = await replay_pending_entry_opportunity_bars(
        RecordingEngine(),
        (_opportunity(last_market_bar_at=cursor),),
        (
            _bar(NOW - timedelta(minutes=1)),
            _bar(NOW - timedelta(minutes=4)),
            _bar(NOW - timedelta(minutes=2)),
            _bar(cursor),
        ),
    )

    assert count == 2
    assert [bar.timestamp for bar in submitted] == [
        NOW - timedelta(minutes=2),
        NOW - timedelta(minutes=1),
    ]
