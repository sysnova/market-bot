from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.alert_engine.outcomes import evaluate_solid_buy_outcomes
from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    BarTimeframe,
    LocalAlert,
    MarketBar,
    NamedValue,
    new_uuid7,
)

NOW = datetime(2026, 7, 27, 14, 30, tzinfo=UTC)


def _alert(*, kind: AlertKind = AlertKind.ENTRY_CONFIRMED) -> LocalAlert:
    return LocalAlert(
        symbol="TEST",
        created_at=NOW,
        severity=AlertSeverity.ACTION,
        title="TEST ENTRY CONFIRMED",
        message="Swing and Intraday confirmed",
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analysis_ids=(new_uuid7(),),
        metrics=(
            NamedValue(name="current_price", value=Decimal("100")),
            NamedValue(name="invalidation", value=Decimal("95")),
            NamedValue(name="objective", value=Decimal("105")),
            NamedValue(name="entry_confirmation_rule_version", value="3.0.0"),
        ),
        score=Decimal("88"),
        reasons=("entry_confirmed",),
        deduplication_key="alert:test:entry-confirmed",
        kind=kind,
    )


def _bar(
    minutes: int,
    *,
    close: str,
    high: str | None = None,
    low: str | None = None,
) -> MarketBar:
    closing = Decimal(close)
    return MarketBar(
        symbol="TEST",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW + timedelta(minutes=minutes),
        open=closing,
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=closing,
        volume=Decimal("1000"),
        source="fixture",
        feed="sip",
    )


@pytest.mark.unit
def test_solid_buy_outcome_measures_declared_horizons_without_lookahead() -> None:
    bars = (
        _bar(-1, close="200"),
        _bar(1, close="100.5", high="101", low="99.5"),
        _bar(15, close="102", high="102.5", low="101"),
        _bar(30, close="99", high="100", low="98"),
        _bar(60, close="104", high="106", low="103"),
        _bar(329, close="105", high="105.5", low="104"),
    )

    outcome = evaluate_solid_buy_outcomes((_alert(),), {"TEST": bars})[0]

    assert outcome.entry_price == Decimal("100")
    assert outcome.return_15m == Decimal("2.0000")
    assert outcome.return_30m == Decimal("-1.0000")
    assert outcome.return_60m == Decimal("4.0000")
    assert outcome.return_close == Decimal("5.0000")
    assert outcome.mfe_percent == Decimal("6.0000")
    assert outcome.mae_percent == Decimal("-2.0000")
    assert outcome.first_level_hit == "TARGET"
    assert outcome.entry_confirmation_rule_versions == ("3.0.0",)


@pytest.mark.unit
def test_outcome_evaluator_excludes_non_solid_buy_zones() -> None:
    alert = _alert(kind=AlertKind.LONG_BUY_ZONE).model_copy(
        update={"horizons": (AnalysisHorizon.LONG_TERM,)}
    )

    assert evaluate_solid_buy_outcomes((alert,), {"TEST": ()}) == ()
