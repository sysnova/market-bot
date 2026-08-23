from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.scalp import (
    ScalpAssessment,
    ScalpDirection,
    ScalpSetup,
    ScalpState,
    ScalpTransition,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _assessment(**changes: object) -> ScalpAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW,
        "engine_version": "1.0.0",
        "state": ScalpState.ARMED,
        "setup": ScalpSetup.SUPPORT_REVERSAL,
        "direction": ScalpDirection.LONG,
        "current_price": Decimal("101"),
        "bid_price": Decimal("100.99"),
        "ask_price": Decimal("101.01"),
        "session_vwap": Decimal("101.50"),
        "spread_bps": Decimal("1.9802"),
        "order_flow_confidence": Decimal("0.78"),
        "entry_price": Decimal("101.01"),
        "invalidation": Decimal("99.75"),
        "target": Decimal("102.90"),
        "max_hold_seconds": 900,
        "support_low": Decimal("100"),
        "support_high": Decimal("101.20"),
        "reasons": ("support_reversal_armed",),
        "context_hash": HASH,
    }
    values.update(changes)
    return ScalpAssessment.model_validate(values)


def test_scalp_assessment_is_frozen_and_uses_decimal_levels() -> None:
    assessment = _assessment()

    assert assessment.entry_price == Decimal("101.01")
    assert assessment.assessment_id.version == 7
    with pytest.raises(ValidationError):
        assessment.current_price = Decimal("102")


def test_non_watching_assessment_requires_ordered_trade_levels() -> None:
    with pytest.raises(ValidationError, match="invalidation < entry_price < target"):
        _assessment(target=Decimal("100.50"))


def test_watching_assessment_must_not_claim_an_entry_setup() -> None:
    with pytest.raises(ValidationError, match="watching assessment"):
        _assessment(
            state=ScalpState.WATCHING,
            setup=ScalpSetup.SUPPORT_REVERSAL,
            direction=ScalpDirection.LONG,
            entry_price=None,
            invalidation=None,
            target=None,
            max_hold_seconds=None,
        )


def test_scalp_assessment_requires_utc() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        _assessment(occurred_at=NOW.replace(tzinfo=None))


def test_scalp_transition_must_change_state() -> None:
    assessment = _assessment()

    with pytest.raises(ValidationError, match="must change state"):
        ScalpTransition(
            assessment_id=assessment.assessment_id,
            symbol=assessment.symbol,
            occurred_at=NOW,
            engine_version="1.0.0",
            previous_state=ScalpState.ARMED,
            state=ScalpState.ARMED,
            setup=assessment.setup,
            direction=assessment.direction,
            reference_price=assessment.current_price,
            reasons=("duplicate_state",),
            context_hash=HASH,
        )
