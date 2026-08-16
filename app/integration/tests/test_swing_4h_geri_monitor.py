from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    GeriStructuralLevel,
)
from app.integration.swing_4h_geri_monitor import _format_assessment


def test_monitor_distinguishes_structural_level_number_from_maturity() -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    levels = (
        GeriStructuralLevel(
            sequence=1,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("100"),
            source_at=now,
            confirmed_at=now,
            broken_at=now + timedelta(hours=4),
        ),
        GeriStructuralLevel(
            sequence=2,
            kind=GeriLevelKind.RESISTANCE,
            price=Decimal("110"),
            source_at=now,
            confirmed_at=now + timedelta(hours=4),
            broken_at=now + timedelta(hours=8),
        ),
        GeriStructuralLevel(
            sequence=3,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("95"),
            source_at=now + timedelta(hours=4),
            confirmed_at=now + timedelta(hours=8),
        ),
    )
    item = GeriAssessment(
        symbol="PFE",
        occurred_at=now + timedelta(hours=8),
        engine_version="1.0.0",
        maturity=GeriMaturity.IN_ZONE_4H,
        current_price=Decimal("95.1"),
        levels=levels,
        active_level_sequence=3,
        active_level_kind=GeriLevelKind.SUPPORT,
        active_level_price=Decimal("95"),
        atr14=Decimal("2"),
        breakout_buffer=Decimal("0.2"),
        zone_low=Decimal("94.5"),
        zone_high=Decimal("95.5"),
        invalidation=Decimal("94"),
        reasons=("horizontal_support_retest",),
        context_hash=f"sha256:{'a' * 64}",
    )

    rendered = _format_assessment(item, color=False)

    assert "4HGERI IN_ZONE_4H | N3 SUPPORT 95" in rendered
    assert "N1 SUPPORT 100 -> N2 RESISTANCE 110 -> N3 SUPPORT 95" in rendered
