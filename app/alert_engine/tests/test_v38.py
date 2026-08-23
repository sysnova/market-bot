from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV38
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryMaturityLevel,
    EntrySignalFamily,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "8" * 64


def test_v38_promotes_one_recovery_setup_to_a_deduplicated_l2_buy() -> None:
    engine = AlertEngineV38()
    alert = engine.ingest(_swing(NOW, anchor="2026-08-19T04:00:00Z"), now=NOW)
    duplicate = engine.ingest(
        _intraday(NOW + timedelta(minutes=3)),
        now=NOW + timedelta(minutes=3),
    )

    assert alert is not None
    assert alert.kind is AlertKind.ENTRY_CONFIRMED
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["entry_signal_family"] == EntrySignalFamily.CORE_RECOVERY.value
    assert metrics["entry_maturity"] == EntryMaturityLevel.L2.value
    assert metrics["entry_setup_id"].endswith("2026-08-19T04:00:00Z")
    assert metrics["buy_zone_low"] == Decimal("57")
    assert metrics["buy_zone_high"] == Decimal("60")
    assert metrics["invalidation"] == Decimal("52")
    assert "swing_recovery_l2_confirmed" in alert.reasons
    assert duplicate is None


def test_v38_allows_a_new_buy_after_a_fresh_correction_anchor() -> None:
    engine = AlertEngineV38()
    first = engine.ingest(_swing(NOW, anchor="2026-08-19T04:00:00Z"), now=NOW)
    later = NOW + timedelta(minutes=20)
    second = engine.ingest(
        _swing(later, anchor="2026-08-20T04:00:00Z"),
        now=later,
    )

    assert first is not None
    assert second is not None
    assert first.deduplication_key != second.deduplication_key


def test_v38_promotes_recovery_after_event_bus_decimal_serialization() -> None:
    engine = AlertEngineV38()
    serialized = AnalysisResult.model_validate(
        _swing(NOW, anchor="2026-08-19T04:00:00Z").model_dump(mode="json"),
        strict=False,
    )

    alert = engine.ingest(serialized, now=NOW)

    assert alert is not None
    metrics = {item.name: item.value for item in alert.metrics}
    assert alert.kind is AlertKind.ENTRY_CONFIRMED
    assert metrics["entry_signal_family"] == EntrySignalFamily.CORE_RECOVERY.value
    assert metrics["entry_maturity"] == EntryMaturityLevel.L2.value
    assert metrics["entry_price"] == Decimal("60")
    assert metrics["invalidation"] == Decimal("52")


def test_v38_prioritizes_recovery_family_over_a_generic_swing_alert() -> None:
    engine = AlertEngineV38()
    swing = _swing(NOW, anchor="2026-08-19T04:00:00Z").model_copy(
        update={"score": Decimal("90"), "confidence": Decimal("0.90")}
    )

    alert = engine.ingest(swing, now=NOW)

    assert alert is not None
    metrics = {item.name: item.value for item in alert.metrics}
    assert alert.kind is AlertKind.ENTRY_CONFIRMED
    assert metrics["entry_signal_family"] == EntrySignalFamily.CORE_RECOVERY.value


def _swing(as_of: datetime, *, anchor: str) -> AnalysisResult:
    return AnalysisResult(
        engine_id="swing",
        engine_version="10.0.0",
        symbol="ASTS",
        horizon=AnalysisHorizon.SWING,
        as_of=as_of,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("65"),
        confidence=Decimal("0.65"),
        reasons=("structure_recovery_confirmed",),
        metrics=(
            NamedValue(name="reference_price", value=Decimal("60")),
            NamedValue(name="classification", value="recovery"),
            NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
            NamedValue(name="recovery_entry_gate_passed", value=True),
            NamedValue(name="swing_entry_gate_passed", value=True),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("3")),
            NamedValue(name="recovery_setup_id", value=f"swing-recovery:ASTS:{anchor}"),
            NamedValue(name="recovery_avwap_anchor_at", value=anchor),
            NamedValue(name="recovery_avwap", value=Decimal("57")),
            NamedValue(name="entry_zone_low", value=Decimal("55")),
            NamedValue(name="entry_zone_high", value=Decimal("58")),
            NamedValue(name="invalidation", value=Decimal("52")),
            NamedValue(name="target_2r", value=Decimal("76")),
            NamedValue(name="entry_confirmation_rule_version", value="3.2.0"),
        ),
        context_hash=HASH,
    )


def _intraday(as_of: datetime) -> AnalysisResult:
    return AnalysisResult(
        engine_id="intraday",
        engine_version="4.0.0",
        symbol="ASTS",
        horizon=AnalysisHorizon.INTRADAY,
        as_of=as_of,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("80"),
        confidence=Decimal("0.80"),
        reasons=("setup:bullish_breakout",),
        metrics=(
            NamedValue(name="setup", value="bullish_breakout"),
            NamedValue(name="reference_price", value=Decimal("60")),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
        ),
        context_hash=HASH,
    )
