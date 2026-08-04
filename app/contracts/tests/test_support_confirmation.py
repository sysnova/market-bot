from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    StructuralSupportReference,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    support_assessment_subject,
    support_transition_subject,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_support_assessment_separates_data_time_from_assessment_time() -> None:
    assessed_at = NOW + timedelta(hours=3)

    item = SupportAssessment(
        symbol="TGT",
        occurred_at=NOW,
        data_as_of=NOW,
        assessed_at=assessed_at,
        engine_version="0.1.0",
        state=SupportState.NO_KEY_SUPPORT,
        current_price=Decimal("105"),
        support_score=Decimal("10"),
        reaction_score=Decimal("0"),
        reversal_score=Decimal("0"),
        confidence=Decimal("0.1"),
        reasons=("no_support",),
        context_hash="sha256:" + "b" * 64,
    )

    assert item.data_as_of == NOW
    assert item.assessed_at == assessed_at


def test_no_nearby_support_preserves_structural_map_and_impulse_origin() -> None:
    item = SupportAssessment(
        symbol="MSFT",
        occurred_at=NOW,
        engine_version="0.1.0",
        state=SupportState.NO_NEARBY_SUPPORT,
        current_price=Decimal("487.65"),
        support_score=Decimal("0"),
        reaction_score=Decimal("0"),
        reversal_score=Decimal("0"),
        confidence=Decimal("0"),
        structural_supports=(
            StructuralSupportReference(
                source="weekly_sma50",
                price=Decimal("445.39"),
                distance_percent=Decimal("8.67"),
                distance_atr=Decimal("2.42"),
            ),
            StructuralSupportReference(
                source="weekly_sma200",
                price=Decimal("391.03"),
                distance_percent=Decimal("19.81"),
                distance_atr=Decimal("5.53"),
            ),
        ),
        impulse_origin=Decimal("377.39"),
        impulse_origin_at=datetime(2026, 7, 23, tzinfo=UTC),
        impulse_peak=Decimal("491.65"),
        impulse_advance_percent=Decimal("30.28"),
        reasons=("no_nearby_higher_timeframe_support",),
        context_hash="sha256:" + "d" * 64,
    )

    assert item.zone_low is None
    assert item.structural_supports[1].source == "weekly_sma200"
    assert item.impulse_advance_percent == Decimal("30.28")


def test_support_assessment_rejects_assessment_before_its_data() -> None:
    with pytest.raises(ValueError, match="assessed_at"):
        SupportAssessment(
            symbol="TGT",
            occurred_at=NOW,
            data_as_of=NOW,
            assessed_at=NOW - timedelta(minutes=1),
            engine_version="0.1.0",
            state=SupportState.NO_KEY_SUPPORT,
            current_price=Decimal("105"),
            support_score=Decimal("10"),
            reaction_score=Decimal("0"),
            reversal_score=Decimal("0"),
            confidence=Decimal("0.1"),
            reasons=("no_support",),
            context_hash="sha256:" + "c" * 64,
        )


def test_support_subjects_are_independent_from_analysis_and_patreon() -> None:
    assert support_assessment_subject("BRK.B") == (
        "marketbot.v1.support-confirmation.assessment.BRK_B"
    )
    assert support_transition_subject(SupportState.RECLAIMED, "BRK.B") == (
        "marketbot.v1.support-confirmation.transition.RECLAIMED.BRK_B"
    )


def test_key_support_states_require_a_complete_frozen_zone() -> None:
    with pytest.raises(ValueError, match="zone"):
        SupportAssessment(
            symbol="TGT",
            occurred_at=NOW,
            engine_version="0.1.0",
            state=SupportState.REACTION_CONFIRMED,
            confirmation_type=SupportConfirmationType.V_RECOVERY,
            current_price=Decimal("105"),
            support_score=Decimal("80"),
            reaction_score=Decimal("75"),
            reversal_score=Decimal("20"),
            confidence=Decimal("0.75"),
            b_wave_risk=True,
            reasons=("reaction_only",),
            context_hash="sha256:" + "a" * 64,
        )
