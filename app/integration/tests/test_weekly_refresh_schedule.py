from datetime import UTC, datetime

import pytest

from app.integration.live_composition import _next_weekly_refresh


@pytest.mark.unit
def test_next_weekly_refresh_is_saturday_after_the_completed_market_week() -> None:
    friday = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    saturday_after_refresh = datetime(2026, 7, 25, 7, 0, tzinfo=UTC)

    assert _next_weekly_refresh(friday) == datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
    assert _next_weekly_refresh(saturday_after_refresh) == datetime(
        2026, 8, 1, 6, 0, tzinfo=UTC
    )
