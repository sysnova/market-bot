"""Publish completed Core-universe warmups through the stable v1 contract."""

from __future__ import annotations

from app.contracts import (
    UNIVERSE_CHANGED_EVENT,
    EventEnvelope,
    UniverseChanged,
    universe_changed_subject,
)

from .event_fanout import EventPublisher


class UniverseEventPublisher:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def publish_universe_changed(self, change: UniverseChanged) -> None:
        await self._publisher.publish(
            universe_changed_subject(),
            EventEnvelope(
                event_id=change.change_id,
                event_type=UNIVERSE_CHANGED_EVENT,
                occurred_at=change.occurred_at,
                source="market-universe",
                subject=change.universe,
                payload=change,
            ),
        )
