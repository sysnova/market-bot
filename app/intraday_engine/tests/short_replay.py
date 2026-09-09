"""Offline SIP replay: session isolation and completed five-minute bars only."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.common.market_session import market_session
from app.contracts import BarTimeframe, MarketBar, MarketSession
from app.integration.bar_aggregator import MinuteBarAggregator
from app.intraday_engine.models import IntradayContext


def contexts() -> list[IntradayContext]:
    raw = json.loads((Path(__file__).parent / "fixtures/asts_20260909_short_bars.json").read_text())
    aggregator = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_5,))
    minutes: list[MarketBar] = []
    five: list[MarketBar] = []
    result: list[IntradayContext] = []
    for r in raw["bars"]["ASTS"]:
        timestamp = datetime.fromisoformat(r["t"].replace("Z", "+00:00"))
        if timestamp.date().isoformat() != "2026-09-09":
            continue
        if market_session(timestamp) is not MarketSession.REGULAR:
            continue
        bar = MarketBar(
            symbol="ASTS",
            timeframe=BarTimeframe.MINUTE_1,
            timestamp=timestamp,
            open=Decimal(str(r["o"])),
            high=Decimal(str(r["h"])),
            low=Decimal(str(r["l"])),
            close=Decimal(str(r["c"])),
            volume=Decimal(str(r["v"])),
            vwap=Decimal(str(r["vw"])),
            trade_count=r["n"],
            source="alpaca-rest-replay",
            feed="sip",
        )
        minutes.append(bar)
        five.extend(aggregator.add(bar))
        result.append(
            IntradayContext(
                symbol="ASTS",
                as_of=timestamp,
                minute_bars=tuple(minutes),
                five_minute_bars=tuple(five),
            )
        )
    return result
