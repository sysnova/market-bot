from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    NamedValue,
    PatternDirection,
)
from app.entry_watcher import EntryWatcherV56, InMemoryEntryWatchStore

NOW = datetime(2026, 9, 2, 12, 28, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _long_analysis(*, analysis_id: str, as_of: datetime, price: str) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=UUID(analysis_id),
        engine_id="long-term",
        engine_version="2.0.0",
        symbol="CRDO",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=as_of,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("64.93"),
        confidence=Decimal("0.6493"),
        reasons=("fixture",),
        metrics=(
            NamedValue(name="classification", value="watch_pullback"),
            NamedValue(name="reference_price", value=Decimal(price)),
            NamedValue(name="buy_zone_low", value=Decimal("170")),
            NamedValue(name="buy_zone_high", value=Decimal("180")),
            NamedValue(name="invalidation", value=Decimal("160")),
            NamedValue(name="distance_to_buy_zone_atr", value=Decimal("0.5")),
        ),
        context_hash=HASH,
    )


@pytest.mark.unit
async def test_previous_session_price_cannot_create_a_new_armed_transition() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV56(store=store)
    previous_session = _long_analysis(
        analysis_id="0195f3a5-9000-7000-8000-000000000011",
        as_of=NOW - timedelta(hours=16, minutes=43),
        price="206.95",
    )

    transition = await watcher.ingest(previous_session, now=NOW)

    assert transition is None
    assert await store.load_active("CRDO") is None


@pytest.mark.unit
async def test_armed_transition_freezes_the_contemporary_emission_price() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV56(store=store)
    fresh = _long_analysis(
        analysis_id="0195f3a5-9000-7000-8000-000000000012",
        as_of=NOW - timedelta(minutes=1),
        price="185",
    )

    transition = await watcher.ingest(fresh, now=NOW)
    active = await store.load_active("CRDO")

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED
    assert transition.occurred_at == NOW
    assert transition.current_price == Decimal("185")
    assert active is not None
    assert active.armed_at == NOW
    assert active.original_price == Decimal("185")
