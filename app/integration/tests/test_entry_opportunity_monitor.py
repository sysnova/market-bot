from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any
from uuid import UUID

import pytest

from app.contracts import (
    ENTRY_OPPORTUNITY_EVENT,
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EventEnvelope,
)
from app.integration import entry_opportunity_monitor
from app.integration.entry_opportunity_monitor import (
    OpportunityDashboard,
    _opportunity_event,
    format_opportunity_dashboard,
)

NOW = datetime(2026, 8, 8, 18, tzinfo=UTC)


def _closed_opportunity(*, revision: int = 4) -> EntryOpportunity:
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7000-8000-000000000001"),
        level=EntryMaturityLevel.L4,
        reached_at=NOW - timedelta(hours=2),
        entry_price=Decimal("100"),
        current_price=Decimal("105"),
        highest_price=Decimal("108"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("92"),
        target=Decimal("110"),
        status=EntryCheckpointStatus.CLOSED,
        closed_at=NOW,
        exit_price=Decimal("105"),
        outcome=EntryLegStatus.TIME_EXIT,
        gain_loss_percent=Decimal("5"),
        mfe_percent=Decimal("8"),
        mae_percent=Decimal("-2"),
        return_15m=Decimal("1.5"),
        return_30m=Decimal("2.5"),
        return_60m=Decimal("4"),
        return_close=Decimal("5"),
    )
    leg = EntryHorizonLeg(
        leg_id=UUID("01987e76-3c00-7001-8000-000000000001"),
        horizon=AnalysisHorizon.SWING,
        status=EntryLegStatus.TIME_EXIT,
        opened_at=NOW - timedelta(hours=2),
        entry_price=Decimal("100"),
        current_price=Decimal("105"),
        invalidation=Decimal("92"),
        target=Decimal("110"),
        highest_price=Decimal("108"),
        lowest_price=Decimal("98"),
        closed_at=NOW,
        exit_price=Decimal("105"),
        gain_loss_percent=Decimal("5"),
        mfe_percent=Decimal("8"),
        mae_percent=Decimal("-2"),
    )
    return EntryOpportunity(
        opportunity_id=UUID("01987e76-3c00-7002-8000-000000000001"),
        symbol="AAPL",
        status=EntryOpportunityStatus.CLOSED,
        current_maturity=EntryMaturityLevel.L4,
        peak_maturity=EntryMaturityLevel.L4,
        progress_percent=Decimal("100"),
        original_watch_id=UUID("01987e76-3c00-7003-8000-000000000001"),
        armed_at=NOW - timedelta(hours=3),
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
        closed_at=NOW,
        close_reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("103"),
        current_price=Decimal("105"),
        revision=revision,
        source_analysis_ids=(UUID("01987e76-3c00-7004-8000-000000000001"),),
        legs=(leg,),
        checkpoints=(checkpoint,),
    )


@pytest.mark.unit
def test_dashboard_renders_maturity_entries_closed_gain_loss_and_tracking_details() -> None:
    opportunity = _closed_opportunity()
    dashboard = OpportunityDashboard(history=25)
    dashboard.merge(opportunity, reasons=("all_horizons_closed",))

    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW)

    for expected in (
        "ENTRY OPPORTUNITIES",
        "AAPL",
        "CLOSED",
        "L4",
        "100%",
        "ORIG 103",
        "ZONE 95-100",
        "INV 92",
        "ALL_HORIZONS_CLOSED",
        "CHECKPOINTS DE MADURACION",
        "ENTRY 100",
        "EXIT 105",
        "G/L +5.0000%",
        "MFE +8.0000%",
        "MAE -2.0000%",
        "15m +1.5000%",
        "LEGS POR HORIZONTE",
        "SWING",
        "ULTIMO EVENTO all_horizons_closed",
        "SOURCE ANALYSES 1",
    ):
        assert expected in output


