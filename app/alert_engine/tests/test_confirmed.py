from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.alert_engine.confirmed import (
    BuyMaturity,
    buy_maturity,
    is_buy_alert,
    is_confirmed_buy,
    is_portfolio_monitor_alert,
    is_solid_buy,
)
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
    assert is_solid_buy(
        alert.model_copy(
            update={
                "kind": AlertKind.HIGH_CONVICTION_BUY,
                "horizons": (
                    AnalysisHorizon.LONG_TERM,
                    AnalysisHorizon.SWING,
                    AnalysisHorizon.INTRADAY,
                ),
            }
        )
    )


def test_entry_confirmations_keep_distinct_maturity_levels(alert: LocalAlert) -> None:
    tactical = alert.model_copy(
        update={
            "kind": AlertKind.ENTRY_CONFIRMED,
            "horizons": (AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY),
        }
    )
    swing = alert.model_copy(update={"kind": AlertKind.ENTRY_CONFIRMED})
    conviction = alert.model_copy(
        update={
            "kind": AlertKind.HIGH_CONVICTION_BUY,
            "horizons": (
                AnalysisHorizon.LONG_TERM,
                AnalysisHorizon.SWING,
                AnalysisHorizon.INTRADAY,
            ),
        }
    )

    assert buy_maturity(tactical) is BuyMaturity.TACTICAL_RECOVERY
    assert buy_maturity(swing) is BuyMaturity.SWING_CONFIRMED
    assert buy_maturity(conviction) is BuyMaturity.HIGH_CONVICTION
    assert is_buy_alert(tactical)
    assert not is_solid_buy(tactical)
    assert is_confirmed_buy(
        alert.model_copy(
            update={
                "kind": AlertKind.ENTRY_WATCH,
                "title": "HIMS ENTRY TRIGGERED",
                "horizons": (
                    AnalysisHorizon.LONG_TERM,
                    AnalysisHorizon.SWING,
                    AnalysisHorizon.INTRADAY,
                ),
            }
        )
    )


def test_confirmed_buy_rejects_unconfirmed_buy_zone(alert: LocalAlert) -> None:
    assert not is_confirmed_buy(alert.model_copy(update={"kind": AlertKind.LONG_BUY_ZONE}))


def test_confirmed_buy_accepts_long_intraday_tactical_recovery(alert: LocalAlert) -> None:
    tactical = alert.model_copy(
        update={
            "kind": AlertKind.ENTRY_CONFIRMED,
            "horizons": (AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY),
        }
    )

    assert is_confirmed_buy(tactical)
    assert buy_maturity(tactical) is BuyMaturity.TACTICAL_RECOVERY


def test_portfolio_monitor_accepts_protect_alert(alert: LocalAlert) -> None:
    protect = alert.model_copy(
        update={"kind": AlertKind.PORTFOLIO_PROTECT, "title": "PROTECT HIMS"}
    )
    assert is_portfolio_monitor_alert(protect)
    assert not is_confirmed_buy(protect)


def test_portfolio_monitor_accepts_buy_pressure_without_promoting_maturity(
    alert: LocalAlert,
) -> None:
    buy_pressure = alert.model_copy(
        update={
            "kind": AlertKind.PORTFOLIO_FLOW_BUY,
            "title": "AGGRESSIVE ENTRY WATCH HIMS",
        }
    )
    assert is_portfolio_monitor_alert(buy_pressure)
    assert not is_confirmed_buy(buy_pressure)
    assert buy_maturity(buy_pressure) is None


def test_portfolio_monitor_accepts_long_portfolio_buy(alert: LocalAlert) -> None:
    long_buy = alert.model_copy(update={"kind": AlertKind.LONG_PORTFOLIO_BUY})
    assert is_portfolio_monitor_alert(long_buy)
    assert is_solid_buy(long_buy)
    assert buy_maturity(long_buy) is BuyMaturity.FULLY_MATURED
