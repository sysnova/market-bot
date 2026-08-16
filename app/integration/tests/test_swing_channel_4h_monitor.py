from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import SwingChannelAssessment, SwingChannelMaturity
from app.integration.swing_channel_4h_monitor import _format_assessment


def test_monitor_explains_state_and_missing_confirmations_in_plain_language() -> None:
    now = datetime(2026, 8, 14, 19, 30, tzinfo=UTC)
    item = SwingChannelAssessment(
        symbol="PFE",
        occurred_at=now,
        assessed_at=now,
        engine_version="1.0.0",
        maturity=SwingChannelMaturity.IN_ZONE_4H,
        current_price=Decimal("24.12"),
        pivot_a_at=datetime(2026, 8, 10, tzinfo=UTC),
        pivot_a_price=Decimal("22"),
        pivot_b_at=datetime(2026, 8, 11, tzinfo=UTC),
        pivot_b_price=Decimal("23"),
        pivot_c_at=datetime(2026, 8, 12, tzinfo=UTC),
        pivot_c_price=Decimal("28"),
        support=Decimal("24"),
        middle=Decimal("26"),
        resistance=Decimal("28"),
        zone_low=Decimal("23.75"),
        zone_high=Decimal("24.25"),
        invalidation=Decimal("23.50"),
        slope_per_bar=Decimal("0.10"),
        width=Decimal("4"),
        width_atr=Decimal("2"),
        distance_to_support_atr=Decimal("0.06"),
        containment_ratio=Decimal("0.8"),
        support_touch_count=2,
        bounce_confirmed=False,
        daily_swing_aligned=False,
        existing_maturity_aligned=False,
        reasons=("projected_support_touched",),
        context_hash=f"sha256:{'a' * 64}",
    )

    rendered = _format_assessment(item, color=False)

    assert "PFE | IN_ZONE_4H | EN ZONA 4H" in rendered
    assert "Confirmaciones: rebote NO | Swing diario NO | Opportunity L3/L4 NO" in rendered
    assert "contencion 80.0%" in rendered
