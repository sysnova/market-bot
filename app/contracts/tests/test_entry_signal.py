from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    entry_signal_subject,
)

NOW = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_entry_signal_is_source_and_engine_version_agnostic() -> None:
    signal = EntrySignal(
        family=EntrySignalFamily.CORE_ENTRY,
        maturity=EntryMaturityLevel.L4,
        symbol="TTWO",
        created_at=NOW,
        setup_id="watch:019fa642-3330-7b79-9ad6-939d0396dbf5",
        entry_price=Decimal("243.39"),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        zone_low=Decimal("240.00"),
        zone_high=Decimal("243.39"),
        invalidation=Decimal("238.59"),
        targets=(Decimal("251.07"),),
        policy_id="core-entry",
        policy_version="1.0.0",
        reasons=("fresh_intraday_reconfirmation",),
    )

    assert signal.signal_id.version == 7
    assert signal.family is EntrySignalFamily.CORE_ENTRY
    assert "engine_version" not in EntrySignal.model_fields
    assert entry_signal_subject(signal.family, signal.symbol) == (
        "marketbot.v1.entry-signal.CORE_ENTRY.TTWO"
    )


def test_non_core_signal_does_not_reuse_core_l4_scale() -> None:
    signal = EntrySignal(
        family=EntrySignalFamily.PATREON_CAPS,
        symbol="NVO",
        created_at=NOW,
        setup_id="patreon:NVO:2026-08-09",
        entry_price=Decimal("52.25"),
        horizons=(AnalysisHorizon.SWING,),
        policy_id="patreon-caps",
        policy_version="1.1.0",
        reasons=("base_recovery_confirmed",),
    )

    assert signal.maturity is None


def test_core_signal_requires_maturity_and_coherent_zone() -> None:
    with pytest.raises(ValidationError, match="core entry signals require maturity"):
        EntrySignal(
            family=EntrySignalFamily.CORE_RECOVERY,
            symbol="TTWO",
            created_at=NOW,
            setup_id="recovery:ttwo",
            entry_price=Decimal("246.50"),
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            policy_id="core-recovery",
            policy_version="1.0.0",
            reasons=("reclaimed_original_entry",),
        )

    with pytest.raises(ValidationError, match="invalidation < zone_low <= zone_high"):
        EntrySignal(
            family=EntrySignalFamily.CORE_ENTRY,
            maturity=EntryMaturityLevel.L2,
            symbol="TTWO",
            created_at=NOW,
            setup_id="watch:ttwo",
            entry_price=Decimal("243.39"),
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            zone_low=Decimal("240"),
            zone_high=Decimal("239"),
            invalidation=Decimal("238"),
            policy_id="core-entry",
            policy_version="1.0.0",
            reasons=("confirmed",),
        )
