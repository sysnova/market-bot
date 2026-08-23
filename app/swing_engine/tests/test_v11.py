import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.contracts import SupportAssessment, SupportConfirmationType, SupportState
from app.swing_engine import SwingEngineV10, SwingEngineV11
from app.swing_engine.models import SwingContext
from app.swing_engine.tests.test_engine import FIXTURES, _context


def _bullish_context() -> SwingContext:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
    return _context(case)


def _support(
    context: SwingContext,
    state: SupportState = SupportState.REACTION_CONFIRMED,
) -> SupportAssessment:
    return SupportAssessment(
        symbol=context.symbol,
        occurred_at=context.daily_bars[-1].timestamp,
        engine_version="0.2.0",
        state=state,
        confirmation_type=SupportConfirmationType.V_RECOVERY,
        current_price=context.price,
        zone_low=Decimal("84"),
        zone_center=Decimal("84.5"),
        zone_high=Decimal("85"),
        invalidation=Decimal("82"),
        support_score=Decimal("85"),
        reaction_score=Decimal("75"),
        reversal_score=Decimal("35"),
        confidence=Decimal("0.75"),
        support_sources=("pivot_daily_57", "weekly_sma10"),
        reasons=("fixture",),
        context_hash=f"sha256:{'8' * 64}",
    )


def test_v11_enriches_matching_swing_zone_without_changing_native_decision() -> None:
    base_context = _bullish_context()
    context = base_context.model_copy(update={"support": _support(base_context)})

    native = SwingEngineV10().analyze(base_context)
    enriched = SwingEngineV11().analyze(context)
    metrics = {item.name: item.value for item in enriched.metrics}

    assert enriched.verdict is native.verdict
    assert enriched.score == native.score
    assert metrics["support_contribution"] == "REACTION"
    assert metrics["support_zone_match"] == "ENTRY_ZONE"
    assert "support_confirmation_reaction_confluence" in enriched.reasons
    assert context.support is not None
    assert context.support.assessment_id in enriched.source_event_ids


def test_v11_ignores_invalidated_support_instead_of_downgrading_swing() -> None:
    base_context = _bullish_context()
    invalidated = _support(base_context, SupportState.INVALIDATED)
    context = base_context.model_copy(update={"support": invalidated})

    native = SwingEngineV10().analyze(base_context)
    enriched = SwingEngineV11().analyze(context)

    assert enriched.verdict is native.verdict
    assert enriched.score == native.score
    assert not any(item.name.startswith("support_") for item in enriched.metrics)


def test_swing_context_rejects_future_support_evidence() -> None:
    context = _bullish_context()
    future = _support(context).model_copy(update={"occurred_at": datetime(2027, 1, 1, tzinfo=UTC)})

    values = context.model_dump(mode="python")
    values["support"] = future
    with pytest.raises(ValueError, match="later than Swing as_of"):
        type(context)(**values)
