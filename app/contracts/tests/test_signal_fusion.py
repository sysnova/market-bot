from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import (
    FusionAssessment,
    FusionState,
    fusion_assessment_subject,
    fusion_buy_confirmed_subject,
    fusion_recovery_confirmed_subject,
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
    assert fusion_recovery_confirmed_subject("BRK.B") == (
        "marketbot.v1.signal-fusion.recovery-confirmed.BRK_B"
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


def test_recovery_confirmed_allows_pending_long_and_support_structure() -> None:
    item = FusionAssessment(
        symbol="TGT",
        occurred_at=NOW,
        engine_version="0.3.0",
        state=FusionState.RECOVERY_CONFIRMED,
        score=Decimal("75"),
        confidence=Decimal("0.75"),
        current_price=Decimal("105"),
        support_zone_gate=True,
        support_reaction_gate=True,
        support_gate=False,
        trend_gate=False,
        timing_gate=True,
        execution_gate=True,
        dilution_gate=True,
        portfolio_gate=True,
        reward_risk_gate=True,
        recovery_gate=True,
        trigger_price=Decimal("103"),
        entry_price=Decimal("105"),
        invalidation=Decimal("100"),
        target_price=Decimal("120"),
        reward_risk_ratio=Decimal("3"),
        reasons=("test",),
        context_hash=f"sha256:{'2' * 64}",
    )

    assert item.state is FusionState.RECOVERY_CONFIRMED


def test_recovery_confirmed_requires_its_explicit_gate() -> None:
    with pytest.raises(ValueError, match="recovery gates"):
        FusionAssessment(
            symbol="TGT",
            occurred_at=NOW,
            engine_version="0.3.0",
            state=FusionState.RECOVERY_CONFIRMED,
            score=Decimal("75"),
            confidence=Decimal("0.75"),
            current_price=Decimal("105"),
            support_zone_gate=True,
            support_reaction_gate=True,
            timing_gate=True,
            execution_gate=True,
            dilution_gate=True,
            portfolio_gate=True,
            reward_risk_gate=True,
            recovery_gate=False,
            trigger_price=Decimal("103"),
            entry_price=Decimal("105"),
            invalidation=Decimal("100"),
            target_price=Decimal("120"),
            reward_risk_ratio=Decimal("3"),
            reasons=("test",),
            context_hash=f"sha256:{'3' * 64}",
        )
