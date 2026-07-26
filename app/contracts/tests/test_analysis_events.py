import pytest

from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    BarTimeframe,
    analysis_result_subject,
    local_alert_subject,
    market_bar_subject,
)


def test_stable_analysis_subjects_are_partitioned_by_kind_and_symbol() -> None:
    assert market_bar_subject(BarTimeframe.MINUTE_1, "BRK.B") == (
        "marketbot.v1.market.bar.1Min.BRK_B"
    )
    assert analysis_result_subject(AnalysisHorizon.SWING, "AAPL") == (
        "marketbot.v1.analysis.result.SWING.AAPL"
    )
    assert local_alert_subject(AlertSeverity.ACTION, "NVDA") == (
        "marketbot.v1.alert.local.ACTION.NVDA"
    )


def test_analysis_subjects_reject_untrusted_tokens() -> None:
    with pytest.raises(ValueError, match="symbol"):
        market_bar_subject(BarTimeframe.MINUTE_1, "AAPL.>")
