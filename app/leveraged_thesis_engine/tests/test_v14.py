from datetime import timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    ExecutionQuoteSnapshot,
    MarketQuote,
    NamedValue,
    OrderFlowStateKind,
    PatternDirection,
)
from app.leveraged_thesis_engine.tests.test_engine import _flow
from app.leveraged_thesis_engine.tests.test_v13 import NOW, _intraday, _swing
from app.leveraged_thesis_engine.v14 import (
    LeveragedThesisEngineV14,
    ShortObservationContext,
    TacticalState,
)


def test_support_cap_is_retained_after_price_crosses_it() -> None:
    from dataclasses import replace

    engine = LeveragedThesisEngineV14()
    context = _context()
    assert context.swing is not None
    context = replace(
        context,
        swing=context.swing.model_copy(
            update={"metrics": (NamedValue(name="structural_support", value=Decimal("57.9")),)}
        ),
    )
    state, report = engine.observe_tactical(TacticalState(), context)
    assert report.status == "BLOCKED"
    assert state.intent is not None and state.intent.objective == Decimal("57.9")
    state, crossed = engine.observe_tactical(
        state, replace(context, underlying_quote=_quote("ASTS", "57.88", "57.89"))
    )
    assert crossed.status == "OBJECTIVE_REACHED"
    assert state.intent is not None and state.intent.objective == Decimal("57.9")


def test_stop_is_terminal_even_after_price_recovers_and_state_roundtrips() -> None:
    from dataclasses import replace

    engine = LeveragedThesisEngineV14()
    state, first = engine.observe_tactical(TacticalState(), _context())
    state, stopped = engine.observe_tactical(
        state, replace(_context(), underlying_quote=_quote("ASTS", "58.5", "58.51"))
    )
    assert stopped.status == "INVALIDATED"
    restored = TacticalState.model_validate_json(state.model_dump_json())
    _, later = engine.observe_tactical(restored, _context())
    assert later.status == "INVALIDATED" and later.setup_id == first.setup_id


def test_price_rise_uses_fresh_quote_history_without_refreshing_flow() -> None:
    from dataclasses import replace

    context = _context()
    old_at = NOW - timedelta(minutes=3)
    old_quote = _quote("ASTN", "16.9", "16.92")
    old_quote = old_quote.model_copy(
        update={
            "published_at": old_at,
            "quote": old_quote.quote.model_copy(
                update={"occurred_at": old_at, "received_at": old_at}
            ),
        }
    )
    engine = LeveragedThesisEngineV14()
    state, _ = engine.observe_tactical(
        TacticalState(),
        replace(
            context, now=old_at, intraday=None, instrument_quote=old_quote, instrument_flow=None
        ),
    )
    _, report = engine.observe_tactical(state, replace(context, instrument_flow=None))
    assert report.status == "READY"
    assert (
        next(m.value for m in report.metrics if m.name == "instrument_confirmation")
        == "price_rise_3m"
    )


def _quote(symbol: str, bid: str, ask: str) -> ExecutionQuoteSnapshot:
    return ExecutionQuoteSnapshot(
        quote=MarketQuote(
            symbol=symbol,
            occurred_at=NOW,
            received_at=NOW,
            bid_price=Decimal(bid),
            ask_price=Decimal(ask),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        ),
        published_at=NOW,
    )


def _context() -> ShortObservationContext:
    swing = _swing().model_copy(
        update={
            "direction": PatternDirection.BULLISH,
            "verdict": AnalysisVerdict.WATCH,
            "metrics": (NamedValue(name="structural_support", value=Decimal("54")),),
        }
    )
    instrument = _intraday().model_copy(
        update={
            "symbol": "ASTN",
            "direction": PatternDirection.BULLISH,
            "metrics": (
                NamedValue(name="invalidation_level", value=Decimal("16.8")),
                NamedValue(name="objective_level", value=Decimal("17.6")),
            ),
        }
    )
    return ShortObservationContext(
        symbol="ASTS",
        now=NOW,
        intraday=_intraday(),
        swing=swing,
        underlying_quote=_quote("ASTS", "58", "58.01"),
        instrument_quote=_quote("ASTN", "17", "17.02"),
        instrument_analysis=instrument,
        instrument_flow=_flow("ASTN", OrderFlowStateKind.BUY_PRESSURE, occurred_at=NOW),
    )


