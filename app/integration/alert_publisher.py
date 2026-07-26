"""Adapt local alert dispatch to the repository event publisher."""

from app.contracts import EventEnvelope, LocalAlert

from .event_fanout import EventPublisher


class AlertEventPublisher:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def publish(self, event_type: str, subject: str, alert: LocalAlert) -> None:
        await self._publisher.publish(
            subject,
            EventEnvelope(
                event_type=event_type,
                occurred_at=alert.created_at,
                source="alert-engine",
                subject=alert.symbol,
                payload=alert,
            ),
        )
