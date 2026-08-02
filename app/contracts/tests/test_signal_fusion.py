from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import (
    FusionAssessment,
    FusionState,
    fusion_assessment_subject,
    fusion_buy_confirmed_subject,
    fusion_transition_subject,
)

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def test_signal_fusion_subjects_are_independent() -> None:
    assert fusion_assessment_subject("BRK.B") == (
        "marketbot.v1.signal-fusion.assessment.BRK_B"
    )
    assert fusion_transition_subject(FusionState.ARMED, "BRK.B") == (
        "marketbot.v1.signal-fusion.transition.ARMED.BRK_B"
    )
    assert fusion_buy_confirmed_subject("BRK.B") == (
        "marketbot.v1.signal-fusion.buy-confirmed.BRK_B"
    )


def test_buy_confirmed_requires_every_execution_gate_and_trade_level() -> None:
    with pytest.raises(ValueError, match="all gates"):
        FusionAssessment(
            symbol="TGT",
            occurred_at=NOW,
            engine_version="0.1.0",
            state=FusionState.BUY_CONFIRMED,
            score=Decimal("90"),
            confidence=Decimal("0.9"),
            current_price=Decimal("105"),
            support_gate=True,
            trend_gate=True,
            timing_gate=True,
            execution_gate=False,
            dilution_gate=True,
            portfolio_gate=True,
            reward_risk_gate=True,
            reasons=("test",),
            context_hash=f"sha256:{'1' * 64}",
        )
