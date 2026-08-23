from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.contracts import MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT, BarTimeframe, MarketBar
from app.contracts.order_flow import (
    MARKET_TRADE_CANCEL_EVENT,
    MARKET_TRADE_CORRECTION_EVENT,
    MARKET_TRADE_EVENT,
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
)


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
    assert first.envelope.event_type == MARKET_TRADE_EVENT
    assert first.envelope.occurred_at == datetime(2026, 7, 24, 14, 30, 1, 123456, tzinfo=UTC)
    assert first.envelope.subject == "AAPL"
    assert isinstance(first.envelope.payload, MarketTrade)
    assert first.envelope.payload.event_id == first.envelope.event_id
    assert first.envelope.payload.trade_id == "1234"
    assert first.envelope.payload.price == Decimal("224.37")
    assert first.envelope.payload.size == Decimal("100")


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
    assert isinstance(quote.envelope.payload, MarketQuote)
    assert quote.envelope.payload.bid_price == Decimal("710.1")
    assert quote.envelope.payload.ask_price == Decimal("710.3")
    assert bar.subject == "marketbot.v1.market.bar.1Min.AAPL"
    assert isinstance(bar.envelope.payload, MarketBar)
    assert bar.envelope.payload.timeframe is BarTimeframe.MINUTE_1
    assert str(bar.envelope.payload.vwap) == "224.4"


def test_trade_corrections_and_cancels_are_typed_reversible_events() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")
    correction = normalizer.stream_message(
        {
            "T": "c",
            "S": "AAPL",
            "oi": 1001,
            "ci": 1002,
            "cp": 101.25,
            "cs": 50,
            "x": "V",
            "cc": ["@"],
            "t": "2026-07-24T14:30:03Z",
            "z": "C",
        }
    )
    cancel = normalizer.stream_message(
        {
            "T": "x",
            "S": "AAPL",
            "i": 1002,
            "t": "2026-07-24T14:30:04Z",
        }
    )

    assert correction.envelope.event_type == MARKET_TRADE_CORRECTION_EVENT
    assert isinstance(correction.envelope.payload, MarketTradeCorrection)
    assert correction.envelope.payload.original_trade_id == "1001"
    assert correction.envelope.payload.corrected_trade.trade_id == "1002"
    assert cancel.envelope.event_type == MARKET_TRADE_CANCEL_EVENT
    assert isinstance(cancel.envelope.payload, MarketTradeCancel)
    assert cancel.envelope.payload.trade_id == "1002"


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
    assert daily.envelope.payload.is_final is False


def test_zero_activity_bar_treats_zero_vwap_as_unavailable() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")

    publication = normalizer.rest_bar(
        "NBIS",
        "1Week",
        {
            "o": 18.94,
            "h": 18.94,
            "l": 18.94,
            "c": 18.94,
            "v": 0,
            "n": 0,
            "vw": 0,
            "t": "2022-02-28T05:00:00Z",
        },
    )

    assert isinstance(publication.envelope.payload, MarketBar)
    assert publication.envelope.payload.volume == 0
    assert publication.envelope.payload.trade_count == 0
    assert publication.envelope.payload.vwap is None


def test_active_bar_rejects_zero_vwap() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")

    with pytest.raises(ValidationError, match="vwap"):
        normalizer.rest_bar(
            "NBIS",
            "1Week",
            {
                "o": 18.94,
                "h": 18.94,
                "l": 18.94,
                "c": 18.94,
                "v": 100,
                "n": 2,
                "vw": 0,
                "t": "2022-02-28T05:00:00Z",
            },
        )
