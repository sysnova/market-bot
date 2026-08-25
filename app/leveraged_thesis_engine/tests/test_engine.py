from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    MarketSession,
    NamedValue,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowWindow,
    PatternDirection,
    SupportAssessment,
    SupportState,
    SupportZonePosition,
)
from app.contracts.leveraged_thesis import LeveragedExposure, LeveragedThesisState
from app.leveraged_thesis_engine import (
    LeveragedPair,
    LeveragedThesisContext,
    LeveragedThesisEngine,
)

NOW = datetime(2026, 8, 24, 15, tzinfo=UTC)


def test_bearish_order_flow_emits_early_astn_before_structure_confirms() -> None:
    evaluation = LeveragedThesisEngine().evaluate(
        _context(
            pair=_asts_pair(),
            analysis=_analysis(direction=PatternDirection.NEUTRAL, setup="no_trigger"),
            underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
        )
    )

    assert evaluation.assessment.state is LeveragedThesisState.EARLY_FLOW
    assert evaluation.assessment.instrument_symbol == "ASTN"
    assert evaluation.assessment.exposure is LeveragedExposure.INVERSE_2X
    assert evaluation.transition is not None


def test_bearish_structure_and_both_flows_confirm_astn_buy_candidate() -> None:
    evaluation = LeveragedThesisEngine().evaluate(
        _context(
            pair=_asts_pair(),
            analysis=_analysis(
                direction=PatternDirection.BEARISH,
                setup="bearish_breakdown",
                regime="bearish_trend",
                quality="strong",
                score=Decimal("82"),
            ),
            underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
        )
    )

    assert evaluation.assessment.state is LeveragedThesisState.BUY_CONFIRMED
    assert evaluation.assessment.instrument_symbol == "ASTN"


def test_bullish_structure_selects_astx_and_nbis_uses_unlevered_long() -> None:
    engine = LeveragedThesisEngine()
    asts = engine.evaluate(
        _context(
            pair=_asts_pair(),
            analysis=_analysis(
                direction=PatternDirection.BULLISH,
                setup="bullish_breakout",
                regime="bullish_trend",
                quality="strong",
                score=Decimal("80"),
            ),
            underlying_flow=_flow("ASTS", OrderFlowStateKind.BUY_PRESSURE),
        )
    ).assessment
    nbis = engine.evaluate(
        _context(
            pair=LeveragedPair(
                underlying_symbol="NBIS",
                bullish_instrument="NBIS",
                bearish_instrument="NBIZ",
                bullish_exposure=LeveragedExposure.LONG_1X,
            ),
            analysis=_analysis(
                symbol="NBIS",
                direction=PatternDirection.BULLISH,
                setup="bullish_vwap_reclaim",
                regime="bullish_trend",
                quality="standard",
                score=Decimal("74"),
            ),
            underlying_flow=_flow("NBIS", OrderFlowStateKind.BUY_PRESSURE),
            bullish_symbol="NBIS",
            bearish_symbol="NBIZ",
        )
    ).assessment

    assert (asts.instrument_symbol, asts.exposure) == (
        "ASTX",
        LeveragedExposure.LONG_2X,
    )
    assert (nbis.instrument_symbol, nbis.exposure) == (
        "NBIS",
        LeveragedExposure.LONG_1X,
    )


def test_hostile_instrument_flow_or_non_regular_session_blocks_candidate() -> None:
    context = _context(
        pair=_asts_pair(),
        analysis=_analysis(
            direction=PatternDirection.BEARISH,
            setup="bearish_breakdown",
            regime="bearish_trend",
            quality="strong",
            score=Decimal("82"),
        ),
        underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
        bearish_flow_kind=OrderFlowStateKind.SELL_PRESSURE,
    )
    hostile = LeveragedThesisEngine().evaluate(context).assessment
    closed = (
        LeveragedThesisEngine()
        .evaluate(
            LeveragedThesisContext(
                pair=context.pair,
                as_of=context.as_of,
                session=MarketSession.AFTER_HOURS,
                analysis=context.analysis,
                underlying_flow=context.underlying_flow,
                instrument_flows=context.instrument_flows,
                support=context.support,
            )
        )
        .assessment
    )

    assert hostile.state is LeveragedThesisState.BLOCKED
    assert "instrument_flow_opposes_buy" in hostile.reasons
    assert closed.state is LeveragedThesisState.BLOCKED
    assert "regular_session_required" in closed.reasons


def test_repeated_state_does_not_emit_duplicate_transition() -> None:
    engine = LeveragedThesisEngine()
    first_context = _context(
        pair=_asts_pair(),
        analysis=_analysis(direction=PatternDirection.NEUTRAL, setup="no_trigger"),
        underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
    )
    first = engine.evaluate(first_context)
    repeated = engine.evaluate(
        LeveragedThesisContext(
            pair=first_context.pair,
            as_of=first_context.as_of + timedelta(seconds=1),
            session=first_context.session,
            analysis=first_context.analysis,
            underlying_flow=first_context.underlying_flow.model_copy(
                update={"occurred_at": first_context.as_of + timedelta(seconds=1)}
            ),
            instrument_flows=first_context.instrument_flows,
            support=first_context.support,
            previous_assessment=first.assessment,
        )
    )

    assert repeated.assessment.state is LeveragedThesisState.EARLY_FLOW
    assert repeated.transition is None


