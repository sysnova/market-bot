from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import SwingTradeAssessment
from app.swing_trade_engine.tests.test_engine import analyze


@pytest.mark.parametrize(
    "stop,low,high",
    [("104.182", "103.3451", "108.805"), ("105", "105", "108"), ("104", "108", "105")],
)
def test_actionable_zone_rejects_invalid_stop_or_reversed_bounds(
    stop: str, low: str, high: str
) -> None:
    payload = analyze("97").model_dump()
    payload.update(
        entry_invalidation=Decimal(stop), entry_zone_low=Decimal(low), entry_zone_high=Decimal(high)
    )
    with pytest.raises(ValidationError):
        SwingTradeAssessment.model_validate(payload)


def test_legacy_fibonacci_only_assessment_remains_readable() -> None:
    item = analyze("97")
    assert SwingTradeAssessment.model_validate(item.model_dump()) == item


def test_new_signal_policy_rejects_legacy_zone_crossing_stop() -> None:
    from app.contracts import EntrySignal, SwingTradeMaturity
    from app.entry_opportunity_engine.tests.test_swing_trade_v4 import swing_signal

    legacy = swing_signal(SwingTradeMaturity.ST2, invalidation="96")
    payload = legacy.model_dump()
    payload["policy_version"] = "1.7.0"
    with pytest.raises(ValidationError, match="above invalidation"):
        EntrySignal.model_validate(payload)
