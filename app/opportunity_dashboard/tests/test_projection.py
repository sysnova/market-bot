from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunitySignalReference,
    EntryOpportunityStatus,
    EntrySignalFamily,
    GeriCountertrendMaturity,
    SwingTradeMaturity,
)
from app.contracts.entry_opportunity import RecoveryExitState
from app.opportunity_dashboard import build_dashboard_snapshot, checkpoint_pnl_percent

NOW = datetime(2026, 8, 31, 15, tzinfo=UTC)


def test_recovery_management_projection_distinguishes_warning_and_pending_exit() -> None:
    state = RecoveryExitState(
        analysis_id=UUID("0199a100-0000-7000-8000-000000000777"),
        evidence_at=NOW - timedelta(hours=1),
        pivot_at=NOW - timedelta(days=2),
        avwap=Decimal("98"),
        breakout_level=Decimal("99"),
        rebound_low=Decimal("96"),
        reaction_low=Decimal("92"),
    )
    cp = _checkpoint(
        20, level=EntryMaturityLevel.L2, family=EntrySignalFamily.CORE_RECOVERY
    ).model_copy(update={"recovery_exit": state})
    item = opportunity().model_copy(update={"checkpoints": (cp,)})
    row = build_dashboard_snapshot((item,), refreshed_at=NOW)["rows"][0]
    assert row["recovery_management"]["status"] == "MONITORING"
    for changes, status in (
        ({"previous_failed_close": Decimal("97"), "previous_failed_bucket": NOW}, "WARNING"),
        (
            {
                "previous_failed_close": Decimal("97"),
                "previous_failed_bucket": NOW,
                "pending_exit_at": NOW + timedelta(minutes=1),
                "last_bar_at": NOW,
                "bar_count": 15,
            },
            "EXIT_PENDING",
        ),
    ):
        changed = cp.model_copy(update={"recovery_exit": state.model_copy(update=changes)})
        row = build_dashboard_snapshot(
            (item.model_copy(update={"checkpoints": (changed,)}),), refreshed_at=NOW
        )["rows"][0]
        assert row["recovery_management"]["status"] == status


def test_active_buy_exposes_protection_separately_from_thesis_invalidation() -> None:
    cp = _checkpoint(20, level=EntryMaturityLevel.L1, current="115").model_copy(
        update={
            "protection_stop": Decimal("105"),
            "protection_updated_at": NOW,
            "protection_rule_version": "1.0.0",
        }
    )
    item = opportunity().model_copy(update={"checkpoints": (cp,)})
    row = build_dashboard_snapshot((item,), refreshed_at=NOW)["rows"][0]
    assert Decimal(row["invalidation"]) == 90
    assert Decimal(row["protection_stop"]) == Decimal(row["effective_stop"]) == 105
    assert float(row["risk_to_invalidation_percent"]) == pytest.approx(
        100 * (115 / 105 - 1), abs=0.0001
    )


def test_closed_buy_uses_its_exit_date_and_has_no_remaining_stop_risk() -> None:
    item = opportunity().model_copy(update={"updated_at": NOW + timedelta(days=5)})
    snapshot = build_dashboard_snapshot((item,), refreshed_at=NOW + timedelta(days=5))
    row = next(r for r in snapshot["rows"] if r["state"] == "L1")
    assert row["checkpoint_status"] == "CLOSED"
    assert row["lifecycle_status"] == "OPEN"
    assert row["updated_at"] == NOW.isoformat()
    assert row["risk_to_invalidation_percent"] is None
    assert row["target_distance_percent"] is None
    assert snapshot["filters"]["statuses"] == ["CLOSED", "OPEN"]


def test_recovery_ct1_is_visible_without_a_buy_or_pnl_checkpoint() -> None:
    ref = EntryOpportunitySignalReference(
        signal_id=UUID("0199a100-0000-7002-8000-000000000001"),
        family=EntrySignalFamily.GERI_COUNTERTREND,
        current_ct=GeriCountertrendMaturity.CT1,
        peak_ct=GeriCountertrendMaturity.CT1,
        setup_id="recovery:AAPL",
        created_at=NOW,
        entry_price=Decimal("95"),
        horizons=(AnalysisHorizon.SWING,),
        policy_id="geri-countertrend",
        policy_version="1.9.0",
    )
    item = opportunity().model_copy(update={"checkpoints": (), "signal_references": (ref,)})
    rows = build_dashboard_snapshot((item,), refreshed_at=NOW)["rows"]
    assert len(rows) == 1
    assert rows[0]["state"] == "CT1"
    assert rows[0]["entry_kind"] == "REFERENCE"
    assert rows[0]["entry_price"] is None
    assert rows[0]["pnl_percent"] is None

    mixed = item.model_copy(update={"checkpoints": opportunity().checkpoints})
    snapshot = build_dashboard_snapshot((mixed,), refreshed_at=NOW)
    assert len(snapshot["rows"]) == 5
    assert snapshot["filters"]["theses"] == [
        {"value": "CORE_ENTRY", "label": "Entrada Core"},
        {"value": "GERI_COUNTERTREND", "label": "GERI Countertrend"},
    ]


