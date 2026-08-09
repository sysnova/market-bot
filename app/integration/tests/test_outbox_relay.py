from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import app.integration.outbox_relay as relay_module
from app.contracts import EventEnvelope
from app.integration.outbox_relay import OutboxRelay
from app.persistence.models import OutboxEvent

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)
OUTBOX_ID = UUID("01987e76-3c00-7000-8000-000000000001")


def _event() -> OutboxEvent:
    envelope = EventEnvelope(
        event_type="entry-watch.transitioned",
        occurred_at=NOW,
        source="entry-watcher-v5",
        subject="AAPL",
        payload={"symbol": "AAPL"},
    )
    return OutboxEvent(
        id=OUTBOX_ID,
        aggregate_type="entry-watch",
        aggregate_id="watch-1",
        event_type=envelope.event_type,
        subject="marketbot.v1.entry-watch.transition.ARMED.AAPL",
        payload=envelope.model_dump(mode="json"),
        headers={},
        occurred_at=NOW,
        available_at=NOW,
        attempts=1,
        created_at=NOW,
    )


@pytest.mark.unit
async def test_relay_publishes_outside_database_transaction_and_marks_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event()
    active_transactions = 0
    claim = AsyncMock(return_value=[event])
    mark = AsyncMock()

    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            nonlocal active_transactions
            active_transactions += 1
            self.outbox = SimpleNamespace(claim_pending=claim, mark_published=mark)
            return self

        async def __aexit__(self, *args: object) -> None:
            nonlocal active_transactions
            active_transactions -= 1

    class Publisher:
        async def publish(self, subject: str, envelope: EventEnvelope) -> None:
            assert active_transactions == 0
            assert subject == event.subject
            assert envelope.event_id == EventEnvelope.model_validate(
                event.payload,
                strict=False,
            ).event_id

    monkeypatch.setattr(relay_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())
    relay = OutboxRelay(MagicMock(), Publisher(), clock=lambda: NOW)

    published = await relay.drain_once()

    assert published == 1
    mark.assert_awaited_once_with(OUTBOX_ID, published_at=NOW)


@pytest.mark.unit
async def test_relay_records_bounded_backoff_after_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event()
    claim = AsyncMock(return_value=[event])
    failure = AsyncMock()

    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            self.outbox = SimpleNamespace(claim_pending=claim, record_failure=failure)
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    publisher = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("nats unavailable")))
    monkeypatch.setattr(relay_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())
    relay = OutboxRelay(
        MagicMock(),
        publisher,
        clock=lambda: NOW,
        initial_backoff=timedelta(seconds=2),
        maximum_backoff=timedelta(seconds=30),
    )

    published = await relay.drain_once()

    assert published == 0
    failure.assert_awaited_once_with(
        OUTBOX_ID,
        error="RuntimeError: nats unavailable",
        available_at=NOW + timedelta(seconds=2),
    )