@pytest.mark.unit
def test_dashboard_rejects_an_older_snapshot_of_the_same_opportunity() -> None:
    dashboard = OpportunityDashboard(history=25)
    newest = _closed_opportunity(revision=4)
    older = newest.model_copy(update={"revision": 3, "current_price": Decimal("101")})

    dashboard.merge(newest)
    dashboard.merge(older)

    assert dashboard.items() == (newest,)


@pytest.mark.unit
def test_monitor_decodes_only_entry_opportunity_events() -> None:
    opportunity = _closed_opportunity()
    event = EntryOpportunityEvent(
        event_id=UUID("01987e76-3c00-7005-8000-000000000001"),
        occurred_at=NOW,
        opportunity=opportunity,
        reasons=("all_horizons_closed",),
    )
    envelope = EventEnvelope(
        event_type=ENTRY_OPPORTUNITY_EVENT,
        occurred_at=NOW,
        source="entry-opportunity-v1",
        subject="AAPL",
        payload=event.model_dump(mode="json"),
    )

    decoded = _opportunity_event(envelope)

    assert decoded == event
    assert _opportunity_event(envelope.model_copy(update={"event_type": "other"})) is None


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _MonitorBus:
    envelope: EventEnvelope
    instance: _MonitorBus | None = None

    def __init__(self) -> None:
        self.subscription = _Subscription()
        self.subject = ""
        self.closed = False
        type(self).instance = self

    @classmethod
    async def connect(cls, **_: Any) -> _MonitorBus:
        return cls()

    async def subscribe(self, subject: str, handler: Any, **_: Any) -> _Subscription:
        self.subject = subject
        await handler(self.envelope)
        return self.subscription

    async def close(self) -> None:
        self.closed = True


class _MonitorStore:
    opportunity: EntryOpportunity

    def __init__(self, *_: Any) -> None:
        pass

    async def is_ready(self) -> bool:
        return True

    async def list_recent(self, *, limit: int) -> tuple[EntryOpportunity, ...]:
        assert limit == 25
        return (self.opportunity,)

    async def list_active(self) -> tuple[EntryOpportunity, ...]:
        return ()


class _Database:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _StopEvent:
    async def wait(self) -> None:
        raise RuntimeError("stop monitor")


@pytest.mark.unit
async def test_monitor_redraws_for_nats_event_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opportunity = _closed_opportunity()
    event = EntryOpportunityEvent(
        event_id=UUID("01987e76-3c00-7005-8000-000000000001"),
        occurred_at=NOW,
        opportunity=opportunity,
        reasons=("all_horizons_closed",),
    )
    _MonitorBus.envelope = EventEnvelope(
        event_type=ENTRY_OPPORTUNITY_EVENT,
        occurred_at=NOW,
        source="entry-opportunity-v1",
        subject="AAPL",
        payload=event,
    )
    _MonitorStore.opportunity = opportunity
    database = _Database()
    monkeypatch.setattr(entry_opportunity_monitor, "NatsJetStreamEventBus", _MonitorBus)
    monkeypatch.setattr(
        entry_opportunity_monitor,
        "PostgresEntryOpportunityStore",
        _MonitorStore,
    )
    monkeypatch.setattr(
        entry_opportunity_monitor,
        "create_database_engine",
        lambda *_args, **_kwargs: database,
    )
    monkeypatch.setattr(
        entry_opportunity_monitor,
        "create_session_factory",
        lambda _database: object(),
    )
    monkeypatch.setattr(entry_opportunity_monitor.asyncio, "Event", _StopEvent)
    output = StringIO()

    with pytest.raises(RuntimeError, match="stop monitor"):
        await entry_opportunity_monitor.run_entry_opportunity_monitor(
            history=25,
            refresh_interval=timedelta(minutes=1),
            stream=output,
        )

    assert output.getvalue().count("ENTRY OPPORTUNITIES") >= 2
    assert "ULTIMO EVENTO all_horizons_closed" in output.getvalue()
    assert _MonitorBus.instance is not None
    assert _MonitorBus.instance.subject == "marketbot.v1.entry-opportunity.transition.>"
    assert _MonitorBus.instance.subscription.unsubscribed is True
    assert _MonitorBus.instance.closed is True
    assert database.disposed is True
