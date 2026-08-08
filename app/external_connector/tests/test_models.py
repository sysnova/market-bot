from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from marketbot_connector import (
    ConnectorConfig,
    ConnectorMessage,
    EventEnvelope,
    parse_start_at,
    resolve_filters,
)


def test_start_at_requires_offset_and_is_normalized_to_utc() -> None:
    parsed = parse_start_at("2026-08-07T09:30:00-03:00")

    assert parsed == datetime(2026, 8, 7, 12, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone offset"):
        parse_start_at("2026-08-07T09:30:00")


def test_connector_config_validates_backpressure_and_durable_name() -> None:
    filters = resolve_filters(engines=("swing",))

    with pytest.raises(ValueError, match="at least batch_size"):
        ConnectorConfig(filters=filters, batch_size=100, max_ack_pending=99)
    with pytest.raises(ValueError, match="forbidden"):
        ConnectorConfig(filters=filters, durable_name="external.bad")


def test_connector_message_serializes_envelopes_and_raw_dlq() -> None:
    envelope = EventEnvelope(event_type="analysis.result.produced", source="swing-v3")
    valid = ConnectorMessage(
        nats_subject="marketbot.v1.analysis.result.SWING.AAPL",
        stream_sequence=10,
        consumer_sequence=2,
        delivered_count=1,
        stored_at=datetime(2026, 8, 7, tzinfo=UTC),
        envelope=envelope,
    ).to_jsonable()
    raw = ConnectorMessage(
        nats_subject="marketbot.dlq",
        stream_sequence=11,
        consumer_sequence=3,
        delivered_count=2,
        stored_at=datetime(2026, 8, 7, tzinfo=UTC),
        envelope=None,
        raw_data=b"not-json",
        decode_error="invalid",
    ).to_jsonable()

    assert valid["envelope"]["source"] == "swing-v3"
    assert valid["redelivered"] is False
    assert raw["raw_base64"] == base64.b64encode(b"not-json").decode("ascii")
    assert raw["redelivered"] is True
