from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    GeriStructuralLevel,
    NamedValue,
    TradeSide,
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


def test_monitor_marks_v12_as_manual_only() -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    levels = (
        GeriStructuralLevel(
            sequence=1,
            kind=GeriLevelKind.RESISTANCE,
            price=Decimal("110"),
            source_at=now,
            confirmed_at=now,
            broken_at=now + timedelta(hours=4),
        ),
        GeriStructuralLevel(
            sequence=2,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("94"),
            source_at=now,
            confirmed_at=now + timedelta(hours=4),
            broken_at=now + timedelta(hours=8),
        ),
        GeriStructuralLevel(
            sequence=3,
            kind=GeriLevelKind.RESISTANCE,
            price=Decimal("112"),
            source_at=now + timedelta(hours=4),
            confirmed_at=now + timedelta(hours=8),
        ),
    )
    item = GeriAssessment(
        symbol="PFE",
        occurred_at=now + timedelta(hours=8),
        engine_version="1.2.0",
        maturity=GeriMaturity.ARMED,
        current_price=Decimal("104"),
        levels=levels,
        active_level_sequence=3,
        active_level_kind=GeriLevelKind.RESISTANCE,
        active_level_price=Decimal("112"),
        atr14=Decimal("4"),
        breakout_buffer=Decimal("0.4"),
        zone_low=Decimal("111"),
        zone_high=Decimal("113"),
        invalidation=Decimal("114"),
        trade_side=TradeSide.SHORT,
        standalone_swing=True,
        reasons=("manual_monitor_only",),
        context_hash=f"sha256:{'b' * 64}",
    )

    rendered = _format_assessment(item, color=False)

    assert "4HGERI v1.2.0 | G0 ARMED | SHORT" in rendered
    assert "MONITOR MANUAL | NO COMPRA | NO OPPORTUNITY" in rendered


def test_monitor_prints_v13_countertrend_lane_separately() -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    item = GeriAssessment(
        symbol="PFE",
        occurred_at=now,
        engine_version="1.3.0",
        maturity=GeriMaturity.EXTENDED,
        current_price=Decimal("95"),
        levels=(
            GeriStructuralLevel(
                sequence=1,
                kind=GeriLevelKind.RESISTANCE,
                price=Decimal("112"),
                source_at=now - timedelta(days=4),
                confirmed_at=now - timedelta(days=3),
            ),
        ),
        active_level_sequence=1,
        active_level_kind=GeriLevelKind.RESISTANCE,
        active_level_price=Decimal("112"),
        atr14=Decimal("4"),
        breakout_buffer=Decimal("0.4"),
        zone_low=Decimal("111"),
        zone_high=Decimal("113"),
        invalidation=Decimal("114"),
        trade_side=TradeSide.SHORT,
        standalone_swing=True,
        reasons=("manual_monitor_only",),
        metrics=(
            NamedValue(name="countertrend_side", value=TradeSide.LONG),
            NamedValue(name="countertrend_state", value=GeriMaturity.ARMED),
            NamedValue(name="countertrend_level_price", value=Decimal("83.68")),
            NamedValue(name="countertrend_zone_low", value=Decimal("82.68")),
            NamedValue(name="countertrend_zone_high", value=Decimal("84.68")),
            NamedValue(name="countertrend_invalidation", value=Decimal("81.68")),
            NamedValue(name="countertrend_target", value=Decimal("100")),
            NamedValue(name="countertrend_reward_risk", value=Decimal("2.5")),
        ),
        context_hash=f"sha256:{'c' * 64}",
    )

    rendered = _format_assessment(item, color=False)

    assert "TACTICAL COUNTERTREND LONG | ARMED" in rendered
    assert "LEVEL 83.68 | ZONE 82.68-84.68 | INV 81.68 | TARGET 100 | R:R 2.5" in rendered
    assert "NO OPPORTUNITY | NO ORDEN" in rendered
