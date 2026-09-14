from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunitySignalReference,
    EntryOpportunitySourceCursor,
    EntryOpportunityStatus,
    EntrySignalFamily,
    GeriCountertrendMaturity,
)


def countertrend_observation(stage: GeriCountertrendMaturity) -> EntryOpportunity:
    now = datetime(2026, 8, 6, 14, tzinfo=UTC)
    return EntryOpportunity(
        symbol="AAPL",
        status=EntryOpportunityStatus.ARMED,
        current_maturity=EntryMaturityLevel.ARMED,
        peak_maturity=EntryMaturityLevel.ARMED,
        progress_percent=Decimal("20"),
        armed_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=10),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        original_price=Decimal("100"),
        current_price=Decimal("100"),
        source_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
        primary_signal_family=EntrySignalFamily.GERI_COUNTERTREND,
        signal_references=(
            EntryOpportunitySignalReference(
                signal_id=UUID("0195f3a5-9000-7000-8000-000000000012"),
                family=EntrySignalFamily.GERI_COUNTERTREND,
                policy_id="geri-countertrend",
                policy_version="1.13.0",
                setup_id="geri-countertrend:AAPL:test",
                created_at=now,
                entry_price=Decimal("100"),
                horizons=(AnalysisHorizon.SWING,),
                current_ct=stage,
                peak_ct=stage,
            ),
        ),
        checkpoints=(),
    )


@pytest.mark.parametrize("stage", [GeriCountertrendMaturity.CT0, GeriCountertrendMaturity.CT1])
def test_countertrend_observation_roundtrips_without_buy_checkpoints(
    stage: GeriCountertrendMaturity,
) -> None:
    observation = countertrend_observation(stage)
    event = EntryOpportunityEvent(
        occurred_at=observation.armed_at,
        opportunity=observation,
        reasons=("countertrend_observation_only",),
    )
    restored = EntryOpportunityEvent.model_validate_json(event.model_dump_json())
    assert restored.opportunity.checkpoints == ()


@pytest.mark.parametrize("mutation", ["open", "core", "maturity", "entered_ct", "entered_leg"])
def test_empty_checkpoints_cannot_hide_a_buy(mutation: str) -> None:
    observation = countertrend_observation(GeriCountertrendMaturity.CT1)
    changes: dict[str, object] = {}
    if mutation == "open":
        changes["status"] = EntryOpportunityStatus.OPEN
    elif mutation == "core":
        changes["primary_signal_family"] = EntrySignalFamily.CORE_ENTRY
    elif mutation == "maturity":
        changes["current_maturity"] = EntryMaturityLevel.L1
    elif mutation == "entered_ct":
        changes["signal_references"] = (
            observation.signal_references[0].model_copy(
                update={
                    "current_ct": GeriCountertrendMaturity.CT2,
                    "peak_ct": GeriCountertrendMaturity.CT2,
                }
            ),
        )
    else:
        changes["legs"] = (
            EntryHorizonLeg(
                horizon=AnalysisHorizon.SWING,
                status=EntryLegStatus.OPEN,
                opened_at=observation.armed_at,
                entry_price=Decimal("100"),
                current_price=Decimal("100"),
                highest_price=Decimal("100"),
                lowest_price=Decimal("100"),
                invalidation=Decimal("90"),
            ),
        )
    broken = observation.model_copy(update=changes)
    with pytest.raises(ValueError, match="checkpoints"):
        EntryOpportunity.model_validate_json(broken.model_dump_json())


def test_leg_owner_is_optional_for_legacy_and_roundtrips_when_present() -> None:
    legacy = {
        "horizon": AnalysisHorizon.SWING,
        "status": EntryLegStatus.WATCHING,
        "current_price": Decimal("100"),
        "highest_price": Decimal("100"),
        "lowest_price": Decimal("100"),
        "invalidation": Decimal("90"),
    }
    assert EntryHorizonLeg.model_validate(legacy).signal_family is None
    owned = EntryHorizonLeg.model_validate(
        {**legacy, "signal_family": EntrySignalFamily.CORE_ENTRY}
    )
    assert EntryHorizonLeg.model_validate_json(owned.model_dump_json()).signal_family is (
        EntrySignalFamily.CORE_ENTRY
    )


@pytest.mark.unit
def test_entry_opportunity_accepts_legacy_snapshot_without_source_cursors() -> None:
    now = datetime(2026, 8, 6, 14, tzinfo=UTC)
    opportunity = EntryOpportunity(
        opportunity_id=UUID("0195f3a5-9000-7000-8000-000000000090"),
        symbol="AAPL",
        status=EntryOpportunityStatus.ARMED,
        current_maturity=EntryMaturityLevel.ARMED,
        peak_maturity=EntryMaturityLevel.ARMED,
        progress_percent=Decimal("20"),
        original_watch_id=UUID("0195f3a5-9000-7000-8000-000000000021"),
        armed_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=56),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        original_price=Decimal("100"),
        current_price=Decimal("100"),
        source_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
        checkpoints=(
            EntryMaturityCheckpoint(
                checkpoint_id=UUID("0195f3a5-9000-7000-8000-000000000091"),
                level=EntryMaturityLevel.ARMED,
                reached_at=now,
                entry_price=Decimal("100"),
                current_price=Decimal("100"),
                highest_price=Decimal("100"),
                lowest_price=Decimal("100"),
                invalidation=Decimal("90"),
            ),
        ),
    )
    legacy_payload = opportunity.model_dump(mode="python")
    legacy_payload.pop("source_cursors")

    restored = EntryOpportunity.model_validate(legacy_payload)

    assert restored.source_cursors == ()
    assert restored.primary_signal_family is EntrySignalFamily.CORE_ENTRY
    assert restored.signal_references == ()


@pytest.mark.unit
def test_entry_opportunity_source_cursor_is_typed_and_stable() -> None:
    now = datetime(2026, 8, 6, 14, tzinfo=UTC)
    cursor = EntryOpportunitySourceCursor(
        source="ENTRY_WATCHER",
        event_id=UUID("0195f3a5-9000-7000-8000-000000000081"),
        occurred_at=now,
    )

    assert cursor.source == "ENTRY_WATCHER"


@pytest.mark.unit
def test_entry_opportunity_rejects_duplicate_source_cursors() -> None:
    now = datetime(2026, 8, 6, 14, tzinfo=UTC)
    cursor = EntryOpportunitySourceCursor(
        source="ENTRY_WATCHER",
        event_id=UUID("0195f3a5-9000-7000-8000-000000000081"),
        occurred_at=now,
    )
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("0195f3a5-9000-7000-8000-000000000091"),
        level=EntryMaturityLevel.ARMED,
        reached_at=now,
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
        invalidation=Decimal("90"),
    )

    with pytest.raises(ValueError, match="source cursors must be unique"):
        EntryOpportunity(
            opportunity_id=UUID("0195f3a5-9000-7000-8000-000000000090"),
            symbol="AAPL",
            status=EntryOpportunityStatus.ARMED,
            current_maturity=EntryMaturityLevel.ARMED,
            peak_maturity=EntryMaturityLevel.ARMED,
            progress_percent=Decimal("20"),
            armed_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=56),
            zone_low=Decimal("95"),
            zone_high=Decimal("100"),
            invalidation=Decimal("90"),
            original_price=Decimal("100"),
            current_price=Decimal("100"),
            source_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
            source_cursors=(cursor, cursor),
            checkpoints=(checkpoint,),
        )