def test_tactical_can_observe_when_daily_swing_vetoes_without_emitting_buy() -> None:
    engine = LeveragedThesisEngineV14()
    state, report = engine.observe_tactical(TacticalState(), _context())
    assert report.status == "READY"
    assert report.orders_enabled is False
    assert state.intent is not None
    daily = engine.inspect_daily(_context())
    assert daily.status == "BLOCKED"
    assert "daily_structure_pending" in [g.reason for g in daily.gates if not g.passed]
    assert engine.evaluate_short(swing=_context().swing, intraday=_intraday(), now=NOW) is None


def test_late_price_rejects_remaining_reward_risk() -> None:
    from dataclasses import replace

    context = replace(_context(), underlying_quote=_quote("ASTS", "57.3", "57.31"))
    _, report = LeveragedThesisEngineV14().observe_tactical(TacticalState(), context)
    assert report.status == "BLOCKED"
    assert "remaining_reward_risk_insufficient" in [g.reason for g in report.gates]


def test_missing_quote_is_unavailable_rr_not_a_measured_bad_rr() -> None:
    from dataclasses import replace

    _, report = LeveragedThesisEngineV14().observe_tactical(
        TacticalState(), replace(_context(), underlying_quote=None)
    )
    assert "remaining_reward_risk_unavailable" in [g.reason for g in report.gates]


def test_mature_intent_retains_timing_when_the_trigger_disappears() -> None:
    from dataclasses import replace

    engine = LeveragedThesisEngineV14()
    state, first = engine.observe_tactical(TacticalState(), _context())
    _, waiting = engine.observe_tactical(state, replace(_context(), intraday=_intraday(gate=False)))
    assert waiting.setup_id == first.setup_id
    assert next(g.passed for g in waiting.gates if g.name == "timing") is True


def test_quote_refresh_cannot_refresh_old_order_flow() -> None:
    from dataclasses import replace

    context = _context()
    assert context.instrument_flow is not None
    context = replace(
        context,
        instrument_flow=context.instrument_flow.model_copy(
            update={"occurred_at": NOW - timedelta(seconds=10)}
        ),
    )
    _, report = LeveragedThesisEngineV14().observe_tactical(TacticalState(), context)
    assert "instrument_confirmation_pending" in [g.reason for g in report.gates]


def test_consecutive_signals_preserve_setup_and_original_invalidation() -> None:
    from dataclasses import replace

    engine = LeveragedThesisEngineV14()
    state, first = engine.observe_tactical(TacticalState(), _context())
    state, second = engine.observe_tactical(
        state, replace(_context(), intraday=_intraday(entry="57.9"))
    )
    assert first.setup_id == second.setup_id
    assert state.intent is not None and state.intent.invalidation == Decimal("58.50")


def test_stale_future_incomplete_and_wrong_symbol_inputs_fail_closed() -> None:
    from dataclasses import replace

    engine = LeveragedThesisEngineV14()
    for change in (
        {"underlying_quote": None},
        {"now": NOW + timedelta(seconds=3)},
        {"intraday": _intraday().model_copy(update={"as_of": NOW})},
        {"intraday": _intraday().model_copy(update={"symbol": "NBIS"})},
        {"intraday": _intraday().model_copy(update={"horizon": AnalysisHorizon.SWING})},
        {"instrument_analysis": None},
    ):
        _, report = engine.observe_tactical(TacticalState(), replace(_context(), **change))
        assert report.status != "READY"
