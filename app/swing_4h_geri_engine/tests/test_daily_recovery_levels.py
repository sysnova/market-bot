from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar
from app.swing_4h_geri_engine.daily_recovery_levels import average_snapshot, average_state
from app.swing_4h_geri_engine.tests.test_v18 import _bar


def daily_history() -> tuple[MarketBar, ...]:
    return tuple(
        _bar(i, "90", "110", "100").model_copy(
            update={
                "timeframe": BarTimeframe.DAY_1,
                "timestamp": datetime(2026, 1, 1, 5, tzinfo=UTC) + timedelta(days=i),
            }
        )
        for i in range(60)
    )


def test_daily_averages_exclude_unfinished_daily_close() -> None:
    history = daily_history()
    changed = history[-1].model_copy(update={"close": Decimal("1000")})
    at = changed.timestamp + timedelta(hours=10)
    snapshot = average_snapshot((*history[:-1], changed), at)
    assert snapshot is not None
    assert snapshot.ema21 == Decimal("100")
    assert snapshot.sma50 == Decimal("100")
    after = average_snapshot((*history[:-1], changed), at + timedelta(hours=8))
    assert after is not None and after.ema21 > after.sma50 > Decimal("100")


def test_missing_daily_history_is_explicit() -> None:
    assert average_snapshot(daily_history()[:20], datetime(2026, 5, 1, tzinfo=UTC)) is None


def test_cross_requires_acceptance_and_volume_before_support_is_confirmed() -> None:
    start = datetime(2026, 5, 1, 13, 30, tzinfo=UTC)
    bars = tuple(
        _bar(i, low, high, close).model_copy(
            update={
                "timeframe": BarTimeframe.MINUTE_15,
                "timestamp": start + timedelta(minutes=15 * i),
            }
        )
        for i, (low, high, close) in enumerate(
            [
                ("98", "100", "99"),
                ("99", "102", "101"),
                ("100.5", "102", "101.5"),
            ]
        )
    )
    assert average_state(bars[:2], Decimal("100"), Decimal("0.2"), True) == "RECLAIM_PENDING"
    assert average_state(bars, Decimal("100"), Decimal("0.2"), False) == "VOLUME_PENDING"
    assert average_state(bars, Decimal("100"), Decimal("0.2"), True) == "RECLAIM_CONFIRMED"
    retest = bars[-1].model_copy(
        update={
            "timestamp": start + timedelta(minutes=45),
            "low": Decimal("100.1"),
        }
    )
    assert (
        average_state((*bars, retest), Decimal("100"), Decimal("0.2"), True) == "RETEST_CONFIRMED"
    )


def test_being_above_a_mean_alone_is_not_a_confirmed_reclaim() -> None:
    bar = _bar(0, "101", "103", "102")
    assert average_state((bar,), Decimal("100"), Decimal("0.2"), True) == "ABOVE_UNCONFIRMED"
