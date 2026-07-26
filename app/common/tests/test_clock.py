from datetime import UTC, datetime, timedelta

import pytest

from app.common.clock import FrozenClock, SystemClock


def test_frozen_clock_returns_injected_utc_instant() -> None:
    instant = datetime(2026, 7, 25, 18, 30, tzinfo=UTC)

    assert FrozenClock(instant).now() == instant


def test_frozen_clock_can_advance() -> None:
    clock = FrozenClock(datetime(2026, 7, 25, tzinfo=UTC))

    clock.advance(timedelta(seconds=5))

    assert clock.now() == datetime(2026, 7, 25, 0, 0, 5, tzinfo=UTC)


def test_system_clock_returns_timezone_aware_utc() -> None:
    assert SystemClock().now().tzinfo is UTC


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 7, 25))
