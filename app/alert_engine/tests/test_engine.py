from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.alert_engine import AlertEngine
from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    PatternDirection,
)

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
FIXTURES = Path(__file__).parents[1] / "fixtures" / "aggregation_cases.json"


def _analysis(
    horizon: AnalysisHorizon,
    *,
    score: str,
    direction: PatternDirection = PatternDirection.NEUTRAL,
    verdict: AnalysisVerdict = AnalysisVerdict.FAVORABLE,
    as_of: datetime = NOW - timedelta(minutes=1),
) -> AnalysisResult:
    return AnalysisResult(
        engine_id=f"fixture-{horizon.value.lower()}",
        engine_version="1.0.0",
        symbol="TEST",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal(score),
        confidence=Decimal("1"),
        reasons=(f"{horizon.value.lower()} fixture",),
        context_hash=HASH,
    )


def _feed_case(
    engine: AlertEngine, case: dict[str, Any], now: datetime = NOW
) -> LocalAlert | None:
    alert = None
    for horizon in AnalysisHorizon:
        if horizon is AnalysisHorizon.DILUTION:
            result = _analysis(
                horizon,
                score=case["scores"][horizon.value],
                verdict=AnalysisVerdict(case["dilution_verdict"]),
                direction=(
                    PatternDirection.BEARISH
                    if case["dilution_verdict"] != "FAVORABLE"
                    else PatternDirection.NEUTRAL
                ),
            )
        else:
            result = _analysis(
                horizon,
                score=case["scores"][horizon.value],
                direction=PatternDirection(case["directions"][horizon.value]),
            )
        alert = engine.ingest(result, now=now) or alert
    return alert


@pytest.mark.unit
@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text(encoding="utf-8")))
def test_weighted_policy_and_dilution_overlay(case: dict[str, Any]) -> None:
    alert = _feed_case(AlertEngine(), case)

    assert alert is not None
    assert alert.severity.value == case["expected_severity"]
    assert alert.horizons == tuple(AnalysisHorizon)
    assert alert.expires_at == NOW + timedelta(minutes=15)
    assert "TEST" in alert.title


@pytest.mark.unit
def test_dilution_avoid_vetoes_bullish_composite_with_critical_warning() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[2]

    alert = _feed_case(AlertEngine(), case)

    assert alert is not None
    assert alert.severity is AlertSeverity.CRITICAL
    assert "DILUTION VETO" in alert.title
    assert "dilution_avoid_veto" in alert.reasons


@pytest.mark.unit
def test_bearish_consensus_is_distinct_from_bullish() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    bearish = {
        **case,
        "directions": {
            "LONG_TERM": "BEARISH",
            "SWING": "BEARISH",
            "INTRADAY": "BEARISH",
        },
    }

    alert = _feed_case(AlertEngine(), bearish)

    assert alert is not None
    assert alert.severity is AlertSeverity.ACTION
    assert "BEARISH" in alert.title
    assert "bearish_consensus" in alert.reasons


@pytest.mark.unit
def test_stale_components_are_retained_but_not_aggregated() -> None:
    engine = AlertEngine()
    stale = NOW - timedelta(days=8)
    result = _analysis(
        AnalysisHorizon.LONG_TERM,
        score="95",
        direction=PatternDirection.BULLISH,
        as_of=stale,
    )

    assert engine.ingest(result, now=NOW) is None
    assert engine.latest("TEST")[AnalysisHorizon.LONG_TERM] == result


@pytest.mark.unit
def test_older_result_does_not_replace_latest() -> None:
    engine = AlertEngine()
    latest = _analysis(
        AnalysisHorizon.SWING,
        score="80",
        direction=PatternDirection.BULLISH,
    )
    older = _analysis(
        AnalysisHorizon.SWING,
        score="95",
        direction=PatternDirection.BULLISH,
        as_of=latest.as_of - timedelta(minutes=1),
    )

    assert engine.ingest(latest, now=NOW) is None
    assert engine.ingest(older, now=NOW) is None
    assert engine.latest("TEST")[AnalysisHorizon.SWING] == latest


@pytest.mark.unit
def test_cooldown_suppresses_repeat_but_allows_escalation() -> None:
    caution_case = json.loads(FIXTURES.read_text(encoding="utf-8"))[1]
    engine = AlertEngine()
    first = _feed_case(engine, caution_case)
    assert first is not None and first.severity is AlertSeverity.WATCH

    replacement = _analysis(
        AnalysisHorizon.DILUTION,
        score="5",
        verdict=AnalysisVerdict.FAVORABLE,
        as_of=NOW,
    )
    escalated = engine.ingest(replacement, now=NOW + timedelta(minutes=1))
    assert escalated is not None
    assert escalated.severity is AlertSeverity.ACTION

    repeated = _analysis(
        AnalysisHorizon.INTRADAY,
        score="89",
        direction=PatternDirection.BULLISH,
        as_of=NOW + timedelta(minutes=2),
    )
    assert engine.ingest(repeated, now=NOW + timedelta(minutes=2)) is None


@pytest.mark.unit
def test_future_analysis_is_rejected() -> None:
    engine = AlertEngine()
    future = _analysis(
        AnalysisHorizon.INTRADAY,
        score="80",
        direction=PatternDirection.BULLISH,
        as_of=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="future"):
        engine.ingest(future, now=NOW)