def test_nearby_unbroken_support_blocks_bearish_arming() -> None:
    assessment = (
        LeveragedThesisEngine()
        .evaluate(
            _context(
                pair=_asts_pair(),
                analysis=_analysis(
                    direction=PatternDirection.BEARISH,
                    setup="bearish_breakdown",
                    regime="bearish_trend",
                    quality="strong",
                    score=Decimal("82"),
                ),
                underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
                support=_support(
                    "ASTS",
                    state=SupportState.REACTION_CONFIRMED,
                    zone_low=Decimal("60"),
                    zone_high=Decimal("61"),
                    invalidation=Decimal("59"),
                    position=SupportZonePosition.ABOVE_ZONE,
                ),
            )
        )
        .assessment
    )

    assert assessment.state is LeveragedThesisState.BLOCKED
    assert "nearby_support_blocks_short" in assessment.reasons


def test_nearby_support_blocks_even_an_early_bearish_flow_notice() -> None:
    assessment = (
        LeveragedThesisEngine()
        .evaluate(
            _context(
                pair=_asts_pair(),
                analysis=_analysis(direction=PatternDirection.NEUTRAL, setup="no_trigger"),
                underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
                support=_support(
                    "ASTS",
                    state=SupportState.WATCH_KEY_SUPPORT,
                    zone_low=Decimal("60"),
                    zone_high=Decimal("61"),
                    invalidation=Decimal("59"),
                    position=SupportZonePosition.ABOVE_ZONE,
                ),
            )
        )
        .assessment
    )

    assert assessment.state is LeveragedThesisState.BLOCKED
    assert "nearby_support_blocks_short" in assessment.reasons


def test_long_first_touch_arms_watch_but_cannot_confirm_buy() -> None:
    assessment = (
        LeveragedThesisEngine()
        .evaluate(
            _context(
                pair=_asts_pair(),
                analysis=_analysis(
                    direction=PatternDirection.BULLISH,
                    setup="bullish_breakout",
                    regime="bullish_trend",
                    quality="strong",
                    score=Decimal("82"),
                ),
                underlying_flow=_flow("ASTS", OrderFlowStateKind.BUY_PRESSURE),
                support=_support(
                    "ASTS",
                    state=SupportState.FIRST_TOUCH,
                    zone_low=Decimal("61"),
                    zone_high=Decimal("63"),
                    invalidation=Decimal("60"),
                    position=SupportZonePosition.IN_ZONE,
                    reaction_score=Decimal("25"),
                ),
            )
        )
        .assessment
    )

    assert assessment.state is LeveragedThesisState.STRUCTURE_ARMED
    assert "support_reaction_pending" in assessment.reasons


def test_missing_support_assessment_is_pending_not_bearish_evidence() -> None:
    assessment = (
        LeveragedThesisEngine()
        .evaluate(
            _context(
                pair=_asts_pair(),
                analysis=_analysis(
                    direction=PatternDirection.BEARISH,
                    setup="bearish_breakdown",
                    regime="bearish_trend",
                    quality="strong",
                    score=Decimal("82"),
                ),
                underlying_flow=_flow("ASTS", OrderFlowStateKind.SELL_PRESSURE),
                support=None,
                include_default_support=False,
            )
        )
        .assessment
    )

    assert assessment.state is LeveragedThesisState.OBSERVING
    assert "support_assessment_pending" in assessment.reasons


def _asts_pair() -> LeveragedPair:
    return LeveragedPair(
        underlying_symbol="ASTS",
        bullish_instrument="ASTX",
        bearish_instrument="ASTN",
        bullish_exposure=LeveragedExposure.LONG_2X,
    )


def _context(
    *,
    pair: LeveragedPair,
    analysis: AnalysisResult,
    underlying_flow: OrderFlowState,
    bullish_symbol: str = "ASTX",
    bearish_symbol: str = "ASTN",
    bearish_flow_kind: OrderFlowStateKind = OrderFlowStateKind.BUY_PRESSURE,
    support: SupportAssessment | None = None,
    include_default_support: bool = True,
) -> LeveragedThesisContext:
    if include_default_support and support is None:
        support = (
            _support(
                pair.underlying_symbol,
                state=SupportState.REACTION_CONFIRMED,
                zone_low=Decimal("60"),
                zone_high=Decimal("61"),
                invalidation=Decimal("59"),
                position=SupportZonePosition.ABOVE_ZONE,
            )
            if underlying_flow.state is OrderFlowStateKind.BUY_PRESSURE
            else _support(
                pair.underlying_symbol,
                state=SupportState.INVALIDATED,
                zone_low=Decimal("64"),
                zone_high=Decimal("65"),
                invalidation=Decimal("63"),
                position=SupportZonePosition.BELOW_ZONE,
            )
        )
    return LeveragedThesisContext(
        pair=pair,
        as_of=NOW,
        session=MarketSession.REGULAR,
        analysis=analysis,
        underlying_flow=underlying_flow,
        support=support,
        instrument_flows={
            bullish_symbol: _flow(
                bullish_symbol,
                OrderFlowStateKind.BUY_PRESSURE,
                bid=Decimal("11.50"),
                ask=Decimal("11.52"),
            ),
            bearish_symbol: _flow(
                bearish_symbol,
                bearish_flow_kind,
                bid=Decimal("4.91"),
                ask=Decimal("4.92"),
            ),
        },
    )


