from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    FusionState,
    MacroRegime,
    NamedValue,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatternDirection,
    StrategyMode,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    WaveAssessment,
    WavePhase,
)
from app.signal_fusion_engine import SignalFusionContext, SignalFusionEngine

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def _analysis(
    horizon: AnalysisHorizon,
    *,
    verdict: AnalysisVerdict = AnalysisVerdict.FAVORABLE,
    score: str = "80",
    reference_price: str = "105",
    invalidation_level: str = "100",
    objective_level: str = "116",
) -> AnalysisResult:
    hash_tokens = {
        AnalysisHorizon.LONG_TERM: "1",
        AnalysisHorizon.SWING: "2",
        AnalysisHorizon.INTRADAY: "3",
        AnalysisHorizon.DILUTION: "4",
    }
    metrics: tuple[NamedValue, ...] = ()
    if horizon is AnalysisHorizon.SWING:
        metrics = (
            NamedValue(name="invalidation", value=Decimal("98")),
            NamedValue(name="target_2r", value=Decimal("120")),
        )
    elif horizon is AnalysisHorizon.INTRADAY:
        metrics = (
            NamedValue(name="reference_price", value=Decimal(reference_price)),
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="invalidation_level", value=Decimal(invalidation_level)),
            NamedValue(name="objective_level", value=Decimal(objective_level)),
            NamedValue(name="reward_risk_ratio", value=Decimal("2.2")),
        )
    return AnalysisResult(
        engine_id=f"{horizon.value.lower()}-engine",
        engine_version="3.0.0",
        symbol="TGT",
        horizon=horizon,
        as_of=NOW,
        verdict=verdict,
        direction=(
            PatternDirection.BEARISH
            if verdict is AnalysisVerdict.AVOID
            else PatternDirection.BULLISH
        ),
        score=Decimal(score),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        metrics=metrics,
        context_hash=f"sha256:{hash_tokens[horizon] * 64}",
    )


def _support(**updates: object) -> SupportAssessment:
    values: dict[str, object] = {
        "symbol": "TGT",
        "occurred_at": NOW,
        "engine_version": "0.1.0",
        "state": SupportState.STRUCTURE_CONFIRMED,
        "confirmation_type": SupportConfirmationType.SWEEP_RECLAIM,
        "current_price": Decimal("105"),
        "zone_low": Decimal("99"),
        "zone_center": Decimal("100"),
        "zone_high": Decimal("101"),
        "invalidation": Decimal("96"),
        "support_score": Decimal("85"),
        "reaction_score": Decimal("90"),
        "reversal_score": Decimal("70"),
        "confidence": Decimal("0.9"),
        "higher_high": True,
        "higher_low": True,
        "b_wave_risk": False,
        "reasons": ("fixture",),
        "context_hash": f"sha256:{'a' * 64}",
    }
    values.update(updates)
    return SupportAssessment(**values)  # type: ignore[arg-type]


def _wave(**updates: object) -> WaveAssessment:
    values: dict[str, object] = {
        "symbol": "TGT",
        "occurred_at": NOW,
        "engine_version": "0.1.0",
        "primary_timeframe": BarTimeframe.DAY_1,
        "phase": WavePhase.WAVE_3_ACTIVE,
        "score": Decimal("85"),
        "confidence": Decimal("0.85"),
        "current_price": Decimal("105"),
        "wave1_origin": Decimal("80"),
        "wave1_peak": Decimal("110"),
        "corrective_low": Decimal("96"),
        "entry_zone_low": Decimal("96"),
        "entry_zone_high": Decimal("101"),
        "trigger_price": Decimal("103"),
        "invalidation": Decimal("94"),
        "target_low": Decimal("125"),
        "target_high": Decimal("132"),
        "reasons": ("fixture",),
        "context_hash": f"sha256:{'b' * 64}",
    }
    values.update(updates)
    return WaveAssessment(**values)  # type: ignore[arg-type]


def _patreon() -> PatreonCapsAssessment:
    return PatreonCapsAssessment(
        symbol="TGT",
        occurred_at=NOW,
        rule_version="1.1.0",
        mode=StrategyMode.PRIMARY,
        state=PatreonCapsState.CONFIRMED_BASE,
        current_price=Decimal("105"),
        zone_low=Decimal("99"),
        zone_center=Decimal("100"),
        zone_high=Decimal("101"),
        invalidation=Decimal("96"),
        atr14=Decimal("3"),
        confluence_score=Decimal("85"),
        confirmation_score=Decimal("80"),
        alignment_score=Decimal("75"),
        patreon_score=Decimal("82"),
        macro_regime=MacroRegime.RISK_ON,
        reasons=("fixture",),
    )


def _context(**updates: object) -> SignalFusionContext:
    values: dict[str, object] = {
        "symbol": "TGT",
        "support": _support(),
        "wave": _wave(),
        "analyses": (
            _analysis(AnalysisHorizon.LONG_TERM),
            _analysis(AnalysisHorizon.SWING),
            _analysis(AnalysisHorizon.INTRADAY),
        ),
        "patreon": _patreon(),
        "holding_quantity": Decimal("10"),
    }
    values.update(updates)
    return SignalFusionContext(**values)  # type: ignore[arg-type]


