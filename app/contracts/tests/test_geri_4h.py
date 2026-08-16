from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.enums import GeriLevelKind, GeriMaturity
from app.contracts.geri_4h import GeriAssessment, GeriStructuralLevel

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def levels() -> tuple[GeriStructuralLevel, ...]:
    return (
        GeriStructuralLevel(
            sequence=1,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("100"),
            source_at=NOW,
            confirmed_at=NOW + timedelta(hours=4),
            broken_at=NOW + timedelta(hours=12),
        ),
        GeriStructuralLevel(
            sequence=2,
            kind=GeriLevelKind.RESISTANCE,
            price=Decimal("110"),
            source_at=NOW + timedelta(hours=4),
            confirmed_at=NOW + timedelta(hours=12),
            broken_at=NOW + timedelta(hours=20),
        ),
        GeriStructuralLevel(
            sequence=3,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("95"),
            source_at=NOW + timedelta(hours=16),
            confirmed_at=NOW + timedelta(hours=20),
        ),
    )


def test_contract_accepts_alternating_horizontal_levels_and_support_zone() -> None:
    assessment = GeriAssessment(
        symbol="PFE",
        occurred_at=NOW + timedelta(hours=24),
        assessed_at=NOW + timedelta(hours=24),
        engine_version="1.0.0",
        maturity=GeriMaturity.ARMED,
        current_price=Decimal("105"),
        levels=levels(),
        active_level_sequence=3,
        active_level_kind=GeriLevelKind.SUPPORT,
        active_level_price=Decimal("95"),
        atr14=Decimal("2"),
        breakout_buffer=Decimal("0.2"),
        zone_low=Decimal("94.5"),
        zone_high=Decimal("95.5"),
        invalidation=Decimal("94"),
        reasons=("support_level_armed",),
        context_hash=f"sha256:{'a' * 64}",
    )

    assert assessment.levels[-1].sequence == 3


def test_contract_rejects_two_consecutive_resistance_levels() -> None:
    malformed = (
        *levels()[:2],
        levels()[2].model_copy(update={"kind": GeriLevelKind.RESISTANCE}),
    )

    with pytest.raises(ValidationError, match="alternate"):
        GeriAssessment(
            symbol="PFE",
            occurred_at=NOW + timedelta(hours=24),
            engine_version="1.0.0",
            maturity=GeriMaturity.BUILDING,
            current_price=Decimal("105"),
            levels=malformed,
            active_level_sequence=3,
            active_level_kind=GeriLevelKind.RESISTANCE,
            active_level_price=Decimal("95"),
            atr14=Decimal("2"),
            breakout_buffer=Decimal("0.2"),
            reasons=("tracking_resistance_break",),
            context_hash=f"sha256:{'b' * 64}",
        )


def test_active_resistance_cannot_publish_a_long_entry_zone() -> None:
    resistance = levels()[:2]
    active = resistance[-1].model_copy(update={"broken_at": None})

    with pytest.raises(ValidationError, match="resistance cannot expose"):
        GeriAssessment(
            symbol="PFE",
            occurred_at=NOW + timedelta(hours=16),
            engine_version="1.0.0",
            maturity=GeriMaturity.BUILDING,
            current_price=Decimal("100"),
            levels=(resistance[0], active),
            active_level_sequence=2,
            active_level_kind=GeriLevelKind.RESISTANCE,
            active_level_price=Decimal("110"),
            atr14=Decimal("2"),
            breakout_buffer=Decimal("0.2"),
            zone_low=Decimal("109"),
            zone_high=Decimal("111"),
            invalidation=Decimal("108"),
            reasons=("tracking_resistance_break",),
            context_hash=f"sha256:{'c' * 64}",
        )
