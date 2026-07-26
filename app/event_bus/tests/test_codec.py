"""Wire codec tests."""

import pytest
from pydantic import ValidationError

from app.contracts import EventEnvelope
from app.event_bus.codec import decode_envelope, encode_envelope


@pytest.mark.unit
def test_envelope_round_trip_is_strict(event: EventEnvelope) -> None:
    assert decode_envelope(encode_envelope(event)) == event


@pytest.mark.unit
def test_invalid_wire_envelope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        decode_envelope(b'{"event_type":"missing-required-fields"}')
