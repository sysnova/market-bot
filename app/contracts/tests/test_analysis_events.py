import pytest

from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    BarTimeframe,
    EntryWatchStatus,
    PatreonCapsState,
    analysis_result_subject,
    entry_watch_transition_subject,
    local_alert_subject,
    market_bar_subject,
    patreon_caps_assessment_subject,
    patreon_caps_transition_subject,
    service_health_subject,
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
    assert service_health_subject("swing-v2") == "marketbot.v1.service.health.swing-v2"
    assert entry_watch_transition_subject(EntryWatchStatus.IN_ZONE, "BRK.B") == (
        "marketbot.v1.entry-watch.transition.IN_ZONE.BRK_B"
    )
    assert patreon_caps_assessment_subject("BRK.B") == (
        "marketbot.v1.patreon-caps.assessment.BRK_B"
    )
    assert patreon_caps_transition_subject(PatreonCapsState.SUPPORT_TEST, "BRK.B") == (
        "marketbot.v1.patreon-caps.transition.SUPPORT_TEST.BRK_B"
    )


def test_analysis_subjects_reject_untrusted_tokens() -> None:
    with pytest.raises(ValueError, match="symbol"):
        market_bar_subject(BarTimeframe.MINUTE_1, "AAPL.>")
