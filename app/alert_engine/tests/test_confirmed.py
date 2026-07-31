from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.alert_engine.confirmed import is_confirmed_buy, is_portfolio_monitor_alert
from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    LocalAlert,
    new_uuid7,
)


@pytest.fixture
def alert() -> LocalAlert:
    return LocalAlert(
        symbol="HIMS",
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
        severity=AlertSeverity.ACTION,
        title="HIMS SIGNAL",
        message="confirmed test signal",
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("80"),
        reasons=("test_confirmation",),
        deduplication_key="confirmed:test:hims",
    )


def test_confirmed_buy_accepts_confirmations_and_triggered_entry(alert: LocalAlert) -> None:
    assert is_confirmed_buy(alert.model_copy(update={"kind": AlertKind.ENTRY_CONFIRMED}))
    assert is_confirmed_buy(alert.model_copy(update={"kind": AlertKind.HIGH_CONVICTION_BUY}))
    assert is_confirmed_buy(
        alert.model_copy(
            update={"kind": AlertKind.ENTRY_WATCH, "title": "HIMS ENTRY TRIGGERED"}
        )
    )


def test_confirmed_buy_rejects_unconfirmed_buy_zone(alert: LocalAlert) -> None:
    assert not is_confirmed_buy(alert.model_copy(update={"kind": AlertKind.LONG_BUY_ZONE}))


def test_portfolio_monitor_accepts_protect_alert(alert: LocalAlert) -> None:
    protect = alert.model_copy(
        update={"kind": AlertKind.PORTFOLIO_PROTECT, "title": "PROTECT HIMS"}
    )
    assert is_portfolio_monitor_alert(protect)
    assert not is_confirmed_buy(protect)


def test_portfolio_monitor_accepts_long_portfolio_buy(alert: LocalAlert) -> None:
    long_buy = alert.model_copy(update={"kind": AlertKind.LONG_PORTFOLIO_BUY})
    assert is_portfolio_monitor_alert(long_buy)
    assert not is_confirmed_buy(long_buy)
