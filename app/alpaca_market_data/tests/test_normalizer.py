from datetime import UTC, datetime

from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.contracts import MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT, BarTimeframe, MarketBar


def test_trade_is_normalized_without_float_values_and_has_stable_identity() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")
    raw = {
        "T": "t",
        "S": "AAPL",
        "i": 1234,
        "x": "V",
        "p": 224.37,
        "s": 100,
        "t": "2026-07-24T14:30:01.123456789Z",
        "c": ["@"],
        "z": "C",
    }

    first = normalizer.stream_message(raw)
    second = normalizer.stream_message(raw)

    assert first.subject == "market.data.trade.aapl"
    assert first.envelope.event_id == second.envelope.event_id
    assert first.envelope.event_id.version == 7
    assert first.envelope.event_type == "market.trade.received"
    assert first.envelope.occurred_at == datetime(
        2026, 7, 24, 14, 30, 1, 123456, tzinfo=UTC
    )
    assert first.envelope.subject == "AAPL"
    assert first.envelope.payload == {
        "conditions": ["@"],
        "exchange": "V",
        "feed": "sip",
        "id": "1234",
        "price": "224.37",
        "provider": "alpaca",
        "size": "100",
        "symbol": "AAPL",
        "tape": "C",
    }


def test_quote_and_bar_are_normalized_to_distinct_subjects() -> None:
    normalizer = AlpacaEventNormalizer(feed="iex")

    quote = normalizer.stream_message(
        {
            "T": "q",
            "S": "BRK.B",
            "bx": "V",
            "bp": 710.1,
            "bs": 2,
            "ax": "K",
            "ap": 710.3,
            "as": 3,
            "t": "2026-07-24T14:30:02Z",
            "c": ["R"],
            "z": "C",
        }
    )
    bar = normalizer.stream_message(
        {
            "T": "b",
            "S": "AAPL",
            "o": 224,
            "h": 225,
            "l": 223.5,
            "c": 224.5,
            "v": 1000,
            "n": 20,
            "vw": 224.4,
            "t": "2026-07-24T14:30:00Z",
        }
    )

    assert quote.subject == "market.data.quote.brk-b"
    assert quote.envelope.payload["bid_price"] == "710.1"
    assert quote.envelope.payload["ask_price"] == "710.3"
    assert bar.subject == "marketbot.v1.market.bar.1Min.AAPL"
    assert isinstance(bar.envelope.payload, MarketBar)
    assert bar.envelope.payload.timeframe is BarTimeframe.MINUTE_1
    assert str(bar.envelope.payload.vwap) == "224.4"


def test_rest_bar_and_snapshot_are_normalized() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")
    bar = normalizer.rest_bar(
        "MSFT",
        "1Day",
        {
            "o": 500,
            "h": 505,
            "l": 495,
            "c": 501,
            "v": 10000,
            "n": 500,
            "vw": 500.5,
            "t": "2026-07-23T04:00:00Z",
        },
    )
    snapshot = normalizer.snapshot(
        "MSFT",
        {
            "latestTrade": {"p": 501.2, "s": 2, "t": "2026-07-24T14:31:00Z"},
            "latestQuote": {"bp": 501.1, "ap": 501.3, "t": "2026-07-24T14:31:00Z"},
            "minuteBar": {"c": 501.2, "t": "2026-07-24T14:30:00Z"},
            "dailyBar": {"c": 501.2, "t": "2026-07-24T04:00:00Z"},
            "prevDailyBar": {"c": 499.0, "t": "2026-07-23T04:00:00Z"},
        },
    )

    assert bar.subject == "marketbot.v1.market.bar.1Day.MSFT"
    assert isinstance(bar.envelope.payload, MarketBar)
    assert str(bar.envelope.payload.close) == "501"
    assert snapshot.subject == "market.data.snapshot.msft"
    assert snapshot.envelope.payload["latest_trade"]["price"] == "501.2"
    assert snapshot.envelope.payload["previous_daily_bar"]["close"] == "499.0"


def test_updated_and_daily_bars_share_the_versioned_bar_subject() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")
    fields = {
        "S": "AAPL",
        "o": 100,
        "h": 101,
        "l": 99,
        "c": 100.5,
        "v": 1000,
        "t": "2026-07-24T14:30:00Z",
    }

    updated = normalizer.stream_message({"T": "u", **fields})
    daily = normalizer.stream_message({"T": "d", **fields})

    assert updated.subject == "marketbot.v1.market.bar.1Min.AAPL"
    assert updated.envelope.event_type == MARKET_BAR_UPDATED_EVENT
    assert isinstance(updated.envelope.payload, MarketBar)
    assert updated.envelope.payload.is_final is False
    assert daily.subject == "marketbot.v1.market.bar.1Day.AAPL"
    assert daily.envelope.event_type == MARKET_BAR_EVENT
    assert isinstance(daily.envelope.payload, MarketBar)
    assert daily.envelope.payload.is_final is True
