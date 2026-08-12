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
    EntrySignalFamily,
    EventEnvelope,
    new_uuid7,
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
def test_dashboard_highlights_trade_summary_with_foreground_colors_only() -> None:
    dashboard = OpportunityDashboard(history=25)
    dashboard.merge(_closed_opportunity())

    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW, color=True)

    assert "COMPRA \033[1;96mAAPL\033[0m" in output
    assert "ENTRADA \033[1;93m100\033[0m" in output
    assert "SALIDA \033[1;95m105\033[0m" in output
    assert "P/L \033[1;92m+5.0000%\033[0m" in output
    assert all(code not in output for code in ("\033[40m", "\033[41m", "\033[42m"))


@pytest.mark.unit
def test_dashboard_uses_red_for_a_negative_live_trade_summary() -> None:
    base = _closed_opportunity()
    checkpoint = base.checkpoints[0].model_copy(
        update={
            "status": EntryCheckpointStatus.OPEN,
            "current_price": Decimal("95"),
            "closed_at": None,
            "exit_price": None,
            "outcome": None,
            "gain_loss_percent": None,
        }
    )
    opportunity = base.model_copy(
        update={
            "status": EntryOpportunityStatus.OPEN,
            "current_price": Decimal("95"),
            "closed_at": None,
            "close_reason": None,
            "checkpoints": (checkpoint,),
        }
    )
    dashboard = OpportunityDashboard(history=25)
    dashboard.merge(opportunity)

    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW, color=True)

    assert "MARCA \033[1;95m95\033[0m" in output
    assert "P/L \033[1;91m-5.0000%\033[0m" in output


@pytest.mark.unit
def test_dashboard_rejects_an_older_snapshot_of_the_same_opportunity() -> None:
    dashboard = OpportunityDashboard(history=25)
    newest = _closed_opportunity(revision=4)
    older = newest.model_copy(update={"revision": 3, "current_price": Decimal("101")})

    dashboard.merge(newest)
    dashboard.merge(older)

    assert dashboard.items() == (newest,)


@pytest.mark.unit
async def test_load_tracked_opportunities_keeps_open_and_today_closed_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today_closed = _closed_opportunity().model_copy(
        update={"symbol": "AAPL", "opportunity_id": new_uuid7()}
    )
    yesterday_closed = _closed_opportunity(
        revision=5,
    ).model_copy(
        update={
            "symbol": "MSFT",
            "opportunity_id": new_uuid7(),
            "closed_at": NOW - timedelta(days=1),
            "updated_at": NOW - timedelta(days=1),
            "checkpoints": (
                _closed_opportunity().checkpoints[0].model_copy(
                    update={"closed_at": NOW - timedelta(days=1)}
                ),
            ),
            "legs": (
                _closed_opportunity().legs[0].model_copy(
                    update={"closed_at": NOW - timedelta(days=1)}
                ),
            ),
        }
    )
    open_opportunity = _closed_opportunity(revision=6).model_copy(
        update={
            "symbol": "NVDA",
            "opportunity_id": new_uuid7(),
            "status": EntryOpportunityStatus.OPEN,
            "closed_at": None,
            "close_reason": None,
        }
    )

    class _Store:
        async def list_recent(self, *, limit: int) -> tuple[EntryOpportunity, ...]:
            assert limit == 25
            return (open_opportunity, today_closed, yesterday_closed)

        async def list_active(self) -> tuple[EntryOpportunity, ...]:
            return (open_opportunity,)

    tracked = await entry_opportunity_monitor._load_tracked_opportunities(
        store=_Store(),
        history=25,
        refreshed_at=NOW,
    )

    assert tuple(item.symbol for item in tracked) == ("NVDA", "AAPL")


@pytest.mark.unit
def test_dashboard_moves_the_latest_nats_update_to_the_bottom() -> None:
    dashboard = OpportunityDashboard(history=25)
    xom = _closed_opportunity().model_copy(update={"symbol": "XOM"})
    aapl = _closed_opportunity().model_copy(
        update={
            "opportunity_id": UUID("01987e76-3c00-7006-8000-000000000001"),
            "symbol": "AAPL",
            "updated_at": NOW - timedelta(minutes=1),
        }
    )
    dashboard.merge(xom)
    dashboard.merge(aapl)
    assert tuple(item.symbol for item in dashboard.items()) == ("XOM", "AAPL")

    dashboard.merge(
        xom.model_copy(update={"revision": 5}),
        reasons=("maturity_l2_reached",),
        focus=True,
    )

    assert tuple(item.symbol for item in dashboard.items()) == ("AAPL", "XOM")
    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW)
    assert output.rfind("ACTUALIZACION RECIENTE NATS") < output.rfind("XOM")
    assert output.rfind("AAPL") < output.rfind("ACTUALIZACION RECIENTE NATS")


@pytest.mark.unit
def test_dashboard_labels_analytical_family_without_fake_core_maturity() -> None:
    base = _closed_opportunity()
    analytical = base.model_copy(
        update={"primary_signal_family": EntrySignalFamily.PATREON_CAPS}
    )
    dashboard = OpportunityDashboard(history=25)
    dashboard.merge(analytical)

    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW)

    assert "FAMILY PATREON_CAPS" in output
    assert "MAT L4" not in output


@pytest.mark.unit
def test_dashboard_labels_tracking_checkpoints_as_references_not_entries() -> None:
    base = _closed_opportunity()
    checkpoint = base.checkpoints[0].model_copy(
        update={
            "level": EntryMaturityLevel.ARMED,
            "status": EntryCheckpointStatus.OPEN,
            "closed_at": None,
            "exit_price": None,
            "outcome": None,
            "gain_loss_percent": None,
        }
    )
    leg = base.legs[0].model_copy(
        update={
            "status": EntryLegStatus.WATCHING,
            "opened_at": None,
            "entry_price": None,
            "closed_at": None,
            "exit_price": None,
            "gain_loss_percent": None,
        }
    )
    tracking = base.model_copy(
        update={
            "status": EntryOpportunityStatus.ARMED,
            "current_maturity": EntryMaturityLevel.ARMED,
            "peak_maturity": EntryMaturityLevel.ARMED,
            "progress_percent": Decimal("20"),
            "closed_at": None,
            "close_reason": None,
            "legs": (leg,),
            "checkpoints": (checkpoint,),
        }
    )
    dashboard = OpportunityDashboard(history=25)
    dashboard.merge(tracking)

    output = format_opportunity_dashboard(dashboard, refreshed_at=NOW)

    assert "REFERENCE 100 PX 105" in output
    assert "MOVE LIVE +5.0000%" in output
    assert "ENTRY 100 PX 105" not in output
    assert "P/L LIVE +5.0000%" not in output
    assert "REFERENCE - PX 105" in output
    assert "MOVE -" in output


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

    async def latest_events(
        self, _opportunity_ids: tuple[UUID, ...]
    ) -> tuple[EntryOpportunityEvent, ...]:
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
    assert "ACTUALIZACION RECIENTE NATS" in output.getvalue()
    assert _MonitorBus.instance is not None
    assert _MonitorBus.instance.subject == "marketbot.v1.entry-opportunity.transition.>"
    assert _MonitorBus.instance.subscription.unsubscribed is True
    assert _MonitorBus.instance.closed is True
    assert database.disposed is True
