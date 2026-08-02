from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import (
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    support_assessment_subject,
    support_transition_subject,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


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
