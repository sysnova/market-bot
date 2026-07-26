from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.canonical import sha256_digest
from app.contracts import EventEnvelope, MarketSession, new_uuid7
from app.reference_engine import context_from_event


@pytest.mark.unit
def test_context_is_strict_frozen_and_hashable_from_event_payload() -> None:
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    correlation_id = uuid4()
    event = EventEnvelope(
        event_id=new_uuid7(),
        event_type="synthetic.price",
        occurred_at=occurred_at,
        source="test",
        correlation_id=correlation_id,
        market_session=MarketSession.CONTINUOUS,
        payload={
            "symbol": "TEST",
            "timeframe": "1m",
            "run_id": "run-1",
            "values": {"price": Decimal("12"), "volume": 7},
        },
    )

    context = context_from_event(event)

    assert context.symbol == "TEST"
    assert context.as_of == occurred_at
    assert context.correlation_id == correlation_id
    assert tuple(item.name for item in context.values) == ("price", "volume")
    assert len(sha256_digest(context.model_dump(mode="python"))) == 64
    with pytest.raises(ValidationError):
        context.symbol = "CHANGED"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize("payload", [None, {}, {"symbol": "TEST", "timeframe": "1m"}])
def test_context_rejects_incomplete_synthetic_payload(payload: object) -> None:
    event = EventEnvelope(
        event_id=new_uuid7(),
        event_type="synthetic.price",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="test",
        market_session=MarketSession.CONTINUOUS,
        payload=payload,
    )

    with pytest.raises(ValueError):
        context_from_event(event)
