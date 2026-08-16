from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityStatus,
)
from app.integration.entry_opportunity_report import (
    build_entry_opportunity_report,
    render_entry_opportunity_evidence_audit,
)

NOW = datetime(2026, 8, 6, 18, tzinfo=UTC)


def opportunity(*, closed: bool, suffix: int) -> EntryOpportunity:
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID(f"01987e76-3c00-7000-8000-{suffix:012d}"),
        level=EntryMaturityLevel.L1 if closed else EntryMaturityLevel.L3,
        reached_at=NOW - timedelta(hours=1),
        entry_price=Decimal("100"),
        current_price=Decimal("105") if closed else Decimal("102"),
        highest_price=Decimal("108"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("92"),
        status=EntryCheckpointStatus.CLOSED if closed else EntryCheckpointStatus.OPEN,
        closed_at=NOW if closed else None,
        exit_price=Decimal("105") if closed else None,
        outcome=EntryLegStatus.TIME_EXIT if closed else None,
        gain_loss_percent=Decimal("5") if closed else None,
        mfe_percent=Decimal("8"),
        mae_percent=Decimal("-2"),
    )
    return EntryOpportunity(
        opportunity_id=UUID(f"01987e76-3c00-7001-8000-{suffix:012d}"),
        symbol="MSFT" if closed else "AAPL",
        status=EntryOpportunityStatus.CLOSED if closed else EntryOpportunityStatus.CONFIRMING,
        current_maturity=checkpoint.level,
        peak_maturity=checkpoint.level,
        progress_percent=Decimal("60") if closed else Decimal("90"),
        armed_at=NOW - timedelta(hours=2),
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        closed_at=NOW if closed else None,
        close_reason=EntryCloseReason.ALL_HORIZONS_CLOSED if closed else None,
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        original_price=Decimal("105"),
        current_price=checkpoint.current_price,
        source_analysis_ids=(UUID("01987e76-3c00-7002-8000-000000000001"),),
        checkpoints=(checkpoint,),
    )


@pytest.mark.unit
def test_report_lists_open_progress_and_l1_success_rate() -> None:
    report = build_entry_opportunity_report(
        (opportunity(closed=False, suffix=1), opportunity(closed=True, suffix=2))
    )

    assert report["summary"] == {"opportunities": 2, "open": 1, "closed": 1}
    assert report["open_trades"][0]["progress_bar"] == "[#########-]"
    assert report["maturity_outcomes"]["L1"]["wins"] == 1
    assert report["maturity_outcomes"]["L1"]["success_rate_percent"] == "100.00"
    assert report["signal_family_outcomes"]["CORE_ENTRY"]["wins"] == 1


@pytest.mark.unit
def test_report_separates_tracking_references_from_actionable_entries() -> None:
    base = opportunity(closed=False, suffix=3)
    armed = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7003-8000-000000000001"),
        level=EntryMaturityLevel.ARMED,
        reached_at=NOW - timedelta(hours=3),
        entry_price=Decimal("110"),
        current_price=Decimal("99"),
        highest_price=Decimal("112"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("90"),
        mfe_percent=Decimal("1.8182"),
        mae_percent=Decimal("-10.9091"),
        return_15m=Decimal("-2"),
        return_30m=Decimal("-4"),
        return_60m=Decimal("-6"),
    )
    in_zone = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7003-8000-000000000002"),
        level=EntryMaturityLevel.IN_ZONE,
        reached_at=NOW - timedelta(hours=2),
        entry_price=Decimal("100"),
        current_price=Decimal("99"),
        highest_price=Decimal("103"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("90"),
        mfe_percent=Decimal("3"),
        mae_percent=Decimal("-2"),
        return_15m=Decimal("-1"),
        return_30m=Decimal("1"),
        return_60m=Decimal("-1"),
    )
    l1 = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7003-8000-000000000003"),
        level=EntryMaturityLevel.L1,
        reached_at=NOW - timedelta(hours=1),
        entry_price=Decimal("98"),
        current_price=Decimal("99"),
        highest_price=Decimal("101"),
        lowest_price=Decimal("97"),
        invalidation=Decimal("90"),
        mfe_percent=Decimal("3.0612"),
        mae_percent=Decimal("-1.0204"),
        return_15m=Decimal("1.0204"),
    )
    audited = base.model_copy(
        update={
            "current_maturity": EntryMaturityLevel.L1,
            "peak_maturity": EntryMaturityLevel.L1,
            "current_price": Decimal("99"),
            "checkpoints": (armed, in_zone, l1),
        }
    )

    audit = build_entry_opportunity_report((audited,))["evidence_audit"]

    assert audit["sample"]["tracking_references"] == 2
    assert audit["sample"]["actionable_entries"] == 1
    assert audit["snapshot"]["tracking"]["negative"] == 2
    assert audit["snapshot"]["actionable"]["negative"] == 0
    assert audit["fixed_horizons"]["tracking"]["30m"]["positive"] == 1
    assert audit["fixed_horizons"]["tracking"]["60m"]["negative"] == 2
    assert audit["pullback_entry_improvement"][0] == {
        "symbol": "AAPL",
        "armed_reference_price": "110",
        "in_zone_reference_price": "100",
        "entry_price_improvement_percent": "9.0909",
        "snapshot_advantage_percent": "9.0000",
        "marks_comparable": True,
    }
    in_zone_evidence = next(
        item for item in audit["negative_evidence"] if item["level"] == "IN_ZONE"
    )
    assert "TEMPORARY_RECOVERY_GAVE_BACK" in in_zone_evidence["classifications"]


