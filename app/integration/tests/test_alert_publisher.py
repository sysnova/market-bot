from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.alert_engine import AlertEngineV3
from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryMaturityLevel,
    EntrySignal,
    LocalAlert,
    NamedValue,
    PatternDirection,
    new_uuid7,
)
from app.integration.alert_publisher import AlertEventPublisher


class Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []

    async def publish(self, subject: str, envelope: object) -> None:
        self.items.append((subject, envelope))


@pytest.mark.unit
async def test_alert_publisher_wraps_local_alert_without_order_intent() -> None:
    recorder = Recorder()
    publisher = AlertEventPublisher(recorder)  # type: ignore[arg-type]
    alert = LocalAlert(
        symbol="AAPL",
        created_at=datetime(2026, 7, 24, 18, 0, tzinfo=UTC),
        severity=AlertSeverity.ACTION,
        title="AAPL BULLISH ACTION",
        message="inspect analyses",
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("80"),
        reasons=("bullish_consensus",),
        deduplication_key="aapl:action:1",
    )

    await publisher.publish(
        "alert.local.produced",
        "marketbot.v1.alert.local.ACTION.AAPL",
        alert,
    )

    subject, envelope = recorder.items[0]
    assert subject == "marketbot.v1.alert.local.ACTION.AAPL"
    assert envelope.event_type == "alert.local.produced"  # type: ignore[attr-defined]
    assert envelope.payload == alert  # type: ignore[attr-defined]


@pytest.mark.unit
async def test_alert_publisher_also_emits_typed_entry_signal_for_buy_decision() -> None:
    recorder = Recorder()
    publisher = AlertEventPublisher(recorder)  # type: ignore[arg-type]
    alert = LocalAlert(
        symbol="AAPL",
        created_at=datetime(2026, 7, 24, 18, 0, tzinfo=UTC),
        severity=AlertSeverity.ACTION,
        title="AAPL SWING CONFIRMED",
        message="analytical entry only",
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analysis_ids=(new_uuid7(),),
        metrics=(NamedValue(name="current_price", value=Decimal("210")),),
        score=Decimal("80"),
        reasons=("swing_continuation_confirmed",),
        deduplication_key="aapl:entry:1",
        kind=AlertKind.ENTRY_CONFIRMED,
    )

    await publisher.publish(
        "alert.local.produced",
        "marketbot.v1.alert.local.ACTION.AAPL",
        alert,
    )

    assert len(recorder.items) == 2
    signal_subject, signal_envelope = recorder.items[1]
    assert signal_subject == "marketbot.v1.entry-signal.CORE_ENTRY.AAPL"
    assert signal_envelope.event_type == "entry-signal.confirmed"  # type: ignore[attr-defined]
    assert isinstance(signal_envelope.payload, EntrySignal)  # type: ignore[attr-defined]


@pytest.mark.unit
async def test_real_swing_continuation_alert_publishes_canonical_l2_signal() -> None:
    now = datetime(2026, 8, 10, 19, 50, tzinfo=UTC)
    engine = AlertEngineV3()
    engine.ingest(_analysis(AnalysisHorizon.SWING, as_of=now), now=now)
    assert engine.ingest(_analysis(AnalysisHorizon.INTRADAY, as_of=now), now=now) is None
    confirmed = engine.ingest(
        _analysis(AnalysisHorizon.INTRADAY, as_of=now + timedelta(minutes=3)),
        now=now + timedelta(minutes=3),
    )
    assert confirmed is not None

    recorder = Recorder()
    await AlertEventPublisher(recorder).publish(  # type: ignore[arg-type]
        "alert.local.produced",
        "marketbot.v1.alert.local.ACTION.ABNB",
        confirmed,
    )

    assert len(recorder.items) == 2
    signal = recorder.items[1][1].payload  # type: ignore[attr-defined]
    assert isinstance(signal, EntrySignal)
    assert signal.maturity is EntryMaturityLevel.L2
    assert signal.entry_price == Decimal("153.24")
    assert signal.zone_low == Decimal("149.5873")
    assert signal.zone_high == Decimal("153.6309")
    assert signal.invalidation == Decimal("149.3350")
    assert signal.targets == (Decimal("158.6200"),)


def _analysis(horizon: AnalysisHorizon, *, as_of: datetime) -> AnalysisResult:
    metrics = (
        (
            NamedValue(name="reference_price", value=Decimal("152.43")),
            NamedValue(name="classification", value="pullback"),
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="entry_zone_low", value=Decimal("149.5873")),
            NamedValue(name="entry_zone_high", value=Decimal("153.6309")),
            NamedValue(name="invalidation", value=Decimal("149.3350")),
            NamedValue(name="target_2r", value=Decimal("158.6200")),
            NamedValue(name="entry_confirmation_rule_version", value="1.0.0"),
        )
        if horizon is AnalysisHorizon.SWING
        else (
            NamedValue(name="reference_price", value=Decimal("153.24")),
            NamedValue(name="setup", value="bullish_breakout"),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="invalidation_level", value=Decimal("152.8569")),
            NamedValue(name="objective_level", value=Decimal("153.8147")),
        )
    )
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="ABNB",
        horizon=horizon,
        as_of=as_of,
        verdict=(
            AnalysisVerdict.FAVORABLE
            if horizon is AnalysisHorizon.SWING
            else AnalysisVerdict.WATCH
        ),
        direction=PatternDirection.BULLISH,
        score=Decimal("100") if horizon is AnalysisHorizon.SWING else Decimal("64"),
        confidence=Decimal("1") if horizon is AnalysisHorizon.SWING else Decimal("0.64"),
        reasons=("fixture",),
        metrics=metrics,
        context_hash="sha256:" + "a" * 64,
    )
