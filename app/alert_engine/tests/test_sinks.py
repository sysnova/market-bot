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
    AlertKind,
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
def test_console_sink_does_not_ring_for_non_solid_analysis(alert: LocalAlert) -> None:
    stream = StringIO()

    ConsoleAlertSink(stream=stream, bell=True).emit(alert)

    assert "[ACTION] TEST" in stream.getvalue()
    assert "score=82" in stream.getvalue()
    assert "\a" not in stream.getvalue()


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
            NamedValue(name="weekly_price_vs_sma200_percent", value=Decimal("28.75")),
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
def test_console_sink_highlights_solid_buy_at_confirmed_market_price() -> None:
    analysis = AnalysisResult(
        engine_id="long-term",
        engine_version="1.1.1",
        symbol="HIMS",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("90"),
        confidence=Decimal("0.95"),
        reasons=("price_in_buy_zone",),
        metrics=(
            NamedValue(name="reference_price", value=Decimal("41.20")),
            NamedValue(name="buy_zone_low", value=Decimal("40")),
            NamedValue(name="buy_zone_high", value=Decimal("42")),
        ),
        context_hash="sha256:" + "b" * 64,
    )
    buyable = _alert(AlertSeverity.CRITICAL).model_copy(
        update={
            "symbol": "HIMS",
            "kind": AlertKind.HIGH_CONVICTION_BUY,
            "title": "HIMS HIGH CONVICTION BUY",
            "horizons": (
                AnalysisHorizon.LONG_TERM,
                AnalysisHorizon.SWING,
                AnalysisHorizon.INTRADAY,
            ),
            "component_analyses": (analysis,),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True, bell=True).emit(buyable)

    first_line = stream.getvalue().splitlines()[0]
    assert first_line == ("\x1b[1;30;102m HIMS | BUY L3 $41.2 | HIGH CONVICTION \x1b[0m")
    assert stream.getvalue().endswith("\a\n")


@pytest.mark.unit
def test_console_sink_does_not_highlight_long_buy_zone_as_solid() -> None:
    alert = _alert().model_copy(update={"kind": AlertKind.LONG_BUY_ZONE})
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True, bell=True).emit(alert)

    assert "SOLID BUY" not in stream.getvalue()
    assert "\x1b[" not in stream.getvalue()
    assert "\a" not in stream.getvalue()


