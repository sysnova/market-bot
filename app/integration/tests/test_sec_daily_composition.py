from datetime import UTC, datetime

from app.integration.sec_daily_composition import _SEC_TIME_ZONE


def test_sec_scan_uses_new_york_filing_date_at_utc_midnight() -> None:
    now = datetime(2026, 7, 28, 0, 15, tzinfo=UTC)

    assert now.astimezone(_SEC_TIME_ZONE).date().isoformat() == "2026-07-27"
