from datetime import timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    LocalAlert,
    NamedValue,
    OrderFlowStateKind,
    new_uuid7,
)
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.v11 import DeferredShortState, LeveragedThesisEngineV11


def alert() -> LocalAlert:
    return LocalAlert(
        symbol="ASTS",
        created_at=NOW,
        severity=AlertSeverity.ACTION,
        title="ASTS SHORT CONFIRMED",
        message="confirmed",
        horizons=(AnalysisHorizon.INTRADAY,),
        component_analysis_ids=(new_uuid7(),),
        score=Decimal("70"),
        reasons=("short_entry_confirmed",),
        deduplication_key="test",
        kind=AlertKind.BEARISH_CONSENSUS,
        metrics=(
            NamedValue(name="short_invalidation", value="63"),
            NamedValue(name="short_entry_price", value="62"),
            NamedValue(name="short_setup_id", value="short:ASTS:test"),
        ),
    )


def test_deferred_short_survives_neutral_underlying_and_confirms_later() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    assert state.intent is not None
    later = NOW + timedelta(minutes=12)
    state = engine.advance_short(
        state, now=later, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, occurred_at=later)
    )
    state = engine.advance_short(
        state,
        now=later,
        flow=_flow(
            "ASTN",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=later,
        ),
    )
    assert state.assessment.state.value == "BUY_CONFIRMED"
    assert "instrument_buy_flow_confirmed" in state.assessment.reasons


def test_price_rise_can_confirm_without_flow_confidence() -> None:
    engine = LeveragedThesisEngineV11()
    state = DeferredShortState()
    baseline = _flow(
        "ASTN",
        OrderFlowStateKind.NEUTRAL,
        bid=Decimal("5"),
        ask=Decimal("5.01"),
        occurred_at=NOW - timedelta(minutes=3),
    ).model_copy(update={"confidence": Decimal("0")})
    state = engine.advance_short(state, now=baseline.occurred_at, flow=baseline)
    state = engine.advance_short(state, now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    rising = _flow(
        "ASTN", OrderFlowStateKind.NEUTRAL, bid=Decimal("5.10"), ask=Decimal("5.11")
    ).model_copy(update={"confidence": Decimal("0")})
    state = engine.advance_short(state, now=NOW, flow=rising)
    assert state.assessment.state.value == "BUY_CONFIRMED"
    assert "instrument_price_rising_3m" in state.assessment.reasons


def test_invalidation_is_terminal_even_if_instrument_confirms_afterwards() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    state = engine.advance_short(
        state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, price=Decimal("63"))
    )
    assert state.assessment.state.value == "CANCELLED"
    state = engine.advance_short(
        state,
        now=NOW,
        flow=_flow("ASTN", OrderFlowStateKind.BUY_PRESSURE, bid=Decimal("5"), ask=Decimal("5.01")),
    )
    assert state.assessment.state.value == "CANCELLED"
    assert engine.advance_short(state, now=NOW, alert=alert()).intent == state.intent


def test_session_close_cancels_pending_and_old_alert_does_not_rearm() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    close = NOW.replace(hour=20)
    state = engine.advance_short(state, now=close)
    assert state.assessment.state.value == "CANCELLED"
    assert "short_session_expired" in state.assessment.reasons
    assert engine.advance_short(DeferredShortState(), now=close, alert=alert()).intent is None


def test_confirmed_purchase_cancels_if_underlying_later_reaches_invalidation() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_short(
        state,
        now=NOW,
        flow=_flow("ASTN", OrderFlowStateKind.BUY_PRESSURE, bid=Decimal("5"), ask=Decimal("5.01")),
    )
    assert state.assessment is not None and state.assessment.state.value == "BUY_CONFIRMED"
    later = NOW + timedelta(minutes=5)
    state = engine.advance_short(
        state,
        now=later,
        flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, price=Decimal("63"), occurred_at=later),
    )
    assert state.assessment is not None and state.assessment.state.value == "CANCELLED"


def test_stale_quote_never_confirms_even_with_buy_flow() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    stale = _flow(
        "ASTN",
        OrderFlowStateKind.BUY_PRESSURE,
        bid=Decimal("5"),
        ask=Decimal("5.01"),
        occurred_at=NOW - timedelta(seconds=3),
    )
    state = engine.advance_short(state, now=NOW, flow=stale)
    assert state.assessment is not None and state.assessment.state.value == "STRUCTURE_ARMED"


def test_price_without_a_full_three_minute_window_does_not_confirm() -> None:
    engine = LeveragedThesisEngineV11()
    state = engine.advance_short(DeferredShortState(), now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    flow = _flow("ASTN", OrderFlowStateKind.NEUTRAL, bid=Decimal("5"), ask=Decimal("5.01"))
    state = engine.advance_short(state, now=NOW, flow=flow)
    assert state.assessment is not None and state.assessment.state.value == "STRUCTURE_ARMED"


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(minutes=30), "BUY_CONFIRMED"),
        (timedelta(minutes=30, seconds=1), "STRUCTURE_ARMED"),
        (timedelta(days=1), "STRUCTURE_ARMED"),
    ],
)
def test_instrument_first_confirmation_has_thirty_minute_limit(
    age: timedelta, expected: str
) -> None:
    engine = LeveragedThesisEngineV11()
    earlier = NOW - age
    state = engine.advance_short(
        DeferredShortState(),
        now=earlier,
        flow=_flow(
            "ASTN",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=earlier,
        ),
    )
    state = engine.advance_short(
        state,
        now=NOW,
        flow=_flow("ASTN", OrderFlowStateKind.NEUTRAL, bid=Decimal("6"), ask=Decimal("6.01")),
    )
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_short(state, now=NOW, alert=alert())
    assert state.assessment is not None and state.assessment.state.value == expected
    if expected == "BUY_CONFIRMED":
        assert "instrument_prior_entry_confirmed" in state.assessment.reasons
        assert state.assessment.instrument_ask == Decimal("6.01")


def test_cached_entry_still_requires_current_quote_and_respects_invalidation() -> None:
    engine = LeveragedThesisEngineV11()
    earlier = NOW - timedelta(minutes=10)
    state = engine.advance_short(
        DeferredShortState(),
        now=earlier,
        flow=_flow(
            "ASTN",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=earlier,
        ),
    )
    assert state.entry_evidence is not None
    state = engine.advance_short(state, now=NOW, alert=alert())
    state = engine.advance_short(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    assert state.assessment.state.value == "STRUCTURE_ARMED"
    state = engine.advance_short(
        state,
        now=NOW + timedelta(seconds=1),
        flow=_flow(
            "ASTS",
            OrderFlowStateKind.NEUTRAL,
            price=Decimal("63"),
            occurred_at=NOW + timedelta(seconds=1),
        ),
    )
    assert state.assessment.state.value == "CANCELLED"


def test_price_confirmation_is_retained_before_short_exists() -> None:
    engine = LeveragedThesisEngineV11()
    state = DeferredShortState()
    for minutes, price in [(-13, "5"), (-10, "5.10")]:
        at = NOW + timedelta(minutes=minutes)
        state = engine.advance_short(
            state,
            now=at,
            flow=_flow(
                "ASTN",
                OrderFlowStateKind.NEUTRAL,
                bid=Decimal(price),
                ask=Decimal(price) + Decimal(".01"),
                occurred_at=at,
            ),
        )
    assert state.entry_evidence is not None
    assert state.entry_evidence.reason == "instrument_price_rising_3m"
    assert state.intent is None
