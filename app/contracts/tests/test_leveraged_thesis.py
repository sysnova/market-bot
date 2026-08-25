from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts.enums import PatternDirection
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisState,
)
from app.contracts.order_flow import OrderFlowStateKind

NOW = datetime(2026, 8, 24, 15, tzinfo=UTC)


def test_confirmed_thesis_requires_complete_executable_evidence() -> None:
    assessment = LeveragedThesisAssessment(
        underlying_symbol="ASTS",
        instrument_symbol="ASTN",
        occurred_at=NOW,
        expires_at=NOW + timedelta(minutes=3),
        engine_version="1.0.0",
        state=LeveragedThesisState.BUY_CONFIRMED,
        direction=PatternDirection.BEARISH,
        exposure=LeveragedExposure.INVERSE_2X,
        underlying_price=Decimal("62.31"),
        instrument_bid=Decimal("4.91"),
        instrument_ask=Decimal("4.92"),
        spread_bps=Decimal("20.346"),
        underlying_flow_state=OrderFlowStateKind.SELL_PRESSURE,
        underlying_flow_confidence=Decimal("0.81"),
        instrument_flow_state=OrderFlowStateKind.BUY_PRESSURE,
        instrument_flow_confidence=Decimal("0.73"),
        structure_score=Decimal("78"),
        reasons=("bearish_structure_confirmed",),
        context_hash="sha256:" + "a" * 64,
    )

    assert assessment.instrument_symbol == "ASTN"
    assert assessment.exposure is LeveragedExposure.INVERSE_2X


def test_actionable_state_rejects_neutral_direction_or_missing_quote() -> None:
    base = {
        "underlying_symbol": "ASTS",
        "instrument_symbol": "ASTN",
        "occurred_at": NOW,
        "expires_at": NOW + timedelta(minutes=3),
        "engine_version": "1.0.0",
        "state": LeveragedThesisState.BUY_CONFIRMED,
        "direction": PatternDirection.BEARISH,
        "exposure": LeveragedExposure.INVERSE_2X,
        "underlying_price": Decimal("62.31"),
        "instrument_bid": Decimal("4.91"),
        "instrument_ask": Decimal("4.92"),
        "spread_bps": Decimal("20.346"),
        "underlying_flow_state": OrderFlowStateKind.SELL_PRESSURE,
        "underlying_flow_confidence": Decimal("0.81"),
        "instrument_flow_state": OrderFlowStateKind.BUY_PRESSURE,
        "instrument_flow_confidence": Decimal("0.73"),
        "structure_score": Decimal("78"),
        "reasons": ("bearish_structure_confirmed",),
        "context_hash": "sha256:" + "a" * 64,
    }

    with pytest.raises(ValueError, match="directional"):
        LeveragedThesisAssessment(**(base | {"direction": PatternDirection.NEUTRAL}))
    with pytest.raises(ValueError, match="quote"):
        LeveragedThesisAssessment(**(base | {"instrument_ask": None}))