def _checkpoint(
    suffix: int,
    *,
    level: EntryMaturityLevel = EntryMaturityLevel.ARMED,
    family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY,
    countertrend: GeriCountertrendMaturity | None = None,
    entry: str = "100",
    current: str = "95",
    closed: bool = False,
) -> EntryMaturityCheckpoint:
    pnl = (Decimal(current) / Decimal(entry) - Decimal("1")) * Decimal("100")
    return EntryMaturityCheckpoint(
        checkpoint_id=UUID(f"0199a100-0000-7000-8000-{suffix:012d}"),
        level=level,
        countertrend_maturity=countertrend,
        signal_family=family,
        setup_id=f"setup-{suffix}" if family is not EntrySignalFamily.CORE_ENTRY else None,
        reached_at=NOW - timedelta(minutes=suffix),
        entry_price=Decimal(entry),
        current_price=Decimal(current),
        highest_price=Decimal("103"),
        lowest_price=Decimal("94"),
        invalidation=Decimal("90"),
        status=EntryCheckpointStatus.CLOSED if closed else EntryCheckpointStatus.OPEN,
        closed_at=NOW if closed else None,
        exit_price=Decimal(current) if closed else None,
        outcome=EntryLegStatus.INVALIDATED if closed else None,
        gain_loss_percent=pnl if closed else None,
        mfe_percent=Decimal("3"),
        mae_percent=Decimal("-6"),
    )


def opportunity() -> EntryOpportunity:
    checkpoints = (
        _checkpoint(1),
        _checkpoint(2, level=EntryMaturityLevel.L1, closed=True),
        _checkpoint(
            3,
            family=EntrySignalFamily.GERI_COUNTERTREND,
            countertrend=GeriCountertrendMaturity.CT0,
        ),
        _checkpoint(
            4,
            family=EntrySignalFamily.GERI_COUNTERTREND,
            countertrend=GeriCountertrendMaturity.CT1,
        ),
    )
    return EntryOpportunity(
        opportunity_id=UUID("0199a100-0000-7001-8000-000000000001"),
        symbol="AAPL",
        status=EntryOpportunityStatus.OPEN,
        current_maturity=EntryMaturityLevel.L1,
        peak_maturity=EntryMaturityLevel.L1,
        progress_percent=Decimal("60"),
        armed_at=NOW - timedelta(hours=2),
        updated_at=NOW,
        expires_at=NOW + timedelta(days=5),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        original_price=Decimal("100"),
        current_price=Decimal("95"),
        source_analysis_ids=(UUID("0199a100-0000-7002-8000-000000000001"),),
        checkpoints=checkpoints,
    )


@pytest.mark.unit
def test_snapshot_separates_references_from_buys_and_projects_filter_dimensions() -> None:
    snapshot = build_dashboard_snapshot((opportunity(),), refreshed_at=NOW)

    rows = snapshot["rows"]
    assert {row["state"] for row in rows} == {"ARMED", "L1", "CT0", "CT1"}
    assert {row["state"] for row in rows if row["entry_kind"] == "REFERENCE"} == {
        "ARMED",
        "CT0",
    }
    assert {row["state"] for row in rows if row["entry_kind"] == "BUY"} == {"L1", "CT1"}
    assert snapshot["filters"]["statuses"] == ["CLOSED", "OPEN"]
    assert snapshot["filters"]["theses"] == [
        {"value": "CORE_ENTRY", "label": "Entrada Core"},
        {"value": "GERI_COUNTERTREND", "label": "GERI Countertrend"},
    ]


@pytest.mark.unit
def test_checkpoint_pnl_uses_audited_close_or_live_mark() -> None:
    live = _checkpoint(10, entry="100", current="97")
    closed = _checkpoint(11, entry="100", current="95", closed=True)

    assert checkpoint_pnl_percent(live) == Decimal("-3.00")
    assert checkpoint_pnl_percent(closed) == Decimal("-5.00")


@pytest.mark.unit
@pytest.mark.parametrize("closed", [False, True])
def test_swing_checkpoints_only_count_confirmed_st3_st4_as_buys(closed: bool) -> None:
    checkpoints = tuple(
        _checkpoint(index, family=EntrySignalFamily.SWING_TRADE, closed=closed).model_copy(
            update={"swing_trade_maturity": stage}
        )
        for index, stage in enumerate(SwingTradeMaturity, start=1)
    )
    item = opportunity().model_copy(update={"checkpoints": checkpoints})
    rows = build_dashboard_snapshot((item,), refreshed_at=NOW)["rows"]
    assert {row["state"]: row["entry_kind"] for row in rows} == {
        "ST1": "REFERENCE",
        "ST2": "REFERENCE",
        "ST3": "BUY",
        "ST4": "BUY",
    }
    assert all(row["pnl_percent"] == "-5.0000" for row in rows)