def test_independent_gates_confirm_an_analytical_buy() -> None:
    result = SignalFusionEngine().evaluate(_context())

    assert result.state is FusionState.BUY_CONFIRMED
    assert result.support_zone_gate is True
    assert result.support_reaction_gate is True
    assert result.support_gate is True
    assert result.trend_gate is True
    assert result.timing_gate is True
    assert result.execution_gate is True
    assert result.dilution_gate is True
    assert result.portfolio_gate is True
    assert result.reward_risk_gate is True
    assert result.reward_risk_ratio is not None
    assert result.reward_risk_ratio >= Decimal("2")
    assert result.patreon_context == PatreonCapsState.CONFIRMED_BASE.value


def test_strong_reaction_with_b_wave_risk_stays_observing() -> None:
    support = _support(
        state=SupportState.RECLAIMED,
        reversal_score=Decimal("10"),
        higher_high=False,
        higher_low=False,
        b_wave_risk=True,
    )

    analyses = (
        _analysis(AnalysisHorizon.LONG_TERM),
        _analysis(AnalysisHorizon.SWING),
        _analysis(
            AnalysisHorizon.INTRADAY,
            verdict=AnalysisVerdict.WATCH,
            score="50",
        ),
    )

    result = SignalFusionEngine().evaluate(
        _context(support=support, analyses=analyses)
    )

    assert result.state is FusionState.OBSERVING
    assert result.support_zone_gate is True
    assert result.support_reaction_gate is True
    assert result.support_gate is False
    assert "support_structure_unconfirmed" in result.reasons


def test_elliott_trigger_and_intraday_confirmation_open_recovery_path() -> None:
    support = _support(
        state=SupportState.REACTION_CONFIRMED,
        current_price=Decimal("104"),
        reversal_score=Decimal("10"),
        higher_high=False,
        higher_low=False,
        b_wave_risk=True,
    )
    analyses = (
        _analysis(
            AnalysisHorizon.LONG_TERM,
            verdict=AnalysisVerdict.WATCH,
            score="20",
        ),
        _analysis(AnalysisHorizon.SWING),
        _analysis(
            AnalysisHorizon.INTRADAY,
            reference_price="105",
            invalidation_level="100",
            objective_level="112.5",
        ),
    )

    result = SignalFusionEngine().evaluate(
        _context(support=support, analyses=analyses)
    )

    assert result.state is FusionState.RECOVERY_CONFIRMED
    assert result.current_price == Decimal("105")
    assert result.recovery_gate is True
    assert result.support_zone_gate is True
    assert result.support_reaction_gate is True
    assert result.support_gate is False
    assert result.trend_gate is False
    assert result.timing_gate is True
    assert result.execution_gate is True
    assert result.invalidation == Decimal("100")
    assert result.target_price == Decimal("120")
    assert result.reward_risk_ratio == Decimal("3.0000")
    assert "elliott_trigger_with_intraday_confirmation" in result.reasons
    assert "recovery_entry_tactical_size_only" in result.reasons


def test_recovery_path_waits_for_elliott_trigger() -> None:
    support = _support(
        state=SupportState.REACTION_CONFIRMED,
        reversal_score=Decimal("10"),
        higher_high=False,
        higher_low=False,
    )
    analyses = (
        _analysis(
            AnalysisHorizon.LONG_TERM,
            verdict=AnalysisVerdict.WATCH,
            score="20",
        ),
        _analysis(AnalysisHorizon.SWING),
        _analysis(AnalysisHorizon.INTRADAY),
    )
    wave = _wave(
        phase=WavePhase.WAVE_2_ENDING,
        trigger_price=Decimal("106"),
    )

    result = SignalFusionEngine().evaluate(
        _context(support=support, wave=wave, analyses=analyses)
    )

    assert result.state is FusionState.OBSERVING
    assert result.recovery_gate is False
    assert result.timing_gate is True  # Swing is favorable but cannot replace Elliott here.


def test_sec_avoid_is_a_hard_veto() -> None:
    analyses = (
        _analysis(AnalysisHorizon.LONG_TERM),
        _analysis(AnalysisHorizon.SWING),
        _analysis(AnalysisHorizon.INTRADAY),
        _analysis(
            AnalysisHorizon.DILUTION,
            verdict=AnalysisVerdict.AVOID,
            score="90",
        ),
    )

    result = SignalFusionEngine().evaluate(_context(analyses=analyses))

    assert result.state is FusionState.VETOED
    assert result.dilution_gate is False
    assert "sec_dilution_veto" in result.reasons


def test_patreon_does_not_replace_missing_independent_sources() -> None:
    result = SignalFusionEngine().evaluate(
        _context(analyses=(_analysis(AnalysisHorizon.SWING),))
    )

    assert result.state is FusionState.INCOMPLETE
    assert result.patreon_context == PatreonCapsState.CONFIRMED_BASE.value
    assert "LONG_TERM" in result.missing_sources
    assert "INTRADAY" in result.missing_sources
