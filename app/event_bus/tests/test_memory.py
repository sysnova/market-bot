"""Contract and reliability tests for the in-memory adapter."""

import asyncio

import pytest
from pydantic import ValidationError

from app.contracts import EventEnvelope
from app.event_bus import InMemoryEventBus, SubscriptionOptions


@pytest.mark.unit
async def test_publish_delivers_a_frozen_envelope(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    received: list[EventEnvelope] = []
    await bus.subscribe("prices.updated", lambda item: _append(received, item))

    await bus.publish("prices.updated", event)
    await bus.join()

    assert received == [event]
    with pytest.raises(ValidationError):
        received[0].source = "changed"  # type: ignore[misc]
    await bus.close()


@pytest.mark.unit
async def test_subject_wildcards_follow_nats_semantics(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    one_token: list[EventEnvelope] = []
    tail: list[EventEnvelope] = []
    await bus.subscribe("prices.*", lambda item: _append(one_token, item))
    await bus.subscribe("prices.>", lambda item: _append(tail, item))

    await bus.publish("prices.updated.nasdaq", event)
    await bus.join()

    assert one_token == []
    assert tail == [event]
    await bus.close()


@pytest.mark.unit
async def test_duplicate_event_id_is_delivered_once(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    received: list[EventEnvelope] = []
    await bus.subscribe("prices.updated", lambda item: _append(received, item))

    await bus.publish("prices.updated", event)
    await bus.publish("prices.updated", event)
    await bus.join()

    assert received == [event]
    await bus.close()


@pytest.mark.unit
async def test_publish_captures_payload_snapshot(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    received: list[EventEnvelope] = []
    await bus.subscribe("prices.updated", lambda item: _append(received, item))

    await bus.publish("prices.updated", event)
    event.payload["price"] = "999.99"
    await bus.join()

    assert received[0].payload == {"price": "201.50"}
    await bus.close()


@pytest.mark.unit
async def test_each_consumer_receives_an_independent_payload_copy(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    first_mutated = asyncio.Event()
    second_price: list[str] = []

    async def mutate_payload(item: EventEnvelope) -> None:
        item.payload["price"] = "mutated-by-first"
        first_mutated.set()

    async def observe_payload(item: EventEnvelope) -> None:
        await first_mutated.wait()
        second_price.append(item.payload["price"])

    await bus.subscribe("prices.updated", mutate_payload)
    await bus.subscribe("prices.updated", observe_payload)

    await bus.publish("prices.updated", event)
    await bus.join()

    assert second_price == ["201.50"]
    await bus.close()


@pytest.mark.unit
async def test_handler_failure_is_redelivered_until_ack(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    attempts = 0

    async def flaky(_: EventEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")

    await bus.subscribe(
        "prices.updated",
        flaky,
        options=SubscriptionOptions(max_deliver=3, redelivery_delay_seconds=0),
    )
    await bus.publish("prices.updated", event)
    await bus.join()

    assert attempts == 2
    await bus.close()


@pytest.mark.unit
async def test_replay_all_delivers_history_to_late_subscriber(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    await bus.publish("prices.updated", event)
    await bus.join()
    event.payload["price"] = "changed-after-publish"
    received: list[EventEnvelope] = []

    await bus.subscribe(
        "prices.updated",
        lambda item: _append(received, item),
        options=SubscriptionOptions(replay_all=True),
    )
    await bus.join()

    assert received[0].payload == {"price": "201.50"}
    await bus.close()


@pytest.mark.unit
async def test_unsubscribe_stops_delivery(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(prefix="marketbot")
    received: list[EventEnvelope] = []
    subscription = await bus.subscribe("prices.updated", lambda item: _append(received, item))
    await subscription.unsubscribe()

    await bus.publish("prices.updated", event)
    await bus.join()

    assert received == []
    await bus.close()


@pytest.mark.unit
async def test_ephemeral_mode_does_not_retain_history_or_event_ids(
    event: EventEnvelope,
) -> None:
    bus = InMemoryEventBus(retain_history=False, deduplicate=False)
    early: list[EventEnvelope] = []
    await bus.subscribe("prices.updated", lambda item: _append(early, item))

    await bus.publish("prices.updated", event)
    await bus.publish("prices.updated", event)
    await bus.join()

    late: list[EventEnvelope] = []
    await bus.subscribe(
        "prices.updated",
        lambda item: _append(late, item),
        options=SubscriptionOptions(replay_all=True),
    )
    await bus.join()

    assert early == [event, event]
    assert late == []
    await bus.close()


@pytest.mark.unit
async def test_synchronous_delivery_applies_backpressure(event: EventEnvelope) -> None:
    bus = InMemoryEventBus(synchronous_delivery=True, deduplicate=False)
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked(_: EventEnvelope) -> None:
        started.set()
        await release.wait()

    await bus.subscribe("prices.updated", blocked)
    publication = asyncio.create_task(bus.publish("prices.updated", event))
    await asyncio.wait_for(started.wait(), timeout=1)

    try:
        assert not publication.done()
    finally:
        release.set()
    await asyncio.wait_for(publication, timeout=1)
    await asyncio.wait_for(bus.close(), timeout=1)


async def _append(items: list[EventEnvelope], item: EventEnvelope) -> None:
    items.append(item)
    await asyncio.sleep(0)
