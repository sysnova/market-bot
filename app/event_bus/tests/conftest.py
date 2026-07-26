"""Shared event bus fixtures."""

from datetime import UTC, datetime

import pytest

from app.contracts import EventEnvelope, new_uuid7


@pytest.fixture
def event() -> EventEnvelope:
    return EventEnvelope(
        event_id=new_uuid7(),
        event_type="market.price.updated",
        occurred_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        source="event-bus-tests",
        subject="AAPL",
        payload={"price": "201.50"},
    )
