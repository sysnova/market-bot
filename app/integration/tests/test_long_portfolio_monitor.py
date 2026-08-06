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
    assert "[############--] 12/14" in line
    assert "SES 0/2" in line
    assert "RF=below_weekly_200w" in line
    assert "SES=0/2" in line


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

    assert "[--------------] 0/14" in line
    assert "DATA=no_long_result" in line
