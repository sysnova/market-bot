from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    FusionState,
    FusionTransition,
    LocalAlert,
    NamedValue,
    new_uuid7,
)
from app.integration.entry_signal_adapter import (
    entry_signal_from_alert,
    entry_signal_from_alert_watch,
    entry_signal_from_fusion,
)

NOW = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_alert_materializes_watcher_trigger_as_the_canonical_core_l4_signal() -> None:
    transition = EntryWatchTransition(
        watch_id=new_uuid7(),
        symbol="TTWO",
        previous_status=EntryWatchStatus.IN_ZONE,
        status=EntryWatchStatus.TRIGGERED,
        occurred_at=NOW,
        zone_low=Decimal("236"),
        zone_high=Decimal("243"),
        invalidation=Decimal("230"),
        current_price=Decimal("243.50"),
        watch_expires_at=NOW + timedelta(days=3),
        reasons=("entry_reconfirmed",),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        source_analysis_ids=(new_uuid7(),),
    )

    signal = entry_signal_from_alert_watch(transition)

    assert signal is not None
    assert signal.family is EntrySignalFamily.CORE_ENTRY
    assert signal.maturity is EntryMaturityLevel.L4
    assert transition.transition_id in signal.source_event_ids


def test_alert_adapter_keeps_core_levels_and_analytical_families_distinct() -> None:
    core = _alert(
        kind=AlertKind.ENTRY_CONFIRMED,
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
    )
    patreon = _alert(kind=AlertKind.PATREON_CAPS_BUY)

    core_signal = entry_signal_from_alert(core)
    patreon_signal = entry_signal_from_alert(patreon)

    assert core_signal is not None
    assert core_signal.maturity is EntryMaturityLevel.L2
    assert patreon_signal is not None
    assert patreon_signal.family is EntrySignalFamily.PATREON_CAPS
    assert patreon_signal.maturity is None


def test_alert_adapter_preserves_recovery_family_setup_and_alert_owned_l2() -> None:
    assessment_id = new_uuid7()
    alert = _alert(
        kind=AlertKind.ENTRY_CONFIRMED,
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
    ).model_copy(
        update={
            "alert_id": assessment_id,
            "metrics": (
                NamedValue(name="current_price", value=Decimal("243.50")),
                NamedValue(name="buy_zone_low", value=Decimal("243.39")),
                NamedValue(name="buy_zone_high", value=Decimal("243.50")),
                NamedValue(name="invalidation", value=Decimal("238.59")),
                NamedValue(name="target", value=Decimal("251.08")),
                NamedValue(
                    name="entry_signal_family",
                    value=EntrySignalFamily.CORE_RECOVERY.value,
                ),
                NamedValue(name="entry_maturity", value=EntryMaturityLevel.L2.value),
                NamedValue(name="entry_setup_id", value="recovery:ttwo"),
                NamedValue(name="entry_setup_policy_version", value="1.1.0"),
            ),
        }
    )

    signal = entry_signal_from_alert(alert)

    assert signal is not None
    assert signal.signal_id == assessment_id
    assert signal.family is EntrySignalFamily.CORE_RECOVERY
    assert signal.maturity is EntryMaturityLevel.L2
    assert signal.setup_id == "recovery:ttwo"
    assert signal.policy_version == "1.1.0"


def test_entry_watch_local_alert_is_not_emitted_as_a_second_l4() -> None:
    alert = _alert(
        kind=AlertKind.ENTRY_WATCH,
        title="TTWO ENTRY TRIGGERED",
        horizons=(
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
    )

    assert entry_signal_from_alert(alert) is None


def test_early_intraday_watch_never_materializes_a_core_entry_signal() -> None:
    alert = _alert(
        kind=AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION,
        title="NVO EARLY INTRADAY WITHOUT CONFIRMATION",
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
    )

    assert entry_signal_from_alert(alert) is None


def test_fusion_confirmation_becomes_its_own_signal_family() -> None:
    transition = FusionTransition(
        assessment_id=new_uuid7(),
        symbol="NVO",
        occurred_at=NOW,
        engine_version="0.3.0",
        previous_state=FusionState.ARMED,
        state=FusionState.BUY_CONFIRMED,
        score=Decimal("81"),
        trigger_price=Decimal("53"),
        entry_price=Decimal("52.50"),
        invalidation=Decimal("50"),
        target_price=Decimal("58"),
        reward_risk_ratio=Decimal("2.2"),
        reasons=("all_gates_confirmed",),
        context_hash="sha256:" + "a" * 64,
    )

    signal = entry_signal_from_fusion(transition)

    assert signal is not None
    assert signal.family is EntrySignalFamily.SIGNAL_FUSION
    assert signal.maturity is None


def _alert(
    *,
    kind: AlertKind,
    title: str = "NVO analytical buy",
    horizons: tuple[AnalysisHorizon, ...] = (AnalysisHorizon.LONG_TERM,),
) -> LocalAlert:
    return LocalAlert(
        symbol="NVO",
        created_at=NOW,
        severity=AlertSeverity.ACTION,
        title=title,
        message="analytical entry only",
        horizons=horizons,
        component_analysis_ids=(new_uuid7(),),
        metrics=(
            NamedValue(name="current_price", value=Decimal("52.50")),
            NamedValue(name="buy_zone_low", value=Decimal("51")),
            NamedValue(name="buy_zone_high", value=Decimal("53")),
            NamedValue(name="invalidation", value=Decimal("49")),
            NamedValue(name="target", value=Decimal("58")),
        ),
        score=Decimal("80"),
        reasons=("confirmed",),
        deduplication_key=f"test:{kind.value}",
        kind=kind,
    )
