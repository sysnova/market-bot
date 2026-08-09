from datetime import UTC, datetime

import pytest

from app.contracts import UNIVERSE_CHANGED_EVENT, EventEnvelope, UniverseChanged
from app.integration.universe_events import UniverseEventPublisher

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


@pytest.mark.unit
async def test_universe_publisher_uses_stable_event_id_and_subject() -> None:
    sink = RecordingPublisher()
    publisher = UniverseEventPublisher(sink)
    change = UniverseChanged(
        occurred_at=NOW,
        source="postgresql-local",
        previous_symbols=("AAPL",),
        symbols=("AAPL", "NVDA"),
        added_symbols=("NVDA",),
        removed_symbols=(),
    )

    await publisher.publish_universe_changed(change)

    subject, envelope = sink.events[0]
    assert subject == "marketbot.v1.universe.changed.core"
    assert envelope.event_id == change.change_id
    assert envelope.event_type == UNIVERSE_CHANGED_EVENT
    assert envelope.payload == change
