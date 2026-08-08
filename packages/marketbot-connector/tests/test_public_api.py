from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marketbot_connector import (
    ConnectorConfig,
    ConnectorMessage,
    EventEnvelope,
    resolve_filters,
)


def test_public_api_validates_and_serializes_an_envelope() -> None:
    envelope = EventEnvelope(
        event_type="analysis.result.produced",
        source="intraday",
        subject="AAPL",
        payload={"symbol": "AAPL", "verdict": "WATCH"},
    )
    message = ConnectorMessage(
        nats_subject="marketbot.v1.analysis.result.INTRADAY.AAPL",
        stream_sequence=10,
        consumer_sequence=1,
        delivered_count=1,
        stored_at=datetime(2026, 8, 7, tzinfo=UTC),
        envelope=envelope,
    ).to_jsonable()

    assert message["envelope"]["payload"]["symbol"] == "AAPL"
    assert message["decode_error"] is None


def test_engine_filters_and_backpressure_are_bounded() -> None:
    filters = resolve_filters(engines=("intraday",))

    assert filters.subjects == ("marketbot.v1.analysis.result.INTRADAY.>",)
    with pytest.raises(ValueError, match="at least batch_size"):
        ConnectorConfig(filters=filters, batch_size=100, max_ack_pending=99)
