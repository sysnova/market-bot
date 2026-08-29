from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import SwingChannelAssessment, SwingChannelMaturity

NOW = datetime(2026, 8, 14, 17, 30, tzinfo=UTC)


def assessment(**updates: object) -> SwingChannelAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW,
        "engine_version": "1.0.0",
        "maturity": SwingChannelMaturity.ARMED,
        "current_price": Decimal("105"),
        "pivot_a_at": datetime(2026, 8, 5, 13, 30, tzinfo=UTC),
        "pivot_a_price": Decimal("95"),
        "pivot_b_at": datetime(2026, 8, 8, 13, 30, tzinfo=UTC),
        "pivot_b_price": Decimal("98"),
        "pivot_c_at": datetime(2026, 8, 12, 13, 30, tzinfo=UTC),
        "pivot_c_price": Decimal("110"),
        "support": Decimal("100"),
        "middle": Decimal("105"),
        "resistance": Decimal("110"),
        "zone_low": Decimal("99.5"),
        "zone_high": Decimal("100.5"),
        "invalidation": Decimal("99"),
        "slope_per_bar": Decimal("0.5"),
        "width": Decimal("10"),
        "width_atr": Decimal("4"),
        "distance_to_support_atr": Decimal("2"),
        "containment_ratio": Decimal("0.85"),
        "support_touch_count": 2,
        "reasons": ("ascending_channel_armed",),
        "context_hash": "sha256:" + "a" * 64,
    }
    values.update(updates)
    return SwingChannelAssessment(**values)  # type: ignore[arg-type]


def test_swing_channel_assessment_keeps_ordered_geometry() -> None:
    item = assessment()

    assert item.invalidation < item.zone_low <= item.support <= item.zone_high
    assert item.support < item.middle < item.resistance


def test_swing_channel_assessment_accepts_impulse_peak_between_supports() -> None:
    item = assessment(
        pivot_b_at=datetime(2026, 8, 12, 13, 30, tzinfo=UTC),
        pivot_c_at=datetime(2026, 8, 8, 13, 30, tzinfo=UTC),
    )

    assert item.pivot_a_at < item.pivot_c_at < item.pivot_b_at


def test_swing_channel_assessment_rejects_non_ascending_pivots() -> None:
    with pytest.raises(ValidationError, match="higher than pivot A"):
        assessment(pivot_b_price=Decimal("94"))


def test_l3_requires_daily_swing_alignment() -> None:
    with pytest.raises(ValidationError, match="daily Swing alignment"):
        assessment(maturity=SwingChannelMaturity.L3, bounce_confirmed=True)


def test_l4_requires_existing_maturity_alignment() -> None:
    with pytest.raises(ValidationError, match="existing L3/L4 alignment"):
        assessment(
            maturity=SwingChannelMaturity.L4,
            bounce_confirmed=True,
            daily_swing_aligned=True,
        )
