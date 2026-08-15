"""Recover Entry Opportunity markouts from the PostgreSQL market-bar cache."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from app.common.market_session import is_regular_session
from app.contracts import (
    BarTimeframe,
    EntryOpportunity,
    MarketBar,
    MarketHistoryRequirement,
)

_MINIMUM_LOOKBACK = timedelta(days=1)
_MAX_RECOVERY_BARS_PER_SYMBOL = 10_000


class EntryOpportunityBarEngine(Protocol):
    async def ingest_bar(self, bar: MarketBar) -> object: ...


def entry_opportunity_history_requirements(
    opportunities: Sequence[EntryOpportunity],
    *,
    as_of: datetime,
) -> tuple[MarketHistoryRequirement, ...]:
    """Request enough 1-minute history to resume the oldest active cursor."""

    if not opportunities:
        return ()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("entry opportunity recovery boundary must be timezone-aware")
    recovery_start = min(
        opportunity.last_market_bar_at or opportunity.armed_at
        for opportunity in opportunities
    )
    if recovery_start > as_of:
        raise ValueError("entry opportunity bar cursor cannot be in the future")
    lookback = max(_MINIMUM_LOOKBACK, as_of - recovery_start + timedelta(minutes=1))
    return (
        MarketHistoryRequirement(
            timeframe=BarTimeframe.MINUTE_1,
            lookback=lookback,
            max_bars_per_symbol=_MAX_RECOVERY_BARS_PER_SYMBOL,
        ),
    )


async def replay_pending_entry_opportunity_bars(
    engine: EntryOpportunityBarEngine,
    opportunities: Sequence[EntryOpportunity],
    bars: Sequence[MarketBar],
) -> int:
    """Replay final regular-session bars strictly after each persisted cursor."""

    active = {opportunity.symbol: opportunity for opportunity in opportunities}
    cursors = {
        opportunity.symbol: opportunity.last_market_bar_at
        for opportunity in opportunities
    }
    replayed = 0
    for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
        opportunity = active.get(bar.symbol)
        if (
            opportunity is None
            or bar.timeframe is not BarTimeframe.MINUTE_1
            or not bar.is_final
            or not is_regular_session(bar.timestamp)
        ):
            continue
        cursor = cursors[bar.symbol]
        if cursor is not None:
            if bar.timestamp <= cursor:
                continue
        elif bar.timestamp < opportunity.armed_at:
            continue
        await engine.ingest_bar(bar)
        cursors[bar.symbol] = bar.timestamp
        replayed += 1
    return replayed
