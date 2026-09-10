from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    BarTimeframe,
    EntryLegStatus,
    EntryOpportunity,
    LocalAlert,
    MarketBar,
    NamedValue,
    TradeSide,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.v14 import EntryOpportunityEngineV14

NOW = datetime(2026, 9, 10, 14, 1, tzinfo=UTC)


def alert() -> LocalAlert:
    return LocalAlert(
        symbol="ASTS",
        kind=AlertKind.BEARISH_CONSENSUS,
        severity=AlertSeverity.ACTION,
        title="ASTS SHORT CONFIRMED",
        message="Simulated short",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
        score=Decimal("80"),
        deduplication_key="short:asts:1",
        reasons=("short_entry_confirmed",),
        metrics=tuple(
            NamedValue(name=k, value=v)
            for k, v in {
                "short_entry_price": Decimal("100"),
                "short_invalidation": Decimal("102"),
                "short_target": Decimal("97"),
                "short_setup_id": "short:asts:setup",
                "short_confirmation_rule_version": "1.3.0",
            }.items()
        ),
    )


def bar(*, low: str, high: str, close: str, open_: str = "100") -> MarketBar:
    return MarketBar(
        symbol="ASTS",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        source="test",
        feed="sip",
    )


@pytest.mark.asyncio
async def test_short_opens_roundtrips_and_marks_profit_without_broker() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    events = await engine.ingest_alert(alert())
    opened = events[0].opportunity
    assert opened.trade_side is TradeSide.SHORT
    assert opened.legs[0].status is EntryLegStatus.OPEN
    assert EntryOpportunity.model_validate_json(opened.model_dump_json()) == opened
    assert await engine.ingest_alert(alert()) == ()
    await engine.ingest_bar(bar(low="98", high="100", close="99"))
    current = await store.load_active("ASTS")
    assert current is not None
    assert current.legs[0].gain_loss_percent == Decimal("1")
    assert current.legs[0].mfe_percent == Decimal("2")
    assert len(store.opportunities) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("low", "high", "open_", "outcome", "pnl"),
    [
        ("96", "100", "100", EntryLegStatus.TARGET_HIT, "3"),
        ("99", "103", "100", EntryLegStatus.INVALIDATED, "-2"),
        ("96", "103", "100", EntryLegStatus.INVALIDATED, "-2"),
        ("103", "105", "104", EntryLegStatus.INVALIDATED, "-4"),
    ],
)
async def test_short_exit_and_conservative_ambiguous_bar(
    low: str, high: str, open_: str, outcome: EntryLegStatus, pnl: str
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    await engine.ingest_alert(alert())
    await engine.ingest_bar(bar(low=low, high=high, close=open_, open_=open_))
    closed = await store.load_latest("ASTS")
    assert closed is not None
    assert closed.legs[0].status is outcome
    assert closed.legs[0].gain_loss_percent == Decimal(pnl)
    assert await store.load_active("ASTS") is None
    assert EntryOpportunity.model_validate_json(closed.model_dump_json()) == closed


@pytest.mark.asyncio
async def test_expired_alert_does_not_reopen_historical_entry() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW + timedelta(hours=1))
    assert await engine.ingest_alert(alert()) == ()
    assert not store.opportunities


@pytest.mark.asyncio
async def test_short_survives_restore_and_duplicate_bar_then_closes_at_session_end() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    await engine.ingest_alert(alert())
    first = bar(low="99", high="100", close="99")
    await engine.ingest_bar(first)
    restored = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    assert await restored.ingest_bar(first) == ()
    last = first.model_copy(update={"timestamp": NOW.replace(hour=19, minute=59)})
    await restored.ingest_bar(last)
    closed = await store.load_latest("ASTS")
    assert closed is not None
    assert closed.legs[0].status is EntryLegStatus.SESSION_CLOSED
    assert closed.legs[0].gain_loss_percent == Decimal("1")


@pytest.mark.asyncio
async def test_reconciliation_closes_without_close_bar_using_last_mark() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    await engine.ingest_alert(alert())
    await engine.ingest_bar(bar(low="99", high="100", close="99"))
    result = await engine.reconcile(now=NOW.replace(hour=20), active_symbols=("ASTS",))
    assert result[0].opportunity.legs[0].gain_loss_percent == Decimal("1")
    assert result[0].reasons == ("short_session_closed_at_last_observed_price",)


@pytest.mark.asyncio
async def test_regular_bearish_alert_and_invalid_geometry_do_not_open() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV14(store=store, now=lambda: NOW)
    assert (
        await engine.ingest_alert(alert().model_copy(update={"reasons": ("bearish_consensus",)}))
        == ()
    )
    bad = alert().model_copy(
        update={
            "metrics": tuple(
                x.model_copy(update={"value": Decimal("99")})
                if x.name == "short_invalidation"
                else x
                for x in alert().metrics
            )
        }
    )
    assert await engine.ingest_alert(bad) == ()
    assert not store.opportunities
