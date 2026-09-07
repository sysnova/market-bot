from datetime import timedelta

from app.contracts import EntryLegStatus, SwingTradeMaturity
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.engine import EntryOpportunityEngineV11
from app.entry_opportunity_engine.tests.test_swing_trade_v4 import NOW, swing_signal


def test_explicit_rebound_exit_closes_only_matching_setup() -> None:
    engine = EntryOpportunityEngineV11(store=InMemoryEntryOpportunityStore())
    active = engine._new_swing_trade_opportunity(swing_signal(SwingTradeMaturity.ST3))
    signal = swing_signal(None, at=NOW + timedelta(minutes=15), price="96").model_copy(
        update={"reasons": ("swing_trade_rebound_exit", "rebound_acceptance_failed")}
    )
    updated, reason = engine._apply_swing_trade(active, signal)
    assert updated is not None
    assert all(leg.status != EntryLegStatus.OPEN for leg in updated.legs)
    assert reason == "swing_trade_rebound_exit"


def test_plain_maturity_loss_keeps_existing_behavior() -> None:
    engine = EntryOpportunityEngineV11(store=InMemoryEntryOpportunityStore())
    active = engine._new_swing_trade_opportunity(swing_signal(SwingTradeMaturity.ST3))
    updated, _ = engine._apply_swing_trade(
        active, swing_signal(None, at=NOW + timedelta(minutes=15))
    )
    assert updated is not None
    assert any(leg.status == EntryLegStatus.OPEN for leg in updated.legs)


def test_rebound_exit_preserves_other_open_leg_and_rejects_stale_exit() -> None:
    from uuid import uuid7

    from app.contracts import EntrySignalFamily

    engine = EntryOpportunityEngineV11(store=InMemoryEntryOpportunityStore())
    active = engine._new_swing_trade_opportunity(swing_signal(SwingTradeMaturity.ST3))
    other = active.legs[0].model_copy(
        update={
            "leg_id": uuid7(),
            "setup_id": "core:AAPL",
            "signal_family": EntrySignalFamily.CORE_ENTRY,
        }
    )
    active = active.model_copy(update={"legs": (*active.legs, other)})
    exit_signal = swing_signal(None, at=NOW + timedelta(minutes=15)).model_copy(
        update={"reasons": ("swing_trade_rebound_exit",)}
    )
    updated, _ = engine._apply_swing_trade(active, exit_signal)
    assert updated is not None and updated.legs[-1] == other
    assert updated.legs[0].status == EntryLegStatus.INVALIDATED
    stale, _ = engine._apply_swing_trade(
        active, exit_signal.model_copy(update={"created_at": NOW - timedelta(minutes=15)})
    )
    assert stale is None
