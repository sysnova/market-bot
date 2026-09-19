from datetime import timedelta
from decimal import Decimal

from app.alert_engine.tests.test_v39 import NOW, _intraday, _swing
from app.alert_engine.v311 import AlertEngineV311
from app.contracts import AlertSeverity, AnalysisResult, NamedValue


def _swing_with_support(
    *, support: str = "57.27", atr: str = "3.7142"
) -> AnalysisResult:
    swing = _swing()
    return swing.model_copy(
        update={
            "metrics": (
                *swing.metrics,
                NamedValue(name="structural_support", value=Decimal(support)),
                NamedValue(name="atr14", value=Decimal(atr)),
            )
        }
    )


def _intraday_at(entry: str) -> AnalysisResult:
    value = Decimal(entry)
    result = _intraday()
    replacements = {
        "reference_price": value,
        "invalidation_level": value + Decimal("0.50"),
        "objective_level": value - Decimal("0.75"),
    }
    return result.model_copy(
        update={
            "as_of": NOW + timedelta(minutes=1),
            "metrics": tuple(
                item.model_copy(update={"value": replacements[item.name]})
                if item.name in replacements
                else item
                for item in result.metrics
            ),
        }
    )


def test_short_is_blocked_inside_structural_support_bounce_zone() -> None:
    engine = AlertEngineV311(short_symbols=("ASTS",))
    assert engine.ingest(_swing_with_support(), now=NOW) is None

    alert = engine.ingest(_intraday_at("57.935"), now=NOW + timedelta(minutes=1))

    assert alert is not None
    assert alert.title == "ASTS SHORT BLOCKED - SUPPORT"
    assert alert.severity is AlertSeverity.WATCH
    assert "short_entry_confirmed" not in alert.reasons
    assert "short_confirmation_blocked_near_support" in alert.reasons
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["short_support_guard_passed"] is False
    assert metrics["short_support_distance_atr"] == Decimal("0.1790")


def test_short_is_allowed_with_room_above_support() -> None:
    engine = AlertEngineV311(short_symbols=("ASTS",))
    engine.ingest(_swing_with_support(support="54", atr="3"), now=NOW)

    alert = engine.ingest(_intraday_at("58"), now=NOW + timedelta(minutes=1))

    assert alert is not None
    assert alert.title == "ASTS SHORT CONFIRMED"
    assert "short_entry_confirmed" in alert.reasons
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["short_support_guard_passed"] is True


def test_short_is_allowed_after_decisive_support_break() -> None:
    engine = AlertEngineV311(short_symbols=("ASTS",))
    engine.ingest(_swing_with_support(support="59", atr="3"), now=NOW)

    alert = engine.ingest(_intraday_at("58"), now=NOW + timedelta(minutes=1))

    assert alert is not None
    assert alert.title == "ASTS SHORT CONFIRMED"
    assert "short_entry_confirmed" in alert.reasons


def test_short_fails_closed_when_support_data_is_missing() -> None:
    engine = AlertEngineV311(short_symbols=("ASTS",))
    engine.ingest(_swing(), now=NOW)

    alert = engine.ingest(_intraday_at("58"), now=NOW + timedelta(minutes=1))

    assert alert is not None
    assert alert.title == "ASTS SHORT BLOCKED - SUPPORT DATA"
    assert "short_entry_confirmed" not in alert.reasons
    assert "short_support_data_missing" in alert.reasons
