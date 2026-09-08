from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, TradeSide
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.recovery import RecoveryRules, fibonacci_confluence, recovery_metrics
from app.swing_4h_geri_engine.tactical_levels import directional_rr, directional_target
from app.swing_4h_geri_engine.tests.test_v18 import _bar
from app.swing_4h_geri_engine.v19 import Swing4HGeriEngineV19


def context() -> Swing4HGeriContext:
    bars = tuple(
        _bar(i, *v)
        for i, v in enumerate(
            (
                ("95", "103", "100"),
                ("97", "110", "105"),
                ("92", "104", "96"),
                ("91", "101", "94"),
                ("88", "100", "92"),
                ("85", "98", "90"),
                ("80", "94", "84"),
                ("82", "92", "88"),
            )
        )
    )
    start = bars[-1].timestamp + timedelta(hours=4)
    fast = tuple(
        _bar(i, *v).model_copy(
            update={
                "timeframe": BarTimeframe.MINUTE_15,
                "timestamp": start + timedelta(minutes=15 * i),
            }
        )
        for i, v in enumerate((("88", "92", "91"), ("98", "100", "99.8"), ("99", "101", "100.2")))
    )
    return Swing4HGeriContext(
        symbol="HUT",
        bars=bars,
        confirmation_bars=fast,
        current_price=Decimal("100.2"),
        as_of=fast[-1].timestamp + timedelta(minutes=15),
        current_price_at=fast[-1].timestamp + timedelta(minutes=15),
    )


def values(c: Swing4HGeriContext) -> dict[str, object]:
    return {m.name: m.value for m in recovery_metrics(c, RecoveryRules())}


def test_recovery_long_acceptance_is_independent_of_geri_direction() -> None:
    m = values(context())
    assert m["countertrend_side"] == TradeSide.LONG
    assert m["countertrend_maturity"] == "CT2"
    assert m["countertrend_eligible"] is True
    assert m["countertrend_invalidation"] < Decimal("100.2") < m["countertrend_target"]


def test_breakout_without_acceptance_does_not_buy() -> None:
    c = context()
    m = values(
        replace(c, confirmation_bars=c.confirmation_bars[:-1], current_price=Decimal("99.8"))
    )
    assert m["countertrend_maturity"] == "CT1"
    assert m["countertrend_fast_confirmation"] is False


def test_floor_failure_invalidates_recovery_even_if_price_rebounds() -> None:
    c = context()
    failed = c.confirmation_bars[-1].model_copy(update={"low": Decimal("79")})
    m = values(replace(c, confirmation_bars=(*c.confirmation_bars[:-1], failed)))
    assert m["countertrend_eligible"] is False
    assert m["countertrend_state"] == "INVALIDATED"


@pytest.mark.parametrize(
    "side,stop,target",
    [
        (TradeSide.SHORT, "105", "110"),
        (TradeSide.SHORT, "95", "90"),
        (TradeSide.LONG, "95", "90"),
        (TradeSide.LONG, "105", "110"),
        (TradeSide.SHORT, "105", "100"),
    ],
)
def test_bad_directional_geometry_never_has_positive_rr(
    side: TradeSide,
    stop: str,
    target: str,
) -> None:
    assert directional_rr(side, Decimal("100"), Decimal(stop), Decimal(target)) is None


def test_target_selects_nearest_obstacle_on_correct_side() -> None:
    levels = (Decimal("90"), Decimal("95"), Decimal("105"), Decimal("110"))
    assert directional_target(TradeSide.SHORT, Decimal("100"), levels) == Decimal("95")
    assert directional_target(TradeSide.LONG, Decimal("100"), levels) == Decimal("105")
    assert directional_target(TradeSide.SHORT, Decimal("80"), levels) is None


def test_daily_future_evidence_is_rejected() -> None:
    c = context()
    daily = c.bars[-1].model_copy(
        update={
            "timeframe": BarTimeframe.DAY_1,
            "timestamp": datetime(2027, 1, 1, tzinfo=UTC),
        }
    )
    with pytest.raises(ValueError, match="daily"):
        values(replace(c, daily_bars=(daily,)))


def test_versioned_engine_preserves_accepted_levels_after_more_bars() -> None:
    c = context()
    engine = Swing4HGeriEngineV19()
    initial = engine.analyze(c)
    later = c.confirmation_bars[-1].model_copy(
        update={
            "timestamp": c.confirmation_bars[-1].timestamp + timedelta(minutes=15),
            "close": Decimal("100.3"),
        }
    )
    after = engine.analyze(
        replace(
            c,
            active_structure=initial,
            confirmation_bars=(*c.confirmation_bars, later),
            current_price=later.close,
            as_of=later.timestamp + timedelta(minutes=15),
            current_price_at=later.timestamp + timedelta(minutes=15),
        )
    )
    before, final = ({m.name: m.value for m in a.metrics} for a in (initial, after))
    for key in (
        "countertrend_invalidation",
        "countertrend_target",
        "countertrend_level_source_at",
        "countertrend_accepted_at",
    ):
        assert before[key] == final[key]
    assert final["countertrend_side"] == TradeSide.LONG


def test_fibonacci_requires_current_price_support_and_confirmed_ascending_anchors() -> None:
    data = (
        ("92", "100", "96"),
        ("90", "99", "95"),
        ("94", "104", "100"),
        ("99", "110", "105"),
        ("98", "105", "100"),
        ("98", "104", "100"),
        ("99", "104", "102"),
    )
    bars = tuple(_bar(i, *v) for i, v in enumerate(data))
    fib = fibonacci_confluence(bars, Decimal("98.5"), Decimal("0.5"))
    assert fib["countertrend_fibonacci_confluence"] is True
    assert fib["countertrend_confluence_priority"] == 1
    assert (
        fibonacci_confluence(bars, Decimal("106"), Decimal("0.5"))[
            "countertrend_fibonacci_confluence"
        ]
        is False
    )
    assert (
        fibonacci_confluence(bars[:4], Decimal("98.5"), Decimal("0.5"))[
            "countertrend_fibonacci_confluence"
        ]
        is False
    )


def test_missing_target_and_low_rr_never_become_eligible() -> None:
    c = context()
    m = {v.name: v.value for v in recovery_metrics(c, RecoveryRules(minimum_rr=Decimal("100")))}
    assert m["countertrend_maturity"] == "CT2"
    assert m["countertrend_eligible"] is False
