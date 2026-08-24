"""Wire codec tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import EventEnvelope, MarketQuote, new_uuid7
from app.event_bus.codec import decode_envelope, encode_envelope


@pytest.mark.unit
def test_envelope_round_trip_is_strict(event: EventEnvelope) -> None:
    assert decode_envelope(encode_envelope(event)) == event


@pytest.mark.unit
def test_invalid_wire_envelope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        decode_envelope(b'{"event_type":"missing-required-fields"}')


@pytest.mark.unit
def test_quote_wire_payload_excludes_computed_fields() -> None:
    occurred_at = datetime(2026, 8, 24, 13, 30, tzinfo=UTC)
    quote = MarketQuote(
        event_id=new_uuid7(),
        symbol="AAPL",
        occurred_at=occurred_at,
        received_at=occurred_at,
        bid_price=Decimal("100.10"),
        ask_price=Decimal("100.20"),
        bid_size=Decimal("2"),
        ask_size=Decimal("3"),
    )
    envelope = EventEnvelope(
        event_id=quote.event_id,
        event_type="market.quote.received",
        occurred_at=occurred_at,
        source="alpaca-market-data",
        subject="AAPL",
        payload=quote,
    )

    encoded = encode_envelope(envelope)
    decoded = decode_envelope(encoded)

    assert b'"mid_price"' not in encoded
    assert b'"spread"' not in encoded
    assert MarketQuote.model_validate(decoded.payload, strict=False) == quote
