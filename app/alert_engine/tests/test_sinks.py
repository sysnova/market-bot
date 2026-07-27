from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from app.alert_engine import AlertDispatcher, ConsoleAlertSink, NdjsonAlertSink
from app.contracts import (
    LOCAL_ALERT_EVENT,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    NamedValue,
    PatternDirection,
    local_alert_subject,
    new_uuid7,
)

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)


def _alert(severity: AlertSeverity = AlertSeverity.ACTION) -> LocalAlert:
    return LocalAlert(
        symbol="TEST",
        created_at=NOW,
        severity=severity,
        title="TEST BULLISH ACTION",
        message="weighted score 82.00; inspect the component analyses",
        horizons=(AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("82"),
        reasons=("bullish_consensus",),
        deduplication_key="alert:test:bullish:action:1",
        expires_at=NOW + timedelta(minutes=15),
    )


@pytest.fixture
def alert() -> LocalAlert:
    return _alert()


@pytest.mark.unit
def test_console_sink_is_readable_and_bell_is_optional(alert: LocalAlert) -> None:
    stream = StringIO()

    ConsoleAlertSink(stream=stream, bell=True).emit(alert)

    assert "[ACTION] TEST" in stream.getvalue()
    assert "score=82" in stream.getvalue()
    assert stream.getvalue().endswith("\a\n")


@pytest.mark.unit
def test_console_sink_renders_actionable_component_context() -> None:
    analysis = AnalysisResult(
        engine_id="long-term",
        engine_version="1.1.1",
        symbol="TEST",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("84"),
        confidence=Decimal("0.9"),
        reasons=("weekly_structure_constructive",),
        metrics=(
            NamedValue(name="classification", value="buy_zone"),
            NamedValue(name="reference_price", value=Decimal("103")),
            NamedValue(name="buy_zone_low", value=Decimal("100")),
            NamedValue(name="buy_zone_high", value=Decimal("105")),
            NamedValue(name="invalidation", value=Decimal("92")),
            NamedValue(name="weekly_sma200", value=Decimal("80")),
            NamedValue(
                name="weekly_price_vs_sma200_percent", value=Decimal("28.75")
            ),
        ),
        context_hash="sha256:" + "a" * 64,
    )
    enriched = _alert().model_copy(update={"component_analyses": (analysis,)})
    stream = StringIO()

    ConsoleAlertSink(stream=stream).emit(enriched)

    output = stream.getvalue()
    assert "Price $103" in output
    assert "Buy zone $100 - $105" in output
    assert "Invalidation $92" in output
    assert "LONG favorable / bullish / score 84" in output
    assert "SMA200W $80 (+28.75%)" in output
    assert "weekly_structure_constructive" in output


@pytest.mark.unit
def test_ndjson_sink_is_append_only_and_idempotent(
    tmp_path: Path, alert: LocalAlert
) -> None:
    path = tmp_path / "alerts.ndjson"
    sink = NdjsonAlertSink(path)

    first = sink.emit(alert)
    duplicate = sink.emit(alert)
    reopened = NdjsonAlertSink(path).emit(alert)

    assert first.persisted is True
    assert duplicate.duplicate is True
    assert reopened.duplicate is True
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["deduplication_key"] == alert.deduplication_key


class PublisherSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, LocalAlert]] = []

    async def publish(self, event_type: str, subject: str, alert: LocalAlert) -> None:
        self.calls.append((event_type, subject, alert))


class SinkSpy:
    def __init__(self) -> None:
        self.alerts: list[LocalAlert] = []

    def emit(self, alert: LocalAlert) -> None:
        self.alerts.append(alert)


@pytest.mark.unit
async def test_dispatcher_uses_contract_event_and_subject(alert: LocalAlert) -> None:
    sink = SinkSpy()
    publisher = PublisherSpy()

    await AlertDispatcher(sinks=(sink,), publisher=publisher).dispatch(alert)

    assert sink.alerts == [alert]
    assert publisher.calls == [
        (LOCAL_ALERT_EVENT, local_alert_subject(alert.severity, alert.symbol), alert)
    ]