@pytest.mark.unit
def test_negative_evidence_explains_giveback_persistence_and_censoring() -> None:
    base = opportunity(closed=False, suffix=4)
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7004-8000-000000000001"),
        level=EntryMaturityLevel.ARMED,
        reached_at=NOW - timedelta(hours=1),
        entry_price=Decimal("100"),
        current_price=Decimal("97"),
        highest_price=Decimal("102"),
        lowest_price=Decimal("96"),
        invalidation=Decimal("90"),
        mfe_percent=Decimal("2"),
        mae_percent=Decimal("-4"),
        return_15m=Decimal("-1"),
        return_30m=Decimal("-2"),
    )
    audited = base.model_copy(
        update={
            "current_maturity": EntryMaturityLevel.ARMED,
            "peak_maturity": EntryMaturityLevel.ARMED,
            "current_price": Decimal("97"),
            "checkpoints": (checkpoint,),
        }
    )

    audit = build_entry_opportunity_report((audited,))["evidence_audit"]

    evidence = audit["negative_evidence"][0]
    assert evidence["role"] == "TRACKING_REFERENCE"
    assert evidence["snapshot_return_percent"] == "-3.0000"
    assert evidence["observed_fixed_horizons"] == 2
    assert evidence["classifications"] == [
        "OPEN_RIGHT_CENSORED",
        "GAVE_BACK_POSITIVE_EXCURSION",
        "NEGATIVE_AT_ALL_OBSERVED_HORIZONS",
    ]
    assert "no hay entradas L1-L4" in " ".join(audit["limitations"])


@pytest.mark.unit
def test_human_audit_labels_references_as_non_trades() -> None:
    base = opportunity(closed=False, suffix=5)
    checkpoint = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7005-8000-000000000001"),
        level=EntryMaturityLevel.ARMED,
        reached_at=NOW,
        entry_price=Decimal("100"),
        current_price=Decimal("99"),
        highest_price=Decimal("101"),
        lowest_price=Decimal("98"),
        invalidation=Decimal("90"),
        mfe_percent=Decimal("1"),
        mae_percent=Decimal("-2"),
    )
    audited = base.model_copy(
        update={
            "current_maturity": EntryMaturityLevel.ARMED,
            "peak_maturity": EntryMaturityLevel.ARMED,
            "current_price": Decimal("99"),
            "checkpoints": (checkpoint,),
        }
    )

    rendered = render_entry_opportunity_evidence_audit(
        build_entry_opportunity_report((audited,))["evidence_audit"]
    )

    assert "REFERENCIAS (NO SON COMPRAS)" in rendered
    assert "ENTRADAS ACCIONABLES L1-L4" in rendered
    assert "AAPL ARMED" in rendered


@pytest.mark.unit
def test_pullback_comparison_does_not_compare_stale_checkpoint_marks() -> None:
    base = opportunity(closed=False, suffix=6)
    armed = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7006-8000-000000000001"),
        level=EntryMaturityLevel.ARMED,
        reached_at=NOW - timedelta(hours=2),
        entry_price=Decimal("110"),
        current_price=Decimal("111"),
        highest_price=Decimal("111"),
        lowest_price=Decimal("109"),
        invalidation=Decimal("90"),
    )
    in_zone = EntryMaturityCheckpoint(
        checkpoint_id=UUID("01987e76-3c00-7006-8000-000000000002"),
        level=EntryMaturityLevel.IN_ZONE,
        reached_at=NOW - timedelta(hours=1),
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
        invalidation=Decimal("90"),
    )
    audited = base.model_copy(update={"checkpoints": (armed, in_zone)})

    comparison = build_entry_opportunity_report((audited,))["evidence_audit"][
        "pullback_entry_improvement"
    ][0]

    assert comparison["entry_price_improvement_percent"] == "9.0909"
    assert comparison["snapshot_advantage_percent"] is None
    assert comparison["marks_comparable"] is False
