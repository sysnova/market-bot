from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, WaveAssessment, WavePhase

NOW = datetime(2026, 8, 1, 20, tzinfo=UTC)


def test_wave_assessment_separates_data_time_from_assessment_time() -> None:
    assessed_at = NOW + timedelta(hours=2)

    item = WaveAssessment(
        symbol="TGT",
        occurred_at=NOW,
        data_as_of=NOW,
        assessed_at=assessed_at,
        engine_version="0.1.0",
        primary_timeframe=BarTimeframe.DAY_1,
        phase=WavePhase.UNRESOLVED,
        score=Decimal("20"),
        confidence=Decimal("0.2"),
        current_price=Decimal("105"),
        reasons=("candidate",),
        context_hash="sha256:" + "b" * 64,
    )

    assert item.data_as_of == NOW
    assert item.assessed_at == assessed_at


def test_wave_assessment_rejects_assessment_before_its_data() -> None:
    with pytest.raises(ValueError, match="assessed_at"):
        WaveAssessment(
            symbol="TGT",
            occurred_at=NOW,
            data_as_of=NOW,
            assessed_at=NOW - timedelta(minutes=1),
            engine_version="0.1.0",
            primary_timeframe=BarTimeframe.DAY_1,
            phase=WavePhase.UNRESOLVED,
            score=Decimal("20"),
            confidence=Decimal("0.2"),
            current_price=Decimal("105"),
            reasons=("candidate",),
            context_hash="sha256:" + "c" * 64,
        )


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
