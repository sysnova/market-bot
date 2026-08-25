from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from typing import Any

import pytest

from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    LOCAL_ALERT_EVENT,
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    EntrySignal,
    EntrySignalFamily,
    EventEnvelope,
    GeriCountertrendMaturity,
    LocalAlert,
    new_uuid7,
)
from app.integration import confirmed_buy_monitor
from app.integration.confirmed_buy_monitor import (
    _analytical_stage_changed,
    run_confirmed_buy_monitor,
)

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _MonitorBus:
    instance: _MonitorBus | None = None

    def __init__(self) -> None:
        self.closed = False
        self.subjects: list[str] = []
        self.subscriptions: list[_Subscription] = []
        type(self).instance = self

    @classmethod
    async def connect(cls, **_: Any) -> _MonitorBus:
        return cls()

    async def subscribe(self, subject: str, handler: Any, **_: Any) -> _Subscription:
        self.subjects.append(subject)
        subscription = _Subscription()
        self.subscriptions.append(subscription)
        for envelope in _events_for(subject):
            await handler(envelope)
        return subscription

    async def close(self) -> None:
        self.closed = True


class _StopEvent:
    async def wait(self) -> None:
        raise RuntimeError("stop monitor")


def _events_for(subject: str) -> tuple[EventEnvelope, ...]:
    if subject == "marketbot.v1.entry-signal.>":
        return (_signal_event(),)
    if subject == "marketbot.v1.alert.local.>":
        return (
            _alert_event(AlertKind.PORTFOLIO_FLOW_BUY, "BUY FLOW TGT"),
            _alert_event(AlertKind.PORTFOLIO_PROTECT, "PROTECT TGT"),
            _alert_event(AlertKind.LEVERAGED_THESIS_EARLY, "EARLY ASTN"),
            _alert_event(AlertKind.LEVERAGED_THESIS_BUY, "BUY ASTN"),
            _alert_event(AlertKind.LEVERAGED_THESIS_CANCELLED, "CANCEL ASTN"),
            _alert_event(AlertKind.ENTRY_OPPORTUNITY_PROGRESS, "ENTRY PROGRESS TGT"),
        )
    return ()


def _signal_event() -> EventEnvelope:
    signal = EntrySignal(
        family=EntrySignalFamily.SIGNAL_FUSION,
        symbol="TGT",
        created_at=NOW,
        setup_id="fusion:tgt",
        entry_price=Decimal("105"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("103"),
        zone_high=Decimal("105"),
        invalidation=Decimal("99"),
        targets=(Decimal("115"),),
        policy_id="signal-fusion",
        policy_version="1.0.0",
        reasons=("confirmed",),
    )
    return EventEnvelope(
        event_type=ENTRY_SIGNAL_EVENT,
        occurred_at=NOW,
        source="signal-fusion-v0",
        subject="TGT",
        payload=signal,
    )


def _alert_event(kind: AlertKind, title: str) -> EventEnvelope:
    alert = LocalAlert(
        symbol="TGT",
        created_at=NOW,
        severity=AlertSeverity.ACTION,
        title=title,
        message="operator notification",
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("80"),
        reasons=("fixture",),
        deduplication_key=f"test:{kind.value}",
        kind=kind,
    )
    return EventEnvelope(
        event_type=LOCAL_ALERT_EVENT,
        occurred_at=NOW,
        source="alert-v3",
        subject="TGT",
        payload=alert,
    )


async def test_monitor_projects_final_signals_and_only_manual_flow_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(confirmed_buy_monitor, "NatsJetStreamEventBus", _MonitorBus)
    monkeypatch.setattr(confirmed_buy_monitor.asyncio, "Event", _StopEvent)
    output = StringIO()

    with pytest.raises(RuntimeError, match="stop monitor"):
        await run_confirmed_buy_monitor(stream=output, bell=False)

    rendered = output.getvalue()
    assert "SIGNAL FUSION CONFIRMED" in rendered
    assert "L4" not in rendered
    assert "BUY FLOW TGT" in rendered
    assert "PROTECT TGT" in rendered
    assert "EARLY ASTN" in rendered
    assert "BUY ASTN" in rendered
    assert "CANCEL ASTN" in rendered
    assert "ENTRY PROGRESS TGT" not in rendered

    bus = _MonitorBus.instance
    assert bus is not None
    assert bus.subjects == [
        "marketbot.v1.entry-signal.>",
        "marketbot.v1.alert.local.>",
    ]
    assert all(item.unsubscribed for item in bus.subscriptions)
    assert bus.closed is True


def test_monitor_realerts_geri_after_maturity_resets() -> None:
    first = EntrySignal(
        family=EntrySignalFamily.GERI_COUNTERTREND,
        countertrend_maturity=GeriCountertrendMaturity.CT2,
        symbol="ADUR",
        created_at=NOW,
        setup_id="geri-countertrend:ADUR:pivot:1.4.0",
        entry_price=Decimal("12.955"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("12.701"),
        zone_high=Decimal("13.099"),
        invalidation=Decimal("12.502"),
        targets=(Decimal("15"),),
        policy_id="geri-countertrend",
        policy_version="1.4.0",
        reasons=("countertrend_ct2",),
    )
    repeated = first.model_copy(update={"signal_id": new_uuid7()})
    reset = first.model_copy(
        update={
            "signal_id": new_uuid7(),
            "countertrend_maturity": GeriCountertrendMaturity.CT1,
        }
    )
    reconfirmed = first.model_copy(update={"signal_id": new_uuid7()})
    state: dict[str, str] = {}

    assert _analytical_stage_changed(first, state) is True
    assert _analytical_stage_changed(repeated, state) is False
    assert _analytical_stage_changed(reset, state) is True
    assert _analytical_stage_changed(reconfirmed, state) is True
