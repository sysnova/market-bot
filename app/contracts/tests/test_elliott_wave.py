from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, WaveAssessment, WavePhase

NOW = datetime(2026, 8, 1, 20, tzinfo=UTC)


def test_wave_assessment_requires_trade_levels_for_an_actionable_phase() -> None:
    with pytest.raises(ValueError, match="levels"):
        WaveAssessment(
            symbol="TGT",
            occurred_at=NOW,
            engine_version="0.1.0",
            primary_timeframe=BarTimeframe.DAY_1,
            phase=WavePhase.WAVE_2_ENDING,
            score=Decimal("75"),
            confidence=Decimal("0.75"),
            current_price=Decimal("105"),
            reasons=("candidate",),
            context_hash="sha256:" + "a" * 64,
        )
