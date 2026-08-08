from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.alert_engine import AlertEngineV3, BuyMaturity, buy_maturity
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 7, 18, 18, tzinfo=UTC)
HASH = "sha256:" + "e" * 64


def analysis(
    horizon: AnalysisHorizon,
    *,
    as_of: datetime,
    direction: PatternDirection = PatternDirection.BULLISH,
    verdict: AnalysisVerdict = AnalysisVerdict.FAVORABLE,
    score: str = "100",
    confidence: str = "1",
    quality: str | None = None,
    higher_low: bool | None = None,
) -> AnalysisResult:
    metrics: list[NamedValue] = [NamedValue(name="reference_price", value="81.30")]
    if horizon is AnalysisHorizon.SWING:
        metrics.extend(
            (
                NamedValue(name="classification", value="pullback"),
                NamedValue(name="anchored_vwap_gate_passed", value=True),
            )
        )
    if horizon is AnalysisHorizon.INTRADAY:
        metrics.extend(
            (
                NamedValue(name="setup", value="bullish_breakout"),
                NamedValue(name="confirmation_quality", value=quality),
                NamedValue(name="five_minute_higher_low", value=higher_low),
            )
        )
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version=("4.0.0" if horizon is AnalysisHorizon.INTRADAY else "3.0.0"),
        symbol="TMDX",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal(score),
        confidence=Decimal(confidence),
        reasons=("fixture",),
        metrics=tuple(metrics),
        context_hash=HASH,
    )


def strong_higher_low(as_of: datetime) -> AnalysisResult:
    return analysis(
        AnalysisHorizon.INTRADAY,
        as_of=as_of,
        verdict=AnalysisVerdict.WATCH,
        score="64",
        confidence="0.64",
        quality="strong",
        higher_low=True,
    )


@pytest.mark.unit
def test_v3_confirms_swing_continuation_after_two_fresh_strong_higher_lows_without_long() -> None:
    engine = AlertEngineV3()
    engine.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            as_of=NOW,
            direction=PatternDirection.BEARISH,
            verdict=AnalysisVerdict.AVOID,
            score="17.25",
            confidence="0.1725",
        ),
        now=NOW,
    )
    engine.ingest(analysis(AnalysisHorizon.SWING, as_of=NOW), now=NOW)

    first = engine.ingest(strong_higher_low(NOW), now=NOW)
    confirmed = engine.ingest(
        strong_higher_low(NOW + timedelta(minutes=3)),
        now=NOW + timedelta(minutes=3),
    )

    assert first is None
    assert confirmed is not None
    assert confirmed.kind is AlertKind.ENTRY_CONFIRMED
    assert confirmed.horizons == (AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY)
    assert buy_maturity(confirmed) is BuyMaturity.SWING_CONFIRMED
    assert "two_strong_intraday_higher_lows" in confirmed.reasons


@pytest.mark.unit
def test_v3_requires_the_configured_delay_between_higher_lows() -> None:
    engine = AlertEngineV3()
    engine.ingest(analysis(AnalysisHorizon.SWING, as_of=NOW), now=NOW)

    engine.ingest(strong_higher_low(NOW), now=NOW)
    too_soon = engine.ingest(
        strong_higher_low(NOW + timedelta(minutes=2)),
        now=NOW + timedelta(minutes=2),
    )
    confirmed = engine.ingest(
        strong_higher_low(NOW + timedelta(minutes=3)),
        now=NOW + timedelta(minutes=3),
    )

    assert too_soon is None
    assert confirmed is not None
    assert confirmed.kind is AlertKind.ENTRY_CONFIRMED


@pytest.mark.unit
def test_v3_does_not_count_weak_or_cross_session_readings() -> None:
    engine = AlertEngineV3()
    engine.ingest(analysis(AnalysisHorizon.SWING, as_of=NOW), now=NOW)
    weak = analysis(
        AnalysisHorizon.INTRADAY,
        as_of=NOW,
        verdict=AnalysisVerdict.WATCH,
        score="64",
        confidence="0.64",
        quality="weak",
        higher_low=True,
    )

    assert engine.ingest(weak, now=NOW) is None
    assert engine.ingest(
        strong_higher_low(NOW + timedelta(minutes=3)),
        now=NOW + timedelta(minutes=3),
    ) is None

    next_session = NOW + timedelta(days=1)
    assert engine.ingest(strong_higher_low(next_session), now=next_session) is None
