from datetime import timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    OrderFlowStateKind,
    SwingTradeMaturity,
)
from app.leveraged_thesis_engine.tests.test_engine import NOW, _flow
from app.leveraged_thesis_engine.v12 import DeferredLongState, LeveragedThesisEngineV12


def long_signal(family: EntrySignalFamily = EntrySignalFamily.SWING_TRADE) -> EntrySignal:
    return EntrySignal(
        family=family,
        symbol="ASTS",
        created_at=NOW,
        setup_id="asts-daily-recovery",
        swing_trade_maturity=SwingTradeMaturity.ST3
        if family is EntrySignalFamily.SWING_TRADE
        else None,
        maturity=EntryMaturityLevel.L2 if family is EntrySignalFamily.CORE_ENTRY else None,
        entry_price=Decimal("62"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("61"),
        zone_high=Decimal("62"),
        invalidation=Decimal("60"),
        policy_id="test",
        policy_version="1.0.0",
        reasons=("native_buy_confirmed",),
    )


@pytest.mark.parametrize("family", [EntrySignalFamily.SWING_TRADE, EntrySignalFamily.CORE_ENTRY])
def test_either_native_swing_confirmation_accepts_prior_astx_entry(
    family: EntrySignalFamily,
) -> None:
    engine = LeveragedThesisEngineV12()
    before = NOW - timedelta(minutes=20)
    state = engine.advance_long(
        DeferredLongState(),
        now=before,
        flow=_flow(
            "ASTX",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=before,
        ),
    )
    assert state.entry_evidence is not None and state.intent is None
    state = engine.advance_long(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_long(
        state,
        now=NOW,
        flow=_flow("ASTX", OrderFlowStateKind.NEUTRAL, bid=Decimal("6"), ask=Decimal("6.01")),
    )
    state = engine.advance_long(state, now=NOW, signal=long_signal(family))
    assert state.assessment is not None and state.assessment.state.value == "BUY_CONFIRMED"
    assert state.assessment.instrument_symbol == "ASTX"
    assert state.assessment.direction.value == "BULLISH"
    assert state.assessment.exposure.value == "LONG_2X"
    assert state.assessment.instrument_ask == Decimal("6.01")
    assert "instrument_prior_entry_confirmed" in state.assessment.reasons
    assert state.assessment.source_entry_signal_id == state.intent.signal.signal_id


@pytest.mark.parametrize(
    "changes",
    [
        {"swing_trade_maturity": SwingTradeMaturity.ST2},
        {"symbol": "NBIS"},
        {"invalidation": None},
        {"created_at": NOW - timedelta(days=1)},
        {"created_at": NOW + timedelta(seconds=1)},
    ],
)
def test_non_actionable_or_stale_signals_do_not_arm(changes: dict[str, object]) -> None:
    state = LeveragedThesisEngineV12().advance_long(
        DeferredLongState(), now=NOW, signal=long_signal().model_copy(update=changes)
    )
    assert state.intent is None


def test_l1_is_not_swing_confirmation() -> None:
    signal = long_signal(EntrySignalFamily.CORE_ENTRY).model_copy(
        update={"maturity": EntryMaturityLevel.L1}
    )
    assert (
        LeveragedThesisEngineV12().advance_long(DeferredLongState(), now=NOW, signal=signal).intent
        is None
    )


def test_pending_long_cancels_at_underlying_stop_and_cannot_rearm_same_setup() -> None:
    engine = LeveragedThesisEngineV12()
    signal = long_signal()
    state = engine.advance_long(DeferredLongState(), now=NOW, signal=signal)
    state = engine.advance_long(
        state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, price=Decimal("60"))
    )
    assert state.assessment.state.value == "CANCELLED"
    state = engine.advance_long(state, now=NOW + timedelta(seconds=1), signal=signal)
    assert state.assessment.state.value == "CANCELLED"


def test_pending_long_expires_at_regular_close() -> None:
    engine = LeveragedThesisEngineV12()
    state = engine.advance_long(DeferredLongState(), now=NOW, signal=long_signal())
    state = engine.advance_long(state, now=NOW.replace(hour=20))
    assert state.assessment.state.value == "CANCELLED"
    assert "long_session_expired" in state.assessment.reasons


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(minutes=30), "BUY_CONFIRMED"),
        (timedelta(minutes=30, seconds=1), "STRUCTURE_ARMED"),
    ],
)
def test_astx_prior_entry_age_boundary(age: timedelta, expected: str) -> None:
    engine = LeveragedThesisEngineV12()
    at = NOW - age
    state = engine.advance_long(
        DeferredLongState(),
        now=at,
        flow=_flow(
            "ASTX",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=at,
        ),
    )
    state = engine.advance_long(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    state = engine.advance_long(
        state,
        now=NOW,
        flow=_flow("ASTX", OrderFlowStateKind.NEUTRAL, bid=Decimal("6"), ask=Decimal("6.01")),
    )
    state = engine.advance_long(state, now=NOW, signal=long_signal())
    assert state.assessment.state.value == expected


def test_st4_can_confirm_astx_later_by_price_without_buy_flow() -> None:
    engine = LeveragedThesisEngineV12()
    source = long_signal().model_copy(update={"swing_trade_maturity": SwingTradeMaturity.ST4})
    state = engine.advance_long(DeferredLongState(), now=NOW, signal=source)
    for seconds, price in [(0, "5"), (180, "5.1")]:
        at = NOW + timedelta(seconds=seconds)
        state = engine.advance_long(
            state, now=at, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL, occurred_at=at)
        )
        state = engine.advance_long(
            state,
            now=at,
            flow=_flow(
                "ASTX",
                OrderFlowStateKind.NEUTRAL,
                bid=Decimal(price),
                ask=Decimal(price) + Decimal(".01"),
                occurred_at=at,
            ),
        )
    assert state.assessment.state.value == "BUY_CONFIRMED"
    assert "instrument_price_rising_3m" in state.assessment.reasons


def test_cached_astx_evidence_does_not_replace_current_executable_quote() -> None:
    engine = LeveragedThesisEngineV12()
    at = NOW - timedelta(minutes=10)
    state = engine.advance_long(
        DeferredLongState(),
        now=at,
        flow=_flow(
            "ASTX",
            OrderFlowStateKind.BUY_PRESSURE,
            bid=Decimal("5"),
            ask=Decimal("5.01"),
            occurred_at=at,
        ),
    )
    state = engine.advance_long(state, now=NOW, signal=long_signal())
    state = engine.advance_long(state, now=NOW, flow=_flow("ASTS", OrderFlowStateKind.NEUTRAL))
    assert state.assessment.state.value == "STRUCTURE_ARMED"
    assert "instrument_quote_pending_or_stale" in state.assessment.reasons
