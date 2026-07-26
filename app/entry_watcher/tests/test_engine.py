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
from app.entry_watcher import EntryWatcher, EntryWatcherPolicy, InMemoryEntryWatchStore

NOW = datetime(2026, 7, 26, 15, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
WATCH_ID = UUID("0195f3a5-9000-7000-8000-000000000021")


def analysis(
    horizon: AnalysisHorizon,
    *,
    classification: str,
    verdict: AnalysisVerdict,
    direction: PatternDirection,
    price: str = "120",
    as_of: datetime = NOW,
) -> AnalysisResult:
    metrics = [
        NamedValue(name="classification", value=classification),
        NamedValue(name="reference_price", value=Decimal(price)),
    ]
    if horizon is AnalysisHorizon.LONG_TERM:
        metrics.extend(
            (
                NamedValue(name="buy_zone_low", value=Decimal("100")),
                NamedValue(name="buy_zone_high", value=Decimal("105")),
                NamedValue(name="invalidation", value=Decimal("92")),
                NamedValue(name="support", value=Decimal("96")),
            )
        )
    return AnalysisResult(
        analysis_id=UUID(
            {
                AnalysisHorizon.LONG_TERM: "0195f3a5-9000-7000-8000-000000000011",
                AnalysisHorizon.SWING: "0195f3a5-9000-7000-8000-000000000012",
                AnalysisHorizon.INTRADAY: "0195f3a5-9000-7000-8000-000000000013",
                AnalysisHorizon.DILUTION: "0195f3a5-9000-7000-8000-000000000014",
            }[horizon]
        ),
        engine_id=f"fixture-{horizon.value.lower()}",
        engine_version="1.0.0",
        symbol="AAPL",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal("80"),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        metrics=tuple(metrics),
        context_hash=HASH,
    )


def long_watch(*, price: str = "120") -> AnalysisResult:
    return analysis(
        AnalysisHorizon.LONG_TERM,
        classification="extended",
        verdict=AnalysisVerdict.CAUTION,
        direction=PatternDirection.BULLISH,
        price=price,
    )


@pytest.mark.unit
async def test_extended_long_setup_arms_and_freezes_original_zone() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(
        store=store,
        policy=EntryWatcherPolicy(ttl=timedelta(weeks=8)),
        id_factory=lambda: WATCH_ID,
    )

    transition = await watcher.ingest(long_watch(), now=NOW)
    active = await store.load_active("AAPL")

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED
    assert active is not None
    assert active.watch_id == WATCH_ID
    assert active.zone_low == Decimal("100")
    assert active.zone_high == Decimal("105")
    assert active.invalidation == Decimal("92")
    assert active.correction_target_percent == Decimal("12.5000")
    assert active.expires_at == NOW + timedelta(weeks=8)
    assert active.anchor_snapshot["metrics"]["support"] == "96"


@pytest.mark.unit
async def test_zone_is_remembered_until_swing_and_intraday_confirm() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.DILUTION,
            classification="clear",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.NEUTRAL,
        ),
        now=NOW,
    )

    entered = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="103",
        ),
        now=NOW,
    )
    active = await store.load_active("AAPL")

    assert entered is not None and entered.status is EntryWatchStatus.IN_ZONE
    assert active is not None and active.zone_high == Decimal("105")

    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )
    triggered = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert "multi_horizon_entry_confirmed" in triggered.reasons
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalidating", "reason"),
    (
        (
            analysis(
                AnalysisHorizon.LONG_TERM,
                classification="avoid",
                verdict=AnalysisVerdict.AVOID,
                direction=PatternDirection.BEARISH,
                price="101",
            ),
            "long_structure_invalidated",
        ),
        (
            analysis(
                AnalysisHorizon.DILUTION,
                classification="avoid",
                verdict=AnalysisVerdict.AVOID,
                direction=PatternDirection.BEARISH,
                price="101",
            ),
            "dilution_veto",
        ),
        (
            analysis(
                AnalysisHorizon.INTRADAY,
                classification="breakdown",
                verdict=AnalysisVerdict.AVOID,
                direction=PatternDirection.BEARISH,
                price="91",
            ),
            "original_invalidation_breached",
        ),
    ),
)
async def test_only_explicit_invalidation_cancels_the_thesis(
    invalidating: AnalysisResult, reason: str
) -> None:
    watcher = EntryWatcher(store=InMemoryEntryWatchStore())
    await watcher.ingest(long_watch(), now=NOW)

    transition = await watcher.ingest(invalidating, now=NOW)

    assert transition is not None
    assert transition.status is EntryWatchStatus.INVALIDATED
    assert reason in transition.reasons


@pytest.mark.unit
async def test_watch_expires_without_reusing_stale_confirmation() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store, policy=EntryWatcherPolicy(ttl=timedelta(days=1)))
    await watcher.ingest(long_watch(), now=NOW)

    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="neutral",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.NEUTRAL,
            as_of=NOW + timedelta(days=1),
        ),
        now=NOW + timedelta(days=1),
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.EXPIRED
    assert await store.load_active("AAPL") is None
