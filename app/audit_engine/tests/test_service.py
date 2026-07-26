from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.audit_engine import AuditService
from app.contracts import EventEnvelope, SubscriptionOptions, new_uuid7


class FakeSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeEventBus:
    def __init__(self, events: list[EventEnvelope]) -> None:
        self._events = events
        self.acknowledged: list[object] = []
        self.subject: str | None = None
        self.options: SubscriptionOptions | None = None

    async def subscribe(
        self,
        subject: str,
        handler: Callable[[EventEnvelope], Awaitable[None]],
        *,
        options: SubscriptionOptions | None = None,
    ) -> FakeSubscription:
        self.subject = subject
        self.options = options
        for event in self._events:
            await handler(event)
            self.acknowledged.append(event.event_id)
        return FakeSubscription()


def audit_event() -> EventEnvelope:
    return EventEnvelope(
        event_id=new_uuid7(),
        event_type="decision.completed",
        occurred_at=datetime(2026, 7, 25, 15, tzinfo=UTC),
        source="reference-engine",
        payload={
            "audit": {"run_id": "run-002", "stream": "decisions"},
            "decision": {"outcome": "accept"},
        },
    )


@pytest.mark.unit
def test_service_consumes_via_port_and_acknowledges_persisted_event(tmp_path: Path) -> None:
    event = audit_event()
    bus = FakeEventBus([event])
    service = AuditService(tmp_path)

    subscription = asyncio.run(service.start(bus))

    assert bus.acknowledged == [event.event_id]
    assert bus.subject == "audit.>"
    assert bus.options is not None
    assert bus.options.durable_name == "audit-engine-v1"
    assert bus.options.replay_all is True
    assert subscription.unsubscribed is False
    service.close()


@pytest.mark.unit
def test_process_returns_duplicate_confirmation(tmp_path: Path) -> None:
    event = audit_event()
    service = AuditService(tmp_path)
    try:
        first = service.process(event)
        duplicate = service.process(event)
    finally:
        service.close()

    assert first.persisted is True
    assert duplicate.persisted is False
    assert duplicate.duplicate is True


@pytest.mark.unit
def test_audit_engine_does_not_import_event_bus_adapter() -> None:
    service_source = (Path(__file__).parents[1] / "service.py").read_text(encoding="utf-8")

    assert "app.event_bus" not in service_source
