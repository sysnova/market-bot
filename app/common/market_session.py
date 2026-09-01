"""US-equity session policy shared by analytical and lifecycle engines."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.contracts import BarTimeframe, MarketBar, MarketSession

_NEW_YORK = ZoneInfo("America/New_York")
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_INTRADAY_TIMEFRAMES = {
    BarTimeframe.MINUTE_1,
    BarTimeframe.MINUTE_5,
    BarTimeframe.MINUTE_15,
    BarTimeframe.HOUR_1,
    BarTimeframe.HOUR_4,
}


def market_session(value: datetime) -> MarketSession:
    """Classify a US-equity timestamp using exchange-local wall-clock hours."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market-session timestamp must be timezone-aware")
    local = value.astimezone(_NEW_YORK)
    if local.weekday() >= 5:
        return MarketSession.CLOSED
    if local.time() < _REGULAR_OPEN:
        return MarketSession.PRE_MARKET
    if local.time() < _REGULAR_CLOSE:
        return MarketSession.REGULAR
    return MarketSession.AFTER_HOURS


def is_regular_session(value: datetime) -> bool:
    return market_session(value) is MarketSession.REGULAR


def is_intraday_analysis_session(value: datetime) -> bool:
    """Allow premarket and RTH while keeping after-hours out of Intraday."""

    return market_session(value) in {MarketSession.PRE_MARKET, MarketSession.REGULAR}


def is_regular_session_close_minute(value: datetime) -> bool:
    """Return whether a timestamp belongs to the final minute of US RTH."""

    if not is_regular_session(value):
        return False
    return value.astimezone(_NEW_YORK).time() >= time(15, 59)


def is_regular_analytical_bar(bar: MarketBar) -> bool:
    """Allow structural bars, but require RTH for every intraday timeframe."""

    return not requires_regular_session(bar.timeframe) or is_regular_session(bar.timestamp)


def requires_regular_session(timeframe: BarTimeframe) -> bool:
    """Return whether analytical history for a timeframe must be restricted to RTH."""

    return timeframe in _INTRADAY_TIMEFRAMES


def analytical_storage_limit(timeframe: BarTimeframe, analytical_limit: int) -> int:
    """Overfetch intraday provider bars so RTH filtering preserves requested depth."""

    if analytical_limit < 1:
        raise ValueError("analytical bar limit must be positive")
    return analytical_limit * 3 if timeframe in _INTRADAY_TIMEFRAMES else analytical_limit


def is_completed_daily_bar(bar: MarketBar, *, as_of: datetime) -> bool:
    """Exclude the provider's still-forming current daily aggregate."""

    if bar.timeframe is not BarTimeframe.DAY_1:
        raise ValueError("completed-daily check requires a 1Day bar")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("daily completion boundary must be timezone-aware")
    return bar.timestamp.astimezone(_NEW_YORK).date() < as_of.astimezone(_NEW_YORK).date()
