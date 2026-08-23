from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from app.alpaca_market_data.microstructure_replay import HistoricalOrderFlowReplay


class FakeRest:
    async def fetch_trades(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]:
        del start, end, limit
        return {
            symbols[0]: [
                {"i": 2, "p": 100.1, "s": 10, "t": "2026-08-21T14:30:00Z"}
            ]
        }

    async def fetch_quotes(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]:
        del start, end, limit
        return {
            symbols[0]: [
                {"bp": 100, "ap": 100.1, "bs": 5, "as": 5, "t": "2026-08-21T14:30:00Z"}
            ]
        }


@pytest.mark.asyncio
async def test_replay_merges_quote_before_trade_at_the_same_exchange_time() -> None:
    start = datetime(2026, 8, 21, 14, tzinfo=UTC)
    replay = HistoricalOrderFlowReplay(
        rest=FakeRest(),
        start=start,
        end=datetime(2026, 8, 21, 15, tzinfo=UTC),
        speed=None,
    )

    events = [event async for event in replay.events(("AAPL",))]

    assert [event["T"] for event in events] == ["q", "t"]
    assert all(event["S"] == "AAPL" for event in events)
    assert events[1]["i"] == 2


def test_replay_requires_utc_aware_causal_boundaries() -> None:
    with pytest.raises(ValueError, match="UTC"):
        HistoricalOrderFlowReplay(
            rest=FakeRest(),
            start=datetime(2026, 8, 21, 14),
            end=datetime(2026, 8, 21, 15),
            speed=None,
        )