def _analysis(
    *,
    symbol: str = "ASTS",
    direction: PatternDirection,
    setup: str,
    regime: str = "range_or_transition",
    quality: str = "weak",
    score: Decimal = Decimal("35"),
) -> AnalysisResult:
    return AnalysisResult(
        engine_id="intraday",
        engine_version="4.0.0",
        symbol=symbol,
        horizon=AnalysisHorizon.INTRADAY,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE if score >= 70 else AnalysisVerdict.WATCH,
        direction=direction,
        score=score,
        confidence=score / Decimal("100"),
        reasons=(f"setup:{setup}",),
        metrics=(
            NamedValue(name="setup", value=setup),
            NamedValue(name="intraday_regime", value=regime),
            NamedValue(name="confirmation_quality", value=quality),
        ),
        context_hash="sha256:" + "b" * 64,
    )


def _flow(
    symbol: str,
    kind: OrderFlowStateKind,
    *,
    bid: Decimal | None = None,
    ask: Decimal | None = None,
) -> OrderFlowState:
    windows = tuple(
        OrderFlowWindow(
            window_seconds=seconds,
            trade_count=20,
            buy_volume=Decimal("800")
            if kind is OrderFlowStateKind.BUY_PRESSURE
            else Decimal("200"),
            sell_volume=Decimal("200")
            if kind is OrderFlowStateKind.BUY_PRESSURE
            else Decimal("800"),
            neutral_volume=Decimal("0"),
            unknown_volume=Decimal("0"),
            delta=Decimal("600") if kind is OrderFlowStateKind.BUY_PRESSURE else Decimal("-600"),
            volume_velocity=Decimal("10"),
            large_buy_volume=Decimal("0"),
            large_sell_volume=Decimal("0"),
            price_change_bps=Decimal("3")
            if kind is OrderFlowStateKind.BUY_PRESSURE
            else Decimal("-3"),
        )
        for seconds in (1, 5, 15, 60, 300)
    )
    midpoint = (bid + ask) / Decimal("2") if bid is not None and ask is not None else None
    spread_bps = (
        ((ask - bid) / midpoint * Decimal("10000")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if bid is not None and ask is not None and midpoint is not None
        else None
    )
    return OrderFlowState(
        symbol=symbol,
        occurred_at=NOW,
        engine_version="1.1.0" if bid is not None else "1.0.0",
        state=kind,
        current_price=Decimal("62") if symbol in {"ASTS", "NBIS"} else Decimal("5"),
        mid_price=midpoint or (Decimal("62") if symbol in {"ASTS", "NBIS"} else Decimal("5")),
        bid_price=bid,
        ask_price=ask,
        spread_bps=spread_bps,
        cumulative_delta=Decimal("600"),
        confidence=Decimal("0.80"),
        data_quality=Decimal("0.90"),
        quote_age_ms=Decimal("100"),
        quote_fresh=True,
        unknown_trade_ratio=Decimal("0"),
        windows=windows,
        reasons=(kind.value.lower(),),
        context_hash="sha256:" + ("c" if symbol in {"ASTS", "NBIS"} else "d") * 64,
    )


def _support(
    symbol: str,
    *,
    state: SupportState,
    zone_low: Decimal,
    zone_high: Decimal,
    invalidation: Decimal,
    position: SupportZonePosition,
    reaction_score: Decimal = Decimal("75"),
) -> SupportAssessment:
    return SupportAssessment(
        symbol=symbol,
        occurred_at=NOW,
        data_as_of=NOW,
        assessed_at=NOW,
        engine_version="0.3.0",
        state=state,
        current_price=Decimal("62"),
        zone_low=zone_low,
        zone_center=(zone_low + zone_high) / Decimal("2"),
        zone_high=zone_high,
        invalidation=invalidation,
        support_score=Decimal("75"),
        reaction_score=reaction_score,
        reversal_score=Decimal("65"),
        confidence=Decimal("0.8"),
        zone_position=position,
        zone_distance_percent=Decimal("1"),
        zone_distance_atr=Decimal("0.8"),
        touch_count=2,
        touch_age_sessions=0,
        actionability_score=Decimal("70"),
        support_sources=("daily-pivot", "weekly-avwap"),
        reasons=("fixture",),
        context_hash="sha256:" + "f" * 64,
    )
