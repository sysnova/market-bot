from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.alert_engine import AlertEngineV2
from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    PatternDirection,
)

NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "d" * 64


def test_alert_v2_has_an_explicit_version() -> None:
    assert AlertEngineV2.engine_id == "alert"
    assert AlertEngineV2.engine_version == "2.0.0"


def analysis(
    horizon: AnalysisHorizon,
    *,
    score: str = "82",
    direction: PatternDirection = PatternDirection.BULLISH,
    verdict: AnalysisVerdict = AnalysisVerdict.FAVORABLE,
    as_of: datetime = NOW - timedelta(minutes=1),
) -> AnalysisResult:
    return AnalysisResult(
        engine_id=f"{horizon.value.lower()}-v2",
        engine_version="2.0.0",
        symbol="HIMS",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal(score),
        confidence=Decimal("0.9"),
        reasons=(f"{horizon.value.lower()} fixture",),
        context_hash=HASH,
    )


@pytest.mark.unit
def test_v2_emits_long_buy_zone_without_waiting_for_other_engines() -> None:
    alert = AlertEngineV2().ingest(analysis(AnalysisHorizon.LONG_TERM), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.LONG_BUY_ZONE
    assert alert.severity is AlertSeverity.WATCH
    assert alert.horizons == (AnalysisHorizon.LONG_TERM,)


@pytest.mark.unit
def test_v2_emits_swing_setup_without_waiting_for_long_or_intraday() -> None:
    alert = AlertEngineV2().ingest(analysis(AnalysisHorizon.SWING), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.SWING_SETUP
    assert alert.severity is AlertSeverity.WATCH


@pytest.mark.unit
def test_v2_confirms_entry_when_swing_and_intraday_are_bullish() -> None:
    engine = AlertEngineV2()
    engine.ingest(analysis(AnalysisHorizon.SWING), now=NOW)

    alert = engine.ingest(analysis(AnalysisHorizon.INTRADAY), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.ENTRY_CONFIRMED
    assert alert.severity is AlertSeverity.ACTION
    assert alert.horizons == (
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )


@pytest.mark.unit
def test_v2_high_conviction_requires_all_three_bullish_engines() -> None:
    engine = AlertEngineV2()
    engine.ingest(analysis(AnalysisHorizon.LONG_TERM), now=NOW)
    engine.ingest(analysis(AnalysisHorizon.SWING), now=NOW)

    alert = engine.ingest(analysis(AnalysisHorizon.INTRADAY), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.HIGH_CONVICTION_BUY
    assert alert.severity is AlertSeverity.ACTION
    assert alert.horizons == (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )


@pytest.mark.unit
def test_v2_does_not_confirm_when_intraday_is_bearish() -> None:
    engine = AlertEngineV2()
    engine.ingest(analysis(AnalysisHorizon.SWING), now=NOW)

    alert = engine.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            direction=PatternDirection.BEARISH,
            verdict=AnalysisVerdict.CAUTION,
        ),
        now=NOW,
    )

    assert alert is None


@pytest.mark.unit
def test_v2_sec_warning_is_informational_and_never_gates_entries() -> None:
    alert = AlertEngineV2().ingest(
        analysis(
            AnalysisHorizon.DILUTION,
            direction=PatternDirection.BEARISH,
            verdict=AnalysisVerdict.AVOID,
        ),
        now=NOW,
    )

    assert alert is not None
    assert alert.kind is AlertKind.SEC_WARNING
    assert alert.severity is AlertSeverity.WATCH
    assert "does not gate entries" in alert.message
