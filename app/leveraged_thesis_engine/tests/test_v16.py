from datetime import timedelta
from decimal import Decimal

import pytest

from app.contracts import OrderFlowStateKind
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.tests.test_v11 import alert
from app.leveraged_thesis_engine.tests.test_v12 import long_signal
from app.leveraged_thesis_engine.v11 import DeferredShortState
from app.leveraged_thesis_engine.v12 import DeferredLongState
from app.leveraged_thesis_engine.v16 import LeveragedThesisEngineV16


@pytest.mark.parametrize(
    "kind", [OrderFlowStateKind.BUY_PRESSURE, OrderFlowStateKind.BUY_ABSORPTION]
)
@pytest.mark.parametrize("quotes", [False, True])
def test_daily_confirms_only_buyer_regime_without_etf_controls(
    kind: OrderFlowStateKind, quotes: bool
) -> None:
    engine = LeveragedThesisEngineV16()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    flow = _flow("ASTN", kind, bid=Decimal("5"), ask=Decimal("6"))
    updates = {"confidence": Decimal(".1"), "data_quality": Decimal(".1"), "quote_fresh": False}
    if not quotes:
        updates.update(bid_price=None, ask_price=None, spread_bps=None)
    state = engine.advance_short(state, now=NOW, flow=flow.model_copy(update=updates))
    assert state.assessment is not None
    assert state.assessment.state.value == "BUY_CONFIRMED"
    assert state.assessment.instrument_confirmation_basis == "BUYER_REGIME"
    assert (
        state.assessment.model_validate_json(state.assessment.model_dump_json()) == state.assessment
    )


@pytest.mark.parametrize(
    "kind,age",
    [
        (OrderFlowStateKind.NEUTRAL, 0),
        (OrderFlowStateKind.SELL_PRESSURE, 0),
        (OrderFlowStateKind.BUY_PRESSURE, 5),
        (OrderFlowStateKind.BUY_PRESSURE, -1),
    ],
)
def test_prior_buyer_evidence_or_rising_price_cannot_replace_current_flow(
    kind: OrderFlowStateKind, age: int
) -> None:
    engine = LeveragedThesisEngineV16()
    before = NOW - timedelta(minutes=3)
    state = engine.advance_short(
        DeferredShortState(),
        now=before,
        flow=_flow(
            "ASTN",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=before,
        ),
    )
    state = engine.advance_short(state, now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_short(
        state,
        now=NOW,
        flow=_flow(
            "ASTN",
            kind,
            bid=Decimal("6"),
            ask=Decimal("6.01"),
            occurred_at=NOW - timedelta(seconds=age),
        ),
    )
    assert state.assessment is not None and state.assessment.state.value == "STRUCTURE_ARMED"


def test_underlying_invalidation_and_session_expiry_still_cancel_daily() -> None:
    engine = LeveragedThesisEngineV16()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    stopped = engine.advance_short(
        state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, price=Decimal("63"))
    )
    expired = engine.advance_short(state, now=NOW.replace(hour=20))
    assert stopped.assessment is not None and stopped.assessment.state.value == "CANCELLED"
    assert expired.assessment is not None and expired.assessment.state.value == "CANCELLED"


def test_long_keeps_executable_quote_policy() -> None:
    engine = LeveragedThesisEngineV16()
    state = engine.advance_long(DeferredLongState(), now=NOW, signal=long_signal())
    state = engine.advance_long(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_long(state, now=NOW, flow=_flow("ASTX", OrderFlowStateKind.BUY_PRESSURE))
    assert state.assessment is not None and state.assessment.state.value == "STRUCTURE_ARMED"
    later = NOW + timedelta(seconds=1)
    state = engine.advance_long(
        state,
        now=later,
        flow=_flow(
            "ASTX",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=later,
        ),
    )
    assert state.assessment is not None and state.assessment.state.value == "BUY_CONFIRMED"
    assert state.assessment.instrument_confirmation_basis == "EXECUTABLE_QUOTE"
