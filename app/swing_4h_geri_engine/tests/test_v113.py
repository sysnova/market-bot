import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from app.contracts import GeriMaturity, MarketBar
from app.swing_4h_geri_engine.tests.test_v18 import _bar, _context
from app.swing_4h_geri_engine.v113 import Swing4HGeriEngineV113


def history() -> tuple[MarketBar, ...]:
    values = [
        ("9", "11", "10"),
        ("8", "10", "9"),
        ("9", "14", "13"),
        ("12", "18", "17"),
        ("14", "17", "15"),
        ("13", "16", "14"),
        ("14", "17", "16"),
        ("12.8", "16", "14"),
    ]
    return tuple(_bar(i, *v) for i, v in enumerate(values))


def test_recent_completed_chain_wins_over_old_unbroken_support() -> None:
    result = Swing4HGeriEngineV113().analyze(_context(history(), "14"))
    assert [v.price for v in result.levels] == list(map(Decimal, ["13", "17", "12.8"]))
    assert result.maturity is GeriMaturity.IN_ZONE_4H
    assert not result.four_hour_confirmation
    assert result.levels[-1].source_at == history()[-1].timestamp


def test_latest_n1_is_shown_while_waiting_for_first_cross() -> None:
    b = (*history()[:7], _bar(7, "14", "17", "16"))
    result = Swing4HGeriEngineV113().analyze(_context(b, "16"))
    assert len(result.levels) == 1
    assert result.active_level_price == Decimal("13")
    assert result.zone_low is None


def test_latest_n3_wins_and_new_building_support_does_not_hide_it() -> None:
    b = (
        *history(),
        _bar(8, "14", "16", "15"),
        _bar(9, "13.5", "15", "14.5"),
        _bar(10, "14", "16", "15"),
        _bar(11, "13.2", "15", "14.5"),
        _bar(12, "14", "16", "15"),
    )
    engine = Swing4HGeriEngineV113()
    result = engine.analyze(_context(b, "15"))
    assert result.levels[0].price == Decimal("13.5")
    assert result.levels[-1].price == Decimal("13.2")
    assert result.levels[-1].source_at == b[11].timestamp
    prior = None
    for end in range(8, len(b) + 1):
        prior = engine.analyze(
            replace(_context(b[:end], str(b[end - 1].close)), active_structure=prior)
        )
    assert prior is not None
    assert prior.levels == result.levels


def test_fibonacci_is_bonus_without_changing_structure_or_confirmation() -> None:
    engine = Swing4HGeriEngineV113()
    result = engine.analyze(_context(history(), "14"))
    metrics = {v.name: v.value for v in result.metrics}
    assert metrics["structural_fibonacci_confluence"] is True
    assert metrics["structural_fibonacci_zone_low"] == Decimal("11.820")
    assert metrics["structural_fibonacci_zone_high"] == Decimal("14.180")
    no_fib = list(history())
    no_fib[3] = _bar(3, "12", "30", "17")
    other = engine.analyze(_context(tuple(no_fib), "14"))
    assert {v.name: v.value for v in other.metrics}["structural_fibonacci_confluence"] is False
    assert other.levels == result.levels
    assert other.maturity == result.maturity
    assert not other.four_hour_confirmation


def test_n2_close_break_confirms_and_n3_perforation_invalidates() -> None:
    engine = Swing4HGeriEngineV113()
    confirmed = (*history(), _bar(8, "14", "19", "18"))
    assert engine.analyze(_context(confirmed, "18")).four_hour_confirmation
    invalid = (*history(), _bar(8, "12", "16", "14"))
    result = engine.analyze(_context(invalid, "14"))
    assert not result.four_hour_confirmation
    assert all(v.price != Decimal("12.8") for v in result.levels)


def test_uuuu_real_bars_select_august_support_and_september_floor() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/uuuu_20260910.json").read_text())
    bars = tuple(MarketBar.model_validate(b, strict=False) for b in fixture["bars"])
    engine = Swing4HGeriEngineV113()
    result = engine.analyze(replace(_context(bars, "13.63"), symbol="UUUU"))
    assert [v.price for v in result.levels] == list(map(Decimal, ["13.61", "16.5", "13.605"]))
    assert result.levels[0].source_at.isoformat().startswith("2026-08-20T17:30")
    assert result.levels[1].source_at.isoformat().startswith("2026-08-26T13:30")
    assert result.levels[2].source_at == bars[-1].timestamp
    assert result.maturity is GeriMaturity.IN_ZONE_4H
    assert not result.four_hour_confirmation
