from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.alert_engine.confirmed import BuyMaturity
from app.contracts import (
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
)
from app.integration.confirmed_signal_projection import project_confirmed_signal

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)


def signal(
    family: EntrySignalFamily,
    *,
    maturity: EntryMaturityLevel | None = None,
) -> EntrySignal:
    return EntrySignal(
        family=family,
        maturity=maturity,
        symbol="TGT",
        created_at=NOW,
        setup_id=f"test:{family.value}",
        entry_price=Decimal("105"),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        zone_low=Decimal("103"),
        zone_high=Decimal("105"),
        invalidation=Decimal("99"),
        targets=(Decimal("115"),),
        policy_id="test-policy",
        policy_version="1.0.0",
        reasons=("confirmed",),
    )


@pytest.mark.parametrize(
    ("maturity", "sound"),
    [
        (EntryMaturityLevel.L1, BuyMaturity.TACTICAL_RECOVERY),
        (EntryMaturityLevel.L2, BuyMaturity.SWING_CONFIRMED),
        (EntryMaturityLevel.L3, BuyMaturity.HIGH_CONVICTION),
        (EntryMaturityLevel.L4, BuyMaturity.FULLY_MATURED),
    ],
)
def test_core_confirmations_keep_real_l1_l4_maturity(
    maturity: EntryMaturityLevel,
    sound: BuyMaturity,
) -> None:
    projection = project_confirmed_signal(
        signal(EntrySignalFamily.CORE_ENTRY, maturity=maturity), color=False
    )

    assert projection is not None
    assert projection.sound_maturity is sound
    assert f"CORE ENTRY {maturity.value}" in projection.text


def test_armed_and_in_zone_are_not_confirmed_buy_projections() -> None:
    for maturity in (EntryMaturityLevel.ARMED, EntryMaturityLevel.IN_ZONE):
        assert (
            project_confirmed_signal(
                signal(EntrySignalFamily.CORE_ENTRY, maturity=maturity), color=False
            )
            is None
        )


def test_recovery_uses_its_real_alert_assigned_maturity() -> None:
    projection = project_confirmed_signal(
        signal(EntrySignalFamily.CORE_RECOVERY, maturity=EntryMaturityLevel.L2),
        color=False,
    )

    assert projection is not None
    assert projection.sound_maturity is BuyMaturity.SWING_CONFIRMED
    assert "CORE RECOVERY L2" in projection.text


@pytest.mark.parametrize(
    ("family", "label"),
    [
        (EntrySignalFamily.PATREON_CAPS, "PATREON CAPS CONFIRMED"),
        (EntrySignalFamily.LONG_PORTFOLIO, "LONG PORTFOLIO BUY"),
        (EntrySignalFamily.SIGNAL_FUSION, "SIGNAL FUSION CONFIRMED"),
    ],
)
def test_final_analytical_families_are_confirmed_without_fake_l4(
    family: EntrySignalFamily,
    label: str,
) -> None:
    projection = project_confirmed_signal(signal(family), color=False)

    assert projection is not None
    assert projection.sound_maturity is None
    assert label in projection.text
    assert "L4" not in projection.text


def test_portfolio_flow_entry_signal_remains_a_manual_local_alert() -> None:
    assert project_confirmed_signal(signal(EntrySignalFamily.PORTFOLIO_FLOW), color=False) is None
