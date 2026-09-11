from dataclasses import replace
from decimal import Decimal

import pytest

from app.contracts import GeriAssessment, GeriMaturity, MarketBar
from app.swing_4h_geri_engine.tests.test_v18 import _bar, _context
from app.swing_4h_geri_engine.v112 import Swing4HGeriEngineV112


def bars() -> tuple[MarketBar, ...]:
    values = [
        ("11", "13", "12"),
        ("10", "12", "11"),
        ("11", "14", "13"),
        ("12", "16", "15"),
        ("12", "15", "13"),
        ("11", "14", "12"),
        ("10", "13", "12"),
        ("9", "12", "11"),
    ]
    return tuple(_bar(i, *v) for i, v in enumerate(values))


def test_n3_exists_before_n2_break_and_zone_is_full_range() -> None:
    result = Swing4HGeriEngineV112().analyze(_context(bars(), "11"))
    assert [v.price for v in result.levels] == list(map(Decimal, ["10", "16", "9"]))
    assert result.levels[1].broken_at is None
    assert result.levels[2].source_at == bars()[-1].timestamp
    assert (result.zone_low, result.zone_high) == (Decimal("9"), Decimal("16"))
    assert result.maturity == GeriMaturity.IN_ZONE_4H
    assert not result.four_hour_confirmation
    assert GeriAssessment.model_validate_json(result.model_dump_json()).levels == result.levels


def test_only_later_four_hour_close_above_n2_confirms() -> None:
    engine = Swing4HGeriEngineV112()
    wick = (*bars(), _bar(8, "11", "17", "15"))
    assert not engine.analyze(_context(wick, "17")).four_hour_confirmation
    confirmed = (*wick, _bar(9, "14", "18", "17"))
    result = engine.analyze(_context(confirmed, "17"))
    assert result.four_hour_confirmation
    assert result.maturity == GeriMaturity.L3
    assert result.levels[1].broken_at == confirmed[-1].timestamp


def test_first_wick_cross_fixes_floor_and_later_lower_low_invalidates() -> None:
    earlier = (*bars()[:6], _bar(6, "9.5", "13", "12"), bars()[7])
    result = Swing4HGeriEngineV112().analyze(_context(earlier, "11"))
    assert result.active_level_price == Decimal("9.5")
    assert result.maturity == GeriMaturity.INVALIDATED
    assert not result.four_hour_confirmation


def test_persisted_structure_survives_rolling_window() -> None:
    engine = Swing4HGeriEngineV112()
    prior = engine.analyze(_context(bars(), "11"))
    later = (*bars()[1:], _bar(8, "11", "17", "16.5"))
    context = replace(_context(later, "16.5"), active_structure=prior)
    result = engine.analyze(context)
    assert result.levels[0].source_at == prior.levels[0].source_at
    assert result.four_hour_confirmation


def test_legacy_contract_still_rejects_unbroken_middle_level() -> None:
    result = Swing4HGeriEngineV112().analyze(_context(bars(), "11"))
    payload = result.model_dump()
    payload["structure_policy"] = "alternating_breaks"
    with pytest.raises(ValueError, match="only the active"):
        GeriAssessment.model_validate(payload)


def test_new_support_after_invalidation_matches_incremental_replay() -> None:
    engine = Swing4HGeriEngineV112()
    history = (
        *bars(),
        _bar(8, "8", "11", "10"),
        _bar(9, "9", "13", "12"),
        _bar(10, "10", "15", "14"),
        _bar(11, "7", "12", "10"),
    )
    prior = None
    for end in range(8, len(history) + 1):
        ctx = _context(history[:end], str(history[end - 1].close))
        prior = engine.analyze(replace(ctx, active_structure=prior))
    result = engine.analyze(_context(history, "10"))
    assert prior is not None
    assert prior.levels == result.levels
    assert [v.price for v in result.levels] == list(map(Decimal, ["8", "15", "7"]))


def test_no_growth_cannot_seed_a_support() -> None:
    flat = tuple(_bar(i, "10", "12", "11") for i in range(8))
    with pytest.raises(ValueError, match="clean support"):
        Swing4HGeriEngineV112().analyze(_context(flat, "11"))
