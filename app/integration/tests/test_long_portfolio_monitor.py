from app.integration.long_portfolio_monitor import _format_progress_line, _ProgressItem
from app.long_portfolio_engine import LongPortfolioValidationGate


def test_formats_hourly_progress_with_gate_and_session_failures() -> None:
    gates = tuple(
        LongPortfolioValidationGate(
            code=code,
            passed=code != "RF",
            detail="below_weekly_200w" if code == "RF" else "ok",
        )
        for code in ("V", "D", "SC", "C", "Z", "SET", "ENT", "TR", "REG", "RF")
    )
    item = _ProgressItem(
        symbol="OXY",
        validation_gates=gates,
        age_ok=True,
        age_detail="22h<=72h",
        qualified_sessions=0,
        minimum_sessions=2,
        cooldown_ok=True,
        cooldown_detail="ready",
        allocation_ok=True,
        allocation_detail="USD 51.50 remaining",
    )

    line = _format_progress_line(item)

    assert "OXY" in line
    assert "ESPERANDO CONDICIONES [############--] 12/14" in line
    assert "CUMPLE: análisis vigente (22h; máximo 72h)" in line
    assert "FALTA: riesgo semanal: debajo de la media de 200 semanas" in line
    assert "confirmaciones 0/2 sesiones" in line
    assert "RF=" not in line


def test_formats_missing_long_result_as_zero_progress() -> None:
    line = _format_progress_line(
        _ProgressItem(
            symbol="HIMS",
            validation_gates=(),
            age_ok=False,
            age_detail="no_long_result",
            qualified_sessions=0,
            minimum_sessions=2,
            cooldown_ok=True,
            cooldown_detail="ready",
            allocation_ok=True,
            allocation_detail="open",
        )
    )

    assert "SIN ANÁLISIS LONG [--------------] 0/14" in line
    assert "todavía no existe un análisis LONG para este ticker" in line


def test_formats_human_gate_values_and_covered_allocation() -> None:
    gates = (
        LongPortfolioValidationGate(code="V", passed=False, detail="WATCH!=FAVORABLE"),
        LongPortfolioValidationGate(code="D", passed=True, detail="BULLISH"),
        LongPortfolioValidationGate(code="SC", passed=False, detail="70.83<72"),
        LongPortfolioValidationGate(code="C", passed=True, detail="0.70"),
        LongPortfolioValidationGate(
            code="Z", passed=False, detail="watch_pullback!=buy_zone"
        ),
        LongPortfolioValidationGate(code="SET", passed=False, detail="65.50<68"),
        LongPortfolioValidationGate(code="ENT", passed=False, detail="55.00<65"),
        LongPortfolioValidationGate(code="TR", passed=True, detail="80"),
        LongPortfolioValidationGate(code="REG", passed=True, detail="allowed"),
        LongPortfolioValidationGate(code="RF", passed=False, detail="below_weekly_200w"),
    )
    item = _ProgressItem(
        symbol="PFE",
        validation_gates=gates,
        age_ok=True,
        age_detail="2.0h<=72h",
        qualified_sessions=0,
        minimum_sessions=2,
        cooldown_ok=True,
        cooldown_detail="ready",
        allocation_ok=False,
        allocation_detail="USD 0.00 remaining",
    )

    line = _format_progress_line(item)

    assert "OBJETIVO CUBIERTO" in line
    assert "veredicto en observación (requiere favorable)" in line
    assert "score general 70.83 (mínimo 72)" in line
    assert "confianza 70%" in line
    assert "precio esperando el retroceso (requiere zona de compra)" in line
    assert "sin cupo; el objetivo de cartera ya está cubierto" in line