@pytest.mark.unit
def test_console_sink_highlights_aggressive_buy_pressure_without_an_l_level() -> None:
    flow = _alert().model_copy(
        update={
            "kind": AlertKind.PORTFOLIO_FLOW_BUY,
            "title": "AGGRESSIVE ENTRY WATCH TEST",
            "metrics": (NamedValue(name="current_price", value=Decimal("100.6")),),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True, bell=True).emit(flow)

    assert stream.getvalue().splitlines()[0] == (
        "\x1b[1;30;103m TEST | BUY FLOW $100.6 | AGGRESSIVE ENTRY WATCH \x1b[0m"
    )
    assert "BUY L" not in stream.getvalue()
    assert "\a" not in stream.getvalue()


@pytest.mark.unit
def test_console_sink_labels_early_intraday_as_unconfirmed_watch() -> None:
    early = _alert(AlertSeverity.WATCH).model_copy(
        update={
            "kind": AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION,
            "title": "TEST EARLY INTRADAY WITHOUT CONFIRMATION",
            "metrics": (NamedValue(name="current_price", value=Decimal("315.14")),),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True, bell=True).emit(early)

    assert stream.getvalue().splitlines()[0] == (
        "\x1b[0;27;49;1;93m TEST | EARLY INTRADAY $315.14 | "
        "WITHOUT CONFIRMATION \x1b[0m"
    )
    assert "BUY L" not in stream.getvalue()
    assert "\a" not in stream.getvalue()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "label"),
    (
        ("TEST ENTRY IN_ZONE EARLY WATCH", "IN ZONE"),
        ("TEST ENTRY BREAKAWAY WATCH", "BREAKAWAY WATCH"),
    ),
)
def test_console_sink_highlights_entry_watch_candidate_price(
    title: str,
    label: str,
) -> None:
    watch = _alert(AlertSeverity.WATCH).model_copy(
        update={
            "kind": AlertKind.ENTRY_WATCH,
            "title": title,
            "metrics": (NamedValue(name="current_price", value=Decimal("311.19")),),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True).emit(watch)

    assert stream.getvalue().splitlines()[0] == (
        f"\x1b[0;27;49;1;93m TEST | {label} | "
        "ENTRY CANDIDATE $311.19 \x1b[0m"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "horizon", "label", "style"),
    (
        (
            AlertKind.SWING_SETUP,
            AnalysisHorizon.SWING,
            "SWING SETUP",
            "\x1b[0;27;49;1;96m",
        ),
        (
            AlertKind.LONG_BUY_ZONE,
            AnalysisHorizon.LONG_TERM,
            "LONG BUY ZONE",
            "\x1b[0;27;49;1;92m",
        ),
    ),
)
def test_console_sink_highlights_analysis_candidate_reference_price(
    kind: AlertKind,
    horizon: AnalysisHorizon,
    label: str,
    style: str,
) -> None:
    analysis = AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="4.0.0",
        symbol="TEST",
        horizon=horizon,
        as_of=NOW,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("70"),
        confidence=Decimal("0.7"),
        reasons=("candidate",),
        metrics=(NamedValue(name="reference_price", value=Decimal("311.19")),),
        context_hash="sha256:" + "d" * 64,
    )
    candidate = _alert(AlertSeverity.WATCH).model_copy(
        update={
            "kind": kind,
            "title": f"TEST {label}",
            "horizons": (horizon,),
            "component_analyses": (analysis,),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True).emit(candidate)

    assert stream.getvalue().splitlines()[0] == (
        f"{style} TEST | {label} | ENTRY CANDIDATE $311.19 \x1b[0m"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("horizons", "expected"),
    (
        (
            (AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY),
            "\x1b[1;30;103m TEST | BUY L1 $103 | TACTICAL RECOVERY \x1b[0m",
        ),
        (
            (AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            "\x1b[1;97;44m TEST | BUY L2 $103 | SWING CONFIRMED \x1b[0m",
        ),
    ),
)
def test_console_sink_distinguishes_entry_maturity_by_horizons(
    horizons: tuple[AnalysisHorizon, ...], expected: str
) -> None:
    analysis = AnalysisResult(
        engine_id="intraday",
        engine_version="3.0.0",
        symbol="TEST",
        horizon=AnalysisHorizon.INTRADAY,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("85"),
        confidence=Decimal("0.85"),
        reasons=("confirmation_gate_passed",),
        metrics=(NamedValue(name="reference_price", value=Decimal("103")),),
        context_hash="sha256:" + "c" * 64,
    )
    alert = _alert().model_copy(
        update={
            "kind": AlertKind.ENTRY_CONFIRMED,
            "horizons": horizons,
            "component_analyses": (analysis,),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True, bell=True).emit(alert)

    assert stream.getvalue().splitlines()[0] == expected
    assert stream.getvalue().endswith("\a\n")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("maturity", "expected_style"),
    (
        ("ARMED", "\x1b[1;97;44m"),
        ("IN_ZONE", "\x1b[1;30;103m"),
        ("L1", "\x1b[1;30;103m"),
        ("L2", "\x1b[1;97;44m"),
        ("L3", "\x1b[1;30;102m"),
        ("L4", "\x1b[1;97;45m"),
    ),
)
def test_console_sink_uses_high_contrast_entry_progress_palette(
    maturity: str, expected_style: str
) -> None:
    progress = _alert().model_copy(
        update={
            "kind": AlertKind.ENTRY_OPPORTUNITY_PROGRESS,
            "metrics": (
                NamedValue(name="progress_percent", value=Decimal("60")),
                NamedValue(name="maturity", value=maturity),
            ),
        }
    )
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True).emit(progress)

    assert stream.getvalue().splitlines()[0].startswith(expected_style)


@pytest.mark.unit
def test_console_sink_does_not_highlight_non_buy_alert(alert: LocalAlert) -> None:
    stream = StringIO()

    ConsoleAlertSink(stream=stream, color=True).emit(alert)

    assert "\x1b[" not in stream.getvalue()


@pytest.mark.unit
def test_ndjson_sink_is_append_only_and_idempotent(tmp_path: Path, alert: LocalAlert) -> None:
    base_path = tmp_path / "alerts.ndjson"
    daily_path = tmp_path / "alerts-2026-07-26.ndjson"
    sink = NdjsonAlertSink(base_path)

    first = sink.emit(alert)
    duplicate = sink.emit(alert)
    reopened = NdjsonAlertSink(base_path).emit(alert)

    assert first.path == daily_path
    assert first.persisted is True
    assert duplicate.duplicate is True
    assert reopened.duplicate is True
    assert not base_path.exists()
    lines = daily_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["deduplication_key"] == alert.deduplication_key


@pytest.mark.unit
def test_ndjson_sink_rotates_by_new_york_market_date(tmp_path: Path, alert: LocalAlert) -> None:
    sink = NdjsonAlertSink(tmp_path / "marketbot-alerts.ndjson")
    late_sunday_utc = alert.model_copy(
        update={
            "created_at": datetime(2026, 7, 27, 1, 0, tzinfo=UTC),
            "deduplication_key": "alert:sunday-market-date",
        }
    )
    monday = alert.model_copy(
        update={
            "created_at": datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
            "deduplication_key": "alert:monday-market-date",
        }
    )

    sunday_receipt = sink.emit(late_sunday_utc)
    monday_receipt = sink.emit(monday)

    assert sunday_receipt.path.name == "marketbot-alerts-2026-07-26.ndjson"
    assert monday_receipt.path.name == "marketbot-alerts-2026-07-27.ndjson"
    assert len(sunday_receipt.path.read_text(encoding="utf-8").splitlines()) == 1
    assert len(monday_receipt.path.read_text(encoding="utf-8").splitlines()) == 1


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
