from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest

from app.contracts import EventEnvelope
from app.integration.event_fanout import EventFanoutPublisher


@dataclass
class RecordingPublisher:
    publications: list[tuple[str, EventEnvelope]] = field(default_factory=list)
    error: Exception | None = None

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        if self.error is not None:
            raise self.error
        self.publications.append((subject, envelope))


@pytest.mark.unit
async def test_fanout_publishes_to_local_primary_and_durable_mirror() -> None:
    primary = RecordingPublisher()
    mirror = RecordingPublisher()
    event = EventEnvelope(event_type="market.bar.received", source="test")
    fanout = EventFanoutPublisher(primary=primary, mirrors=(mirror,))

    await fanout.publish("marketbot.v1.market.bar.1Min.AAPL", event)

    assert primary.publications == mirror.publications


@pytest.mark.unit
async def test_fanout_keeps_local_pipeline_alive_when_mirror_is_unavailable() -> None:
    primary = RecordingPublisher()
    mirror = RecordingPublisher(error=ConnectionError("nats unavailable"))
    failures: list[tuple[str, Exception]] = []
    callback: Callable[[str, Exception], Awaitable[None]] = _failure_recorder(failures)
    event = EventEnvelope(event_type="market.bar.received", source="test")
    fanout = EventFanoutPublisher(
        primary=primary,
        mirrors=(mirror,),
        on_mirror_error=callback,
    )

    await fanout.publish("marketbot.v1.market.bar.1Min.AAPL", event)

    assert primary.publications == [("marketbot.v1.market.bar.1Min.AAPL", event)]
    assert failures[0][0] == "marketbot.v1.market.bar.1Min.AAPL"
    assert isinstance(failures[0][1], ConnectionError)


def _failure_recorder(
    failures: list[tuple[str, Exception]],
) -> Callable[[str, Exception], Awaitable[None]]:
    async def record(subject: str, error: Exception) -> None:
        failures.append((subject, error))

    return record
