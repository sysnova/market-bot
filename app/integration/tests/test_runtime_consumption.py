from datetime import UTC, datetime
from unittest.mock import patch

import fakeredis
import pytest

from app.integration.redis_ticker_cache import RedisTickerCache


@pytest.mark.unit
def test_cache_reads_do_not_write_a_lease_for_every_read() -> None:
    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    with patch("app.integration.redis_ticker_cache.time", return_value=1000):
        view = cache.view()
    key = cache.namespace + "owners"
    with patch("app.integration.redis_ticker_cache.time", return_value=1001):
        cache.call("snapshot", view)
        cache.call("snapshot", view)
    assert cache.redis.zscore(key, cache.owner) == 1000
    with patch("app.integration.redis_ticker_cache.time", return_value=1031):
        cache.call("snapshot", view)
    assert cache.redis.zscore(key, cache.owner) == 1031


@pytest.mark.unit
def test_rotation_refresh_is_daily_not_every_five_minutes() -> None:
    from app.integration.market_rotation_composition import RotationRefreshSchedule

    schedule = RotationRefreshSchedule()
    now = datetime(2026, 9, 30, 14, tzinfo=UTC)
    assert schedule.due(now)
    schedule.completed(now)
    assert not schedule.due(now.replace(hour=15))
    assert schedule.due(now.replace(day=1, month=10))
