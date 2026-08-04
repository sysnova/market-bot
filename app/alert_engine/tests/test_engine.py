from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.alert_engine import AlertEngine
from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    EntryWatchTransition,
    LocalAlert,
    NamedValue,
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
    metrics: tuple[NamedValue, ...] = (),
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
        metrics=metrics,
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
def test_dilution_avoid_is_warning_only_for_bullish_composite() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[2]

    alert = _feed_case(AlertEngine(), case)

    assert alert is not None
    assert alert.severity is AlertSeverity.CRITICAL
    assert alert.score == Decimal("92.00")
    assert "BULLISH" in alert.title
    assert "dilution_avoid_warning" in alert.reasons


@pytest.mark.unit
def test_directional_alert_does_not_require_sec_analysis() -> None:
    engine = AlertEngine()
    alert = None
    for horizon in (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    ):
        alert = engine.ingest(
            _analysis(
                horizon,
                score="88",
                direction=PatternDirection.BULLISH,
            ),
            now=NOW,
        ) or alert

    assert alert is not None
    assert alert.severity is AlertSeverity.ACTION
    assert "dilution_analysis_unavailable" in alert.reasons
    assert tuple(item.horizon for item in alert.component_analyses) == (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )


@pytest.mark.unit
def test_entry_alert_includes_latest_analysis_context() -> None:
    engine = AlertEngine()
    for horizon in (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    ):
        engine.ingest(
            _analysis(
                horizon,
                score="84",
                direction=PatternDirection.BULLISH,
                metrics=(NamedValue(name="reference_price", value=Decimal("103")),),
            ),
            now=NOW,
        )
    transition = EntryWatchTransition(
        watch_id=UUID("0195f3a5-9000-7000-8000-000000000001"),
        symbol="TEST",
        previous_status=EntryWatchStatus.IN_ZONE,
        status=EntryWatchStatus.TRIGGERED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("103"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("multi_horizon_entry_confirmed",),
        horizons=(
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
        source_analysis_ids=(
            UUID("0195f3a5-9000-7000-8000-000000000002"),
        ),
    )

    alert = engine.ingest_entry_watch(transition, now=NOW)

    assert tuple(item.horizon for item in alert.component_analyses) == (
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )


@pytest.mark.unit
def test_sec_avoid_emits_standalone_watch_without_gating_entries() -> None:
    result = _analysis(
        AnalysisHorizon.DILUTION,
        score="82",
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
    )

    alert = AlertEngine().ingest(result, now=NOW)

    assert alert is not None
    assert alert.severity is AlertSeverity.WATCH
    assert "SEC DILUTION WARNING" in alert.title
    assert "dilution_avoid_warning" in alert.reasons
    assert "does not gate entries" in alert.message


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
    caution_case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    caution_case = {
        **caution_case,
        "scores": {
            "LONG_TERM": "70",
            "DILUTION": "5",
            "SWING": "70",
            "INTRADAY": "70",
        },
    }
    engine = AlertEngine()
    first = _feed_case(engine, caution_case)
    assert first is not None and first.severity is AlertSeverity.WATCH

    replacement = _analysis(
        AnalysisHorizon.INTRADAY,
        score="100",
        direction=PatternDirection.BULLISH,
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


@pytest.mark.unit
def test_triggered_entry_watch_becomes_an_action_alert() -> None:
    transition = EntryWatchTransition(
        watch_id=UUID("0195f3a5-9000-7000-8000-000000000001"),
        symbol="TEST",
        previous_status=EntryWatchStatus.IN_ZONE,
        status=EntryWatchStatus.TRIGGERED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("103"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("multi_horizon_entry_confirmed",),
        horizons=(
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.DILUTION,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
        source_analysis_ids=(
            UUID("0195f3a5-9000-7000-8000-000000000002"),
        ),
    )

    alert = AlertEngine().ingest_entry_watch(transition, now=NOW)

    assert alert.severity is AlertSeverity.ACTION
    assert "ENTRY TRIGGERED" in alert.title
    assert alert.deduplication_key.endswith(":triggered")


@pytest.mark.unit
def test_in_zone_entry_watch_is_an_explicit_early_entry_watch() -> None:
    transition = EntryWatchTransition(
        watch_id=UUID("0195f3a5-9000-7000-8000-000000000001"),
        symbol="TEST",
        previous_status=EntryWatchStatus.ARMED,
        status=EntryWatchStatus.IN_ZONE,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("103"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("target_zone_reached", "awaiting_entry_confirmation"),
        horizons=(AnalysisHorizon.LONG_TERM,),
        source_analysis_ids=(
            UUID("0195f3a5-9000-7000-8000-000000000002"),
        ),
    )

    alert = AlertEngine().ingest_entry_watch(transition, now=NOW)

    assert alert.severity is AlertSeverity.WATCH
    assert "ENTRY IN_ZONE EARLY WATCH" in alert.title
    assert "early entry watch" in alert.message.lower()


@pytest.mark.unit
def test_moderate_breakaway_emits_watch_while_intraday_confirmation_is_pending() -> None:
    transition = EntryWatchTransition(
        watch_id=UUID("0195f3a5-9000-7000-8000-000000000001"),
        symbol="TEST",
        previous_status=EntryWatchStatus.IN_ZONE,
        status=EntryWatchStatus.ARMED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("107"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=(
            "breakaway_continuation_pending",
            "awaiting_fresh_intraday_confirmation",
        ),
        horizons=(AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING),
        source_analysis_ids=(
            UUID("0195f3a5-9000-7000-8000-000000000002"),
        ),
    )

    alert = AlertEngine().ingest_entry_watch(transition, now=NOW)

    assert alert.severity is AlertSeverity.WATCH
    assert "ENTRY BREAKAWAY WATCH" in alert.title
    assert "recent zone touch" in alert.message.lower()
